"""讨论 (admin only): GPT and Claude discuss a requirement read-only, the owner decides, Cowork takes the brief.

The content of a discussion lives in files (orch/discuss.py: <data>/discussions/DS-0001/discussion.json, under an OS
lock); this module keeps one web row per discussion for the process status (queued / running / pausing / waiting /
paused / interrupted / failed / approved / stopped), starts one worker process per launch (maoweb/discuss_worker.py)
and turns the owner's choices into launches. When the row and the files disagree after a crash, the files win
(_reconcile). Discussions spend the owner's ChatGPT and Max quota, so only the admin may use them, and only one runs
at a time. Nothing is ever written into a project folder.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

from orch import discuss, discuss_call, discuss_record
from orch.proc import kill_tree, run_alive, started_within

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = """CREATE TABLE IF NOT EXISTS discussions (
 did TEXT PRIMARY KEY, user_id INTEGER NOT NULL, project_id INTEGER DEFAULT 0, title TEXT DEFAULT '',
 status TEXT NOT NULL, worker_pid INTEGER DEFAULT 0, server_pid INTEGER DEFAULT 0, log TEXT DEFAULT '',
 error TEXT DEFAULT '', created_at REAL, updated_at REAL);"""
# later columns: ALTER TABLE ... ADD COLUMN (as in jobs.py). ended_at: when the last worker run ended (set only
# there and on a restart), so a phone alert for a failed run keys on that run, not on later owner actions
COLUMNS: dict[str, str] = {"ended_at": "REAL DEFAULT 0"}
ACTIVE = ("queued", "running", "pausing")
FINAL = ("approved", "stopped")
RESUMABLE = ("paused", "interrupted", "failed")
MAX_ACTIVE_TOTAL = 1                  # Cowork shares the Max quota: one discussion at a time
ERRORS = ("", "step_failed", "worker_error", "restarted", "lost")   # row.error is one of these codes
STOP_WAIT = 15.0                      # > run_streaming's 1 s check + GRACE_SECONDS (10 s) for the CLI
SLACK = 2.0                           # seconds: start times read from the system are rounded
VIEW_VERSION = 1
RAW_LIMIT = 20000
MODEL_ACTIONS = ("back_to_gpt", "claude_review", "one_more_round")
OWNER_ONLY = ("edit_requirement", "reject_plan", "approve")
ALLOWED = {
    "queued": ("pause", "stop"), "running": ("pause", "stop"), "pausing": ("stop",),
    "waiting": ("back_to_gpt", "claude_review", "one_more_round", "edit_requirement", "reject_plan", "approve", "stop"),
    "paused": ("resume", "edit_requirement", "stop"), "interrupted": ("resume", "edit_requirement", "stop"),
    "failed": ("resume", "edit_requirement", "stop"),
}
AGENTS = {"gpt": {"pool": "gpt", "name": "GPT", "vendor": "openai", "initials": "G", "color": "openai"},
          "claude": {"pool": "claude", "name": "Claude", "vendor": "anthropic", "initials": "C", "color": "anthropic"}}

# Chinese messages for the web (maoweb/static/i18n.js has their English)
ADMIN_ONLY = "只有管理员可以使用讨论"
NOT_NOW = discuss.NOT_NOW
NO_SUCH = discuss.NO_SUCH
ONE_ACTIVE = "已经有一场讨论在进行，等它停下再开新的"
FOLDER_GONE = "这个项目的文件夹不见了"
NO_EFFORT = "这台电脑上的 claude 不支持推理强度设置"
NO_CLI = {"gpt": "这台电脑上找不到 codex 命令", "claude": "这台电脑上找不到 claude 命令"}
UNKNOWN = "不认识的操作"


def _popen_flags() -> dict:
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {"start_new_session": True}   # its own process group, so kill_tree reaches it and only it


def _ours(pid) -> bool:
    return bool(pid) and pid not in (os.getpid(), os.getppid())


def _is_worker(pid: int, since: float) -> bool:
    """The worker a row recorded still runs (it started before the row was last written). When that cannot be told:
    no; the worker stops by itself once its web server is gone (discuss_worker.watch)."""
    if not _ours(pid) or not started_within(pid, 0, (since or 0) + SLACK):
        return False
    try:   # Linux: the command line says it too
        return b"maoweb.discuss_worker" in Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return True


def _id(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        raise ValueError("没有这个项目") from None


def _part(fn, default):
    """One part of a view: a broken part gives its default, never a 500."""
    try:
        return fn()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return default


class Discussions:
    timeout_s: float = discuss.DEFAULT_TIMEOUT_S        # tests set it lower

    def __init__(self, app):
        self.app = app
        self._guard = threading.RLock()
        self._threads: dict[str, threading.Thread] = {}
        self._processes: dict[str, subprocess.Popen] = {}
        self._stop: dict[str, str] = {}                  # did -> "pause" | "stop" while the owner stops its worker
        self._cli: dict[str, dict[int, float]] = {}      # did -> {CLI pid: when the worker said it started}
        self._seen: dict[str, list[int]] = {}            # did -> every CLI pid its workers started (tests look)
        app.db.db.executescript(SCHEMA)
        cols = {r["name"] for r in app.db.q("PRAGMA table_info(discussions)")}
        for name, decl in COLUMNS.items():
            if name not in cols:
                app.db.x(f"ALTER TABLE discussions ADD COLUMN {name} {decl}")
        self.store = discuss.Store(Path(app.data) / "discussions")    # touches no disk until the first create
        # the fake team for tests only (read once here, handed to every worker in its payload)
        self.team_path = os.environ.get("MAO_DISCUSS_TEAM", "")
        self.team_error = ""
        try:
            self.team = discuss_call.load_team(self.team_path or None)
        except ValueError as e:    # fail closed: never fall back to the real CLIs when the team file is broken
            print(f"MAO_DISCUSS_TEAM: {e}", file=sys.stderr)
            self.team, self.team_error = None, str(e)[:300]
        self._migrate()

    # -- rows ----------------------------------------------------------------------------------------------
    def update(self, did: str, **fields) -> None:
        fields["updated_at"] = time.time()
        self.app.db.x(f"UPDATE discussions SET {', '.join(k + '=?' for k in fields)} WHERE did=?",
                      (*fields.values(), did))

    def watch_rows(self) -> list[dict]:
        """What the phone-alert watcher looks at (orch/watch.py): one small row per discussion."""
        return [dict(r) for r in self.app.db.q("SELECT did, status, error, updated_at, ended_at FROM discussions")]

    def _row(self, did: str) -> dict:
        rows = self.app.db.q("SELECT * FROM discussions WHERE did=?", (str(did),))
        if not rows:
            raise ValueError(NO_SUCH)
        return dict(rows[0])

    def _alive(self, did: str) -> bool:
        thread = self._threads.get(did)
        return thread is not None and thread.is_alive()

    def _active(self, row: dict) -> bool:
        return row["status"] in ACTIVE or self._alive(row["did"])

    def _active_count(self, but: str = "") -> int:
        rows = self.app.db.q(f"SELECT did FROM discussions WHERE status IN {ACTIVE}")
        dids = {r["did"] for r in rows} | {d for d in list(self._threads) if self._alive(d)}
        dids.discard(but)
        return len(dids)

    def waiting_count(self) -> int:
        """Discussions that wait for the owner's decision (the badge on 讨论)."""
        return int(self.app.db.q("SELECT COUNT(*) AS n FROM discussions WHERE status='waiting'")[0]["n"])

    def _migrate(self) -> None:
        """Rows a stopped server left active become interrupted (nothing re-runs by itself)."""
        for r in self.app.db.q(f"SELECT * FROM discussions WHERE status IN {ACTIVE}"):
            r = dict(r)
            if _ours(r["server_pid"]) and started_within(r["server_pid"], 0, (r["updated_at"] or 0) + SLACK):
                continue   # the MAO that started it still runs (a second one on the same data folder): its discussion
            if _is_worker(r["worker_pid"], r["updated_at"]):
                kill_tree(r["worker_pid"])
            self._release(r["did"])
            self.update(r["did"], status="interrupted", error="restarted", ended_at=time.time())
            self._reconcile(r["did"])

    def _reconcile(self, did: str, state: dict | None = None) -> None:
        """The files win: a brief means approved; an empty queue with a stage card (and no group left to finish)
        means it waits for the owner, unless the owner stopped it. Only for rows that are not active."""
        row = self._row(did)
        if self._active(row):
            return
        try:
            st = state if state is not None else self.store.load(did)
        except discuss.DiscussError:
            return
        if st.get("brief"):
            if row["status"] != "approved" or row["error"]:
                self.update(did, status="approved", error="")
        elif (not st.get("queue") and discuss.pending_group(st) is None and st.get("stages")
              and row["status"] != "stopped"):
            if row["status"] != "waiting" or row["error"]:
                self.update(did, status="waiting", error="")

    # -- what the owner can choose -------------------------------------------------------------------------------
    def _caps(self) -> dict:
        if self.team_error:   # a broken test team: report nothing found and probe no real CLI
            return {s: {"found": False, "version": "", "cli": discuss_call.CLIS[s], "no_session_persistence": False,
                        "params": discuss_call._params(s, False, False)} for s in discuss.SIDES}
        return discuss_call.capabilities(self.team)

    def _problem(self, caps: dict) -> str:
        if self.team_error:
            return self.team_error
        for side in discuss.SIDES:
            if not (caps.get(side) or {}).get("found"):
                return NO_CLI[side]
        return ""

    def _defaults(self) -> dict:
        blank = {"model": "", "effort": "", "output_limit_words": discuss.WORDS_DEFAULT}
        fallback = {"first": "gpt", "sides": {s: dict(blank) for s in discuss.SIDES},
                    "controls": dict(discuss.DEFAULT_CONTROLS)}
        last = self.app.db.setting("discuss_last")
        if not isinstance(last, dict):
            return fallback
        try:
            _, first, sides, controls = discuss.validate("x", last.get("first"), last.get("sides"), last.get("controls"))
        except (discuss.DiscussError, AttributeError, TypeError):
            return fallback
        return {"first": first, "sides": sides, "controls": controls}

    def options(self, user) -> dict:
        caps = self._caps()
        problem = self._problem(caps)
        return {"sides": caps, "defaults": self._defaults(),
                "limits": {"requirement": discuss.MAX_REQUIREMENT, "text": discuss.MAX_TEXT,
                           "changes": discuss.MAX_CHANGES, "changes_chars": discuss.MAX_CHANGES_CHARS,
                           "rounds": [discuss.ROUNDS_MIN, discuss.ROUNDS_MAX], "hard_rounds": discuss.HARD_ROUNDS,
                           "hard_calls": discuss.HARD_CALLS, "words": [discuss.WORDS_MIN, discuss.WORDS_MAX]},
                "projects": [{"id": p["id"], "name": p["name"]} for p in self.app.projects.list()],
                "usage_7d": _part(lambda: self.store.usage_since(time.time() - 7 * 86400), {}),
                "ready": not problem, "problem": problem}

    # -- starting one ---------------------------------------------------------------------------------------------
    def create(self, user, body: dict) -> dict:
        by = user["display"] or user["name"]
        project = None
        pid = _id(body.get("project_id"))
        if pid:
            p = self.app.projects.get(pid)
            if not Path(p["path"]).is_dir():
                raise ValueError(FOLDER_GONE)
            project = {"name": p["name"], "path": p["path"]}
        sides = body.get("sides") if isinstance(body.get("sides"), dict) else {}
        controls = body.get("controls") if isinstance(body.get("controls"), dict) else {}
        requirement, first, sides, controls = discuss.validate(body.get("requirement"), str(body.get("first") or ""),
                                                               sides, controls)
        caps = self._caps()
        problem = self._problem(caps)
        if problem:
            raise ValueError(problem)
        for side in discuss.SIDES:
            if sides[side]["effort"] and not caps[side]["params"]["effort"]["applies"]:
                raise ValueError(NO_EFFORT)
        with self._guard:
            if self._active_count() >= MAX_ACTIVE_TOTAL:
                raise ValueError(ONE_ACTIVE)
            carried = discuss_record.carry_over(self.store, project["path"]) if project else None
            did = self.store.create(requirement, first, sides, controls, project=project, by=by, carried=carried)
            self.app.db.set_setting("discuss_last", {"first": first, "sides": sides, "controls": controls})
            now = time.time()
            self.app.db.x("INSERT INTO discussions(did, user_id, project_id, title, status, created_at, updated_at) "
                          "VALUES(?,?,?,?,?,?,?)", (did, user["id"], pid, discuss.title_of(requirement), "queued", now, now))
            self.launch(did)
        return {"did": did}

    # -- the list and one discussion --------------------------------------------------------------------------------
    def list(self, user) -> list[dict]:
        out = []
        for r in self.app.db.q("SELECT did, title, status, error, project_id, updated_at FROM discussions "
                               "ORDER BY created_at DESC LIMIT 200"):
            s = _part(lambda: self.store.summary(r["did"]), {}) if self._exists(r["did"]) else {}
            out.append({"did": r["did"], "title": s.get("title") or r["title"], "status": r["status"],
                        "error": r["error"] or "", "first": s.get("first") or "", "round": s.get("round") or 0,
                        "project": (s.get("project") or {}).get("name") or None, "brief_state": s.get("brief_state") or "",
                        "updated_at": max(float(r["updated_at"] or 0), float(s.get("updated_at") or 0))})
        return out

    def _exists(self, did: str) -> bool:
        try:
            self.store.path(did)
            return True
        except discuss.DiscussError:
            return False

    def _allowed(self, row: dict, state: dict | None) -> list[str]:
        acts = list(ALLOWED.get(row["status"], ()))
        if state is not None and discuss.current_plan(state) is None:
            acts = [a for a in acts if a not in ("approve", "reject_plan")]
        return acts

    def _sig(self, did: str, row: dict) -> str:
        st = (self.store.path(did) / "discussion.json").stat()
        raw = f"{VIEW_VERSION}|{row['status']}|{row['updated_at']}|{st.st_mtime_ns}|{st.st_size}"
        return hashlib.sha1(raw.encode()).hexdigest()[:20]

    def view(self, user, did: str, sig: str = "") -> dict:
        self.store.path(did)                                  # a bad or unknown id: 没有这场讨论
        row = self._row(did)
        if not self._active(row):
            _part(lambda: self._reconcile(did), None)
            row = self._row(did)
        now_sig = self._sig(did, row)
        if sig and sig == now_sig:
            return {"same": True, "sig": now_sig}
        state = self.store.load(did)
        return self._view(row, state, now_sig)

    def _view(self, row: dict, st: dict, sig: str) -> dict:
        did, status = row["did"], row["status"]
        active = self._active(row)
        actions = self._allowed(row, st)
        stages = st.get("stages") or []
        rec, why = _part(lambda: self._recommend(st, actions), (None, ""))
        proj = st.get("project")
        return {
            "sig": sig, "did": did, "title": st.get("title") or row["title"], "status": status,
            "error": row["error"] or "", "can_act": status not in FINAL, "active": active,
            "actions": actions, "recommended": rec, "why": why,
            "first_question": _part(lambda: self._first_question(st), None) if "edit_requirement" in actions else None,
            "project": ({"id": row["project_id"], "name": proj.get("name", "")} if proj else None),
            "requirement": st.get("requirement", ""), "first": st.get("first", "gpt"),
            "sides": st.get("sides") or {}, "controls": st.get("controls") or {},
            "round": st.get("round", 1), "calls": st.get("calls", 0),
            "caps": {"rounds": discuss.HARD_ROUNDS, "calls": discuss.HARD_CALLS},
            "limits": _part(lambda: discuss.limits_left(st), {"changes_left": 0, "rejects_left": 0}),
            "agents": AGENTS,
            "running": ([{"id": r.get("step"), "side": r.get("side"), "step": r.get("kind") or "",
                          "since": r.get("started_at")} for r in st.get("running") or []] if active else []),
            "queue_len": len(st.get("queue") or []),
            "turns": _part(lambda: [self._turn(t) for t in st.get("turns") or []], []),
            "stages": stages, "plan": discuss.latest_plan(st), "plans": st.get("plans") or [],
            "changes": st.get("changes") or [], "rejected": st.get("rejected") or [],
            "carried": st.get("carried"), "events": st.get("events") or [],
            "record": {"sections": _part(lambda: discuss_record.record_sections(st), {})},
            "usage": _part(lambda: self._usage(st), {}),
            "brief": st.get("brief"), "last_error": st.get("last_error"),
            "created_at": st.get("created_at"), "updated_at": st.get("updated_at"),
        }

    @staticmethod
    def _turn(t: dict) -> dict:
        out = {k: v for k, v in t.items() if k != "files"}   # raw text only on demand (/api/discussion/raw)
        out["has_repair"] = bool((t.get("files") or {}).get("repair_raw"))
        return out

    @staticmethod
    def _first_question(st: dict) -> dict | None:
        qs = discuss_record.open_questions(st)
        return {"id": qs[0]["id"], "text": qs[0]["text"], "by": qs[0]["by"]} if qs else None

    @staticmethod
    def _recommend(st: dict, actions: list[str]) -> tuple[str | None, str]:
        """The latest stage's next step, unless the owner changed something since: then another round. Never an
        action the hard caps refuse: at the cap it is approve (with a current plan) or the stage's own step."""
        stages = st.get("stages") or []
        if not stages:
            return None, ""
        last = stages[-1]
        nxt = last.get("next_step") or {}
        action, why = nxt.get("action"), nxt.get("why", "")
        at = float(last.get("at") or 0)
        wants = ""
        if discuss.current_plan(st) is None and (st.get("plans") or []) and \
                any(float(r.get("at") or 0) > at for r in st.get("rejected") or []):
            wants = "rejected"
        else:
            later = [c for c in st.get("changes") or [] if float(c.get("at") or 0) > at]
            if later and not discuss_record.open_questions(st):
                wants = "answered" if all(c["kind"] == "answer" for c in later) else "changed"
        round_ok = discuss.round_allowed(st)
        if wants and round_ok:
            action, why = "one_more_round", wants
        capped = (action == "one_more_round" and not round_ok) or \
            (action in ("back_to_gpt", "claude_review") and not discuss.call_allowed(st))
        if capped or (wants and not round_ok):
            action = "approve" if discuss.current_plan(st) is not None else "edit_requirement"
            why = "cap_reached"
        if action not in actions:
            return None, why
        return action, why

    @staticmethod
    def _usage(st: dict) -> dict:
        out = {}
        for side in discuss.SIDES:
            u = {"calls": 0, "tokens_in": 0, "tokens_out": 0, "tokens_cached": 0, "seconds": 0.0,
                 "est_cost_usd": None, "quota": None}
            if side == "claude":
                u["cost_is_estimate"] = True
            for t in st.get("turns") or []:
                if t.get("side") != side:
                    continue
                tu = t.get("usage") or {}
                for k in ("calls", "tokens_in", "tokens_out", "tokens_cached"):
                    u[k] += int(tu.get(k) or 0)
                u["seconds"] = round(u["seconds"] + float(t.get("seconds") or 0), 2)
                if side == "claude" and tu.get("est_cost_usd"):
                    u["est_cost_usd"] = round((u["est_cost_usd"] or 0.0) + float(tu["est_cost_usd"]), 6)
                if tu.get("quota"):
                    u["quota"] = tu["quota"]
            out[side] = u
        return out

    def raw(self, user, did: str, turn: str, repair: bool = False) -> dict:
        st = self.store.load(did)
        t = next((x for x in st.get("turns") or [] if x["id"] == str(turn or "")), None)
        if t is None:
            raise ValueError(discuss.NO_TURN_FILE)
        name = f"{t['id']}-{t['side']}-{t['step']}{'.repair' if repair else ''}.raw.txt"
        text = self.store.read_turn_file(did, name)
        return {"turn": t["id"], "repair": bool(repair), "text": text[:RAW_LIMIT], "clipped": len(text) > RAW_LIMIT,
                "chars": len(text)}

    # -- the owner's choices ------------------------------------------------------------------------------------------
    def action(self, user, did: str, action: str, text: str = "", kind: str = "", ref: str = "") -> dict:
        by = user["display"] or user["name"]
        action, text = str(action or ""), str(text or "")
        if len(text.strip()) > discuss.MAX_TEXT:
            raise ValueError("写得太长了，最多 4000 字")
        self.store.path(did)
        proc = None
        with self._guard:
            row = self._row(did)
            active = self._active(row)
            if not active:
                self._reconcile(did)
                row = self._row(did)
            state = self.store.load(did)
            if action not in ("pause", "stop", "resume") + discuss.OWNER_ACTIONS:
                raise ValueError(UNKNOWN)
            if action not in self._allowed(row, state):
                raise ValueError(NOT_NOW)
            if action in ("pause", "stop") and active:
                self._event(did, action, by)
                if self._alive(did):
                    self._stop[did] = action
                    self.update(did, status="pausing")
                    proc = self._processes.get(did)
                else:   # a row left active with nothing behind it
                    self.update(did, status="paused" if action == "pause" else "stopped", error="")
            elif action == "stop":
                self._event(did, action, by)
                self.update(did, status="stopped", error="")
            elif action == "resume":
                if self._active_count(but=did) >= MAX_ACTIVE_TOTAL:
                    raise ValueError(ONE_ACTIVE)
                self._event(did, action, by)
                if state.get("queue") or discuss.pending_group(state) is not None:
                    self.launch(did)
                else:
                    self.update(did, status="approved" if state.get("brief") else "waiting", error="")
            else:
                if action in MODEL_ACTIONS and self._active_count(but=did) >= MAX_ACTIVE_TOTAL:
                    raise ValueError(ONE_ACTIVE)
                res = discuss.owner_action(self.store, did, action, text, str(kind or ""), str(ref or ""), by)
                if action == "approve":
                    self.update(did, status="approved", error="")
                elif res.get("launch"):
                    self.launch(did)
                else:
                    self.update(did)
            status = self._row(did)["status"]
        if proc is not None:   # the worker's thread notices the end and settles the status
            threading.Thread(target=self.stop_process, args=(proc,), daemon=True).start()
        return {"ok": True, "status": status}

    def _event(self, did: str, action: str, by: str) -> None:
        try:
            discuss.record_event(self.store, did, action, by=by)
        except discuss.DiscussError as e:   # the record is busy: the choice still counts
            print(f"{did}: event {action} not recorded: {e}", file=sys.stderr)

    # -- the worker -------------------------------------------------------------------------------------------------
    def launch(self, did: str) -> None:
        with self._guard:
            payload = {"root": str(self.store.root), "did": did, "parent_pid": os.getpid(),
                       "timeout_s": float(self.timeout_s), "team": self.team_path}
            self._stop.pop(did, None)
            self.update(did, status="queued", error="", server_pid=os.getpid())
            thread = threading.Thread(target=self._work, args=(did, payload), daemon=True, name=f"discuss-{did}")
            self._threads[did] = thread
            thread.start()

    def _work(self, did: str, payload: dict) -> None:
        proc, final, code, detail = None, None, "", ""
        log = self._row(did)["log"] or ""
        try:
            with self._guard:
                if did in self._stop:
                    return
            env = dict(os.environ, PYTHONIOENCODING="utf-8")
            proc = subprocess.Popen([sys.executable, "-m", "maoweb.discuss_worker"], cwd=ROOT, env=env,
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    text=True, encoding="utf-8", errors="replace", **_popen_flags())
            with self._guard:
                self._processes[did] = proc
                stopping = did in self._stop
                self.update(did, worker_pid=proc.pid, server_pid=os.getpid(), **({} if stopping else {"status": "running"}))
            if stopping:
                self.stop_process(proc)
                return
            proc.stdin.write(json.dumps(payload, ensure_ascii=False))
            proc.stdin.close()
            for line in proc.stdout:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                kind = event.get("event") if isinstance(event, dict) else None
                if kind == "log":
                    log = (log + "\n" + str(event.get("text", "")))[-20000:]
                    self.update(did, log=log)
                elif kind == "step_started":
                    try:
                        with self._guard:
                            cli = int(event.get("pid") or 0)
                            self._cli.setdefault(did, {})[cli] = time.time()
                            self._seen.setdefault(did, []).append(cli)
                    except (TypeError, ValueError):
                        pass
                    self.update(did)
                elif kind in ("turn", "stage"):
                    self.update(did)
                elif kind == "finished":
                    final = str(event.get("status") or "waiting")
                    if final not in ("waiting", "approved"):
                        final = "waiting"
                elif kind == "failed":
                    final = "failed"
                    code = str(event.get("code") or "worker_error")
                    code = code if code in ERRORS and code else "worker_error"
                    detail = str(event.get("detail") or "")[:1000]
            proc.wait(timeout=15)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            final, code, detail = "failed", "worker_error", f"{type(exc).__name__}: {exc}"[:1000]
        finally:
            if proc is not None:
                if proc.poll() is None:
                    self.stop_process(proc)
                for stream in (proc.stdin, proc.stdout):
                    if stream and not stream.closed:
                        try:
                            stream.close()
                        except OSError:
                            pass
            if detail:
                log = (log + "\n" + detail)[-20000:]
            self._ended(did, proc, final, code, log)

    def _ended(self, did: str, proc, final: str | None, code: str, log: str) -> None:
        """After the worker: paused / stopped / interrupted (lost) / failed / waiting / approved; then the files win."""
        with self._guard:
            if proc is not None and self._processes.get(did) is proc:
                self._processes.pop(did, None)
        self._release(did)
        with self._guard:
            if self._threads.get(did) is threading.current_thread():
                self._threads.pop(did, None)
            stop = self._stop.pop(did, None)
            ended = time.time()   # ended_at: written with the final status of this run, in every branch
            if stop == "pause":
                self.update(did, status="paused", error="", log=log, ended_at=ended)
            elif stop == "stop":
                self.update(did, status="stopped", error="", log=log, ended_at=ended)
            elif final is None:   # a restarted server may have said why already (restarted): that stays (one statement)
                self.app.db.x("UPDATE discussions SET error=CASE WHEN status='interrupted' AND error<>'' THEN error "
                              "ELSE 'lost' END, status='interrupted', log=?, updated_at=?, ended_at=? WHERE did=?",
                              (log, ended, ended, did))
            elif final == "failed":
                self.update(did, status="failed", error=code or "worker_error", log=log, ended_at=ended)
            else:
                self.update(did, status=final, error="", log=log, ended_at=ended)
            _part(lambda: self._reconcile(did), None)

    def _release(self, did: str) -> None:
        """A stopped worker leaves no CLI running: kill what the files and the worker's events name, then clear
        `running` in the files (best effort)."""
        with self._guard:
            pids = dict(self._cli.pop(did, {}))
        try:
            state = self.store.load(did)
        except discuss.DiscussError:
            state = {}
        for r in state.get("running") or []:
            if r.get("pid"):
                pids.setdefault(int(r["pid"]), float(r.get("started_at") or 0))
        for pid, at in pids.items():
            if _ours(pid) and run_alive(pid, at):   # not a newer program with that pid
                kill_tree(pid)
        if state.get("running"):
            try:
                self.store.update(did, lambda st: st.__setitem__("running", []), lock_timeout=5.0)
            except discuss.DiscussError as e:
                print(f"{did}: could not clear the running steps: {e}", file=sys.stderr)

    @staticmethod
    def stop_process(proc: subprocess.Popen) -> None:
        if proc.poll() is not None:
            return
        if os.name == "nt":   # the tree: the worker and the CLI it started
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True,
                           timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            proc.terminate()   # the worker sets STOP; run_streaming stops the CLI
        try:
            proc.wait(timeout=STOP_WAIT)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

    def wait(self, did: str, timeout: float = 120) -> None:  # tests
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            thread = self._threads.get(did)
            if thread is None:
                return
            thread.join(0.2)
            if not thread.is_alive() and self._threads.get(did) is thread:
                return
        raise TimeoutError(f"discussion {did} still running")

    def cli_pids(self, did: str) -> list[int]:   # tests: the CLIs the workers said they started
        with self._guard:
            return list(self._seen.get(did, []))


__all__ = ["Discussions", "ACTIVE", "FINAL", "RESUMABLE", "MAX_ACTIVE_TOTAL", "ERRORS", "STOP_WAIT"]
