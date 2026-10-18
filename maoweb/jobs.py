"""Jobs: one piece of work on a registered project, started on the project page or handed over from a chat.

商量 (a planner reads the project and proposes a plan) -> the user approves -> each item is built, checked and reviewed
-> the user merges it (stop mode "each") or orch does (stop mode "plan") -> the next item. orch does the work in its own
process (maoweb/job_worker.py), one launch at a time; this module keeps the row up to date from the worker's JSON lines
and turns the user's decisions into launches. Same table as the old chat hand-off, so old rows keep showing;
maoweb/timeline.py turns a row plus orch's own records into what the pages show. Members see and act on their own
jobs, the admin on all of them.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from pathlib import Path

from orch.context import estimate_tokens
from orch.proc import kill_tree, pid_alive, run_alive, started_within

from . import timeline

ROOT = Path(__file__).resolve().parents[1]
FINAL = ("done", "cancelled")
ACTIVE = ("queued", "planning", "running", "pausing")
TASK_ACTIONS = ("merge", "revise", "discard", "retry", "accept")
FITS = {"merge": ("verified",), "revise": ("verified", "blocked", "failed"), "retry": ("blocked", "failed"),
        "accept": ("blocked", "failed"), "discard": ("queued", "verified", "blocked", "failed")}
DONE_ALREADY = {"merge": "merged", "discard": "cancelled"}   # a second click on the same button changes nothing
SCHEMA = """
CREATE TABLE IF NOT EXISTS dispatch_jobs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, conversation_id INTEGER NOT NULL,
 project TEXT NOT NULL, path TEXT NOT NULL, goal TEXT NOT NULL, context TEXT NOT NULL,
 jobs INTEGER NOT NULL, mode TEXT NOT NULL, status TEXT NOT NULL, plan_id TEXT DEFAULT '',
 plan_json TEXT DEFAULT '{}', task_ids TEXT DEFAULT '[]', routes_json TEXT DEFAULT '{}',
 log TEXT DEFAULT '', error TEXT DEFAULT '', created_at REAL, updated_at REAL
);
CREATE INDEX IF NOT EXISTS dispatch_conv ON dispatch_jobs(conversation_id, user_id);
"""
COLUMNS = {"title": "TEXT DEFAULT ''", "stop_mode": "TEXT DEFAULT 'plan'", "project_id": "INTEGER DEFAULT 0",
           "plans": "TEXT DEFAULT '[]'", "pending": "TEXT DEFAULT '[]'", "events": "TEXT DEFAULT '[]'",
           "with_chat": "INTEGER DEFAULT 0", "worker_pid": "INTEGER DEFAULT 0", "server_pid": "INTEGER DEFAULT 0"}
JSON_LISTS = ("plans", "pending", "events", "task_ids")
LIGHT = "id, user_id, conversation_id, project_id, path, status, title, created_at, updated_at"   # no log / context
NOT_NOW = "当前状态不能执行这个操作"
RESTARTED = "网页服务已重启。请检查任务进度，再决定是否继续；不会自动重复执行。"
LOST = "后台进程意外停止了。请看一下进度，再决定是否继续。"
FAILED = "执行失败"
SLACK = 2.0          # seconds: start times read from the system are rounded


def _loads(text, default):
    try:
        value = json.loads(text or "")
    except ValueError:
        return default
    return value if isinstance(value, type(default)) else default


def _norm(path) -> str:
    try:
        return os.path.normcase(str(Path(path).expanduser().resolve()))
    except (OSError, RuntimeError, ValueError):
        return os.path.normcase(str(path))


def _ro(path, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
    """A read-only look at orch's own records; nothing when they cannot be read."""
    try:
        db = (Path(path) / ".agents" / "state.db").resolve()
        con = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=5)
    except (OSError, ValueError, sqlite3.Error):
        return []
    try:
        con.row_factory = sqlite3.Row
        return con.execute(sql, args).fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()


def _int(value, default: int, wrong: str) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        raise ValueError(wrong) from None


def make_title(goal: str) -> str:
    """First non-empty line of the goal without Markdown marks, up to the first 。, at most 60 characters."""
    line = next((ln for ln in (goal or "").splitlines() if ln.strip()), "")
    line = re.sub(r"^\s*(?:#+|>|[-*+]|\d+[.)])\s+", "", line)
    line = re.sub(r"\s+", " ", re.sub(r"[*`~]+", "", line)).strip()   # keep _: file names have it
    return (line.split("。")[0].strip() or line)[:60]


def first_sentence(text: str) -> str:
    text = re.sub(r"\s+", " ", re.sub(r"[*`~#>]+", "", text or "")).strip()
    head = re.split(r"(?<=[。！？])|(?<=[.!?])(?=\s|$)", text, maxsplit=1)[0]
    return head.strip().rstrip("。！？.!?").strip()[:60]


def plan_title(goal: str, summary: str, from_chat: bool = False) -> str:
    """A better title from the plan: the plan summary up to its first "：" or "。", if that still says something
    (6+ characters). A goal handed over from a chat is often just the last thing said there, so it always takes
    the plan's; a goal the user typed only when its first line is too short to say much (under 10 characters)."""
    if not from_chat and len(make_title(goal)) >= 10:
        return ""
    head = re.split(r"[：。]", first_sentence(summary), maxsplit=1)[0].strip()
    return head if len(head) >= 6 else ""


def folder_gone(path) -> bool:
    """The project folder (or orch's records in it) is no longer there: nothing of the job can be worked on."""
    try:
        return not (Path(path) / ".agents").is_dir()
    except (OSError, ValueError):
        return True


def _popen_flags() -> dict:
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {"start_new_session": True}   # its own process group, so kill_tree reaches it and only it


def _ours(pid: int) -> bool:
    return bool(pid) and pid not in (os.getpid(), os.getppid())


def _is_worker(pid: int, since: float) -> bool:
    """The worker a row recorded still runs: it started before the row was last written (a pid handed out again
    belongs to a program started later, after a reboot too). When that cannot be told: no, the worker stops by
    itself once its web server is gone (job_worker.watch), so leaving it is safe and killing a stranger is not."""
    if not _ours(pid) or not started_within(pid, 0, (since or 0) + SLACK):
        return False
    try:   # Linux: the command line says it too
        return b"maoweb.job_worker" in Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return True


class Jobs:
    def __init__(self, app):
        self.app = app
        self._guard = threading.RLock()
        self._threads: dict[int, threading.Thread] = {}
        self._processes: dict[int, subprocess.Popen] = {}
        self._stop: dict[int, str] = {}      # jid -> "pause" | "cancel" while the user stops its worker
        app.db.db.executescript(SCHEMA)
        cols = {r["name"] for r in app.db.q("PRAGMA table_info(dispatch_jobs)")}
        for name, decl in COLUMNS.items():
            if name not in cols:
                app.db.x(f"ALTER TABLE dispatch_jobs ADD COLUMN {name} {decl}")
        self._migrate()

    # -- rows ----------------------------------------------------------------------------
    def _migrate(self) -> None:
        """Old chat hand-off rows get the new fields; rows a stopped server left running become interrupted."""
        q, x, now = self.app.db.q, self.app.db.x, time.time()
        projects = {_norm(p["path"]): p["id"] for p in self._projects()}
        for r in q("SELECT id, plan_id, plans, path, project_id, title, goal FROM dispatch_jobs"):
            fields = {}
            if r["plans"] in ("", "[]", None) and r["plan_id"]:
                fields["plans"] = json.dumps([r["plan_id"]])
            if not r["title"]:
                fields["title"] = make_title(r["goal"])
            if not r["project_id"] and projects.get(_norm(r["path"])):
                fields["project_id"] = projects[_norm(r["path"])]
            if fields:
                x(f"UPDATE dispatch_jobs SET {', '.join(k + '=?' for k in fields)} WHERE id=?",
                  (*fields.values(), r["id"]))
        x("UPDATE dispatch_jobs SET status='plan_ready' WHERE status='draft'")
        for r in q(f"SELECT id, path, worker_pid, server_pid, updated_at FROM dispatch_jobs WHERE status IN {ACTIVE}"):
            if _ours(r["server_pid"]) and started_within(r["server_pid"], 0, (r["updated_at"] or 0) + SLACK):
                continue   # the Crossview that started it still runs (a second one on the same data folder): its job
            if _is_worker(r["worker_pid"], r["updated_at"]):
                kill_tree(r["worker_pid"])
            self._release(r["path"], r["worker_pid"])
            x("UPDATE dispatch_jobs SET status='interrupted', error=?, updated_at=? WHERE id=?",
              (RESTARTED, now, r["id"]))
        for r in q("SELECT id, pending, events FROM dispatch_jobs "
                   "WHERE status='interrupted' AND pending NOT IN ('', '[]')"):
            dropped = _loads(r["pending"], [])
            note = {"at": now, "action": "dropped", "task_id": "", "by": "MAO", "items": dropped,
                    "text": "、".join(f"{p.get('action', '')} {p.get('task_id', '')}" for p in dropped)}
            x("UPDATE dispatch_jobs SET pending='[]', events=? WHERE id=?",
              (json.dumps(_loads(r["events"], []) + [note], ensure_ascii=False), r["id"]))

    def update(self, jid: int, **fields) -> None:
        fields["updated_at"] = time.time()
        self.app.db.x(f"UPDATE dispatch_jobs SET {', '.join(k + '=?' for k in fields)} WHERE id=?",
                      (*fields.values(), jid))

    def _get(self, jid: int) -> dict:
        rows = self.app.db.q("SELECT * FROM dispatch_jobs WHERE id=?", (jid,))
        if not rows:
            raise ValueError("没有这个任务")
        return dict(rows[0])

    def _row(self, jid: int, user) -> dict:
        row = self._get(jid)
        if not self._sees(user, row):
            raise ValueError("没有这个任务")
        return row

    @staticmethod
    def _sees(user, row) -> bool:
        return bool(user["is_admin"]) or row["user_id"] == user["id"]

    def _rows(self, where: str = "", args: tuple = (), cols: str = "*") -> list[dict]:
        sql = f"SELECT {cols} FROM dispatch_jobs" + (f" WHERE {where}" if where else "") + " ORDER BY id DESC"
        return [dict(r) for r in self.app.db.q(sql, args)]

    def visible(self, user, project_id: int | None = None) -> list[dict]:
        """Rows this user may see, newest first: members their own, the admin every one."""
        return [r for r in self._rows("project_id=?" if project_id is not None else "",
                                      (project_id,) if project_id is not None else ())
                if self._sees(user, r)]

    def _projects(self) -> list[dict]:
        projects = getattr(self.app, "projects", None)
        return projects.list() if projects is not None else []

    def _registered(self, row) -> bool:
        return bool(row["project_id"]) and any(p["id"] == row["project_id"] for p in self._projects())

    def _names(self) -> dict[int, str]:
        return {u["id"]: u["display"] or u["name"] for u in self.app.db.users()}

    def _alive(self, jid: int) -> bool:
        thread = self._threads.get(jid)
        return thread is not None and thread.is_alive()

    def is_active(self, row) -> bool:
        return row["status"] in ACTIVE or self._alive(row["id"])

    def public(self, row, user, names: dict | None = None) -> dict:
        """The row as timeline.py takes it: JSON columns parsed, plus who owns it and what this user may do."""
        job = dict(row)
        for key in JSON_LISTS:
            job[key] = _loads(job.get(key), [])
        job["plan_json"] = _loads(job.get("plan_json"), {})
        job["owner"] = (names if names is not None else self._names()).get(row["user_id"], "")
        job["mine"] = row["user_id"] == user["id"]
        job["can_act"] = self._registered(row) and self._sees(user, row)
        job["active"] = self.is_active(row)
        # 不做了 is on its way: the worker is stopped first, then a cancel launch drops the items
        job["cancelling"] = job["active"] and (self._stop.get(row["id"]) == "cancel" or job.get("mode") == "cancel")
        return job

    def running(self, path) -> bool:
        """A job of this project folder is at work or about to start (its worker may change orch's branches)."""
        key = _norm(path)
        with self._guard:
            alive = [jid for jid, t in self._threads.items() if t.is_alive()]
            rows = self._rows(f"status IN {ACTIVE}" + (f" OR id IN ({','.join(map(str, alive))})" if alive else ""),
                              cols=LIGHT)
        return any(_norm(r["path"]) == key for r in rows)

    def active_count(self) -> int:
        """Jobs at work or about to start (the 旧功能 tab warns before the old dispatch is switched off)."""
        return int(self.app.db.q(f"SELECT COUNT(*) AS n FROM dispatch_jobs WHERE status IN {ACTIVE}")[0]["n"])

    def has_open(self, pid: int) -> bool:
        """A job of this project is not finished (the project cannot be removed then)."""
        return bool(self._open_rows(pid))

    def _open_rows(self, pid: int) -> list[dict]:
        path = next((_norm(p["path"]) for p in self._projects() if p["id"] == pid), None)
        return [r for r in self._rows(cols=LIGHT)
                if r["status"] not in FINAL and (r["project_id"] == pid or (path and _norm(r["path"]) == path))]

    def end_missing(self, pid: int) -> None:
        """Jobs of a project whose folder is gone end as cancelled (removing the project from the list does
        this first): there is nothing left to stop or clean up, and they would keep the project from going."""
        with self._guard:
            for r in self._open_rows(pid):
                if not self._alive(r["id"]) and folder_gone(r["path"]):
                    self._end_gone(self._get(r["id"]))

    def _end_gone(self, row, event: dict | None = None) -> None:
        note = {"at": time.time(), "action": "note", "code": "folder_gone", "task_id": "", "by": "MAO",
                "text": str(row["path"])[:300]}
        events = _loads(row["events"], []) + ([event] if event else []) + [note]
        self.update(row["id"], status="cancelled", pending="[]", error="",
                    events=json.dumps(events, ensure_ascii=False))

    def adopt(self, project: dict) -> None:
        """A project added again (same folder) takes back its old jobs."""
        key = _norm(project["path"])
        for r in self.app.db.q("SELECT id, path FROM dispatch_jobs"):
            if _norm(r["path"]) == key:
                self.app.db.x("UPDATE dispatch_jobs SET project_id=? WHERE id=?", (project["id"], r["id"]))

    # -- starting a job ---------------------------------------------------------------------
    def context(self, cid: int) -> str:
        kept, budget = [], 12000
        for msg in reversed(self.app.db.messages(cid)):
            content = msg["content"]
            cost = estimate_tokens(content)
            if cost > budget:
                break
            budget -= cost
            kept.append({"role": msg["role"], "content": content})
        return "网页讨论资料（仅作为需求参考）：\n" + json.dumps(list(reversed(kept)), ensure_ascii=False)

    def submit(self, user, body: dict) -> int:
        project = self.app.projects.get(body.get("project_id"))
        goal = str(body.get("goal") or "").strip()
        if not goal:
            raise ValueError("请写清楚要做什么")
        if len(goal) > 8000:
            raise ValueError("要做的事写得太长了，最多 8000 字")
        stop_mode = str(body.get("stop_mode") or "each")
        jobs = _int(body.get("jobs"), 2, "同时做的项数必须是 1 到 4")
        if stop_mode not in ("each", "plan"):
            raise ValueError("不认识的操作")
        if jobs not in range(1, 5):
            raise ValueError("同时做的项数必须是 1 到 4")
        cid = _int(body.get("conversation_id"), 0, "没有这个对话")
        if cid and not self.app.db.conversation(user["id"], cid):
            raise ValueError("没有这个对话")
        with_chat = bool(cid) and bool(body.get("with_chat", True))
        state = self.app.projects.state(project["path"])
        if not state.get("ready"):
            raise ValueError(state.get("problem") or "这个项目现在用不了")
        context = self.context(cid) if with_chat else ""
        with self._guard:
            key = _norm(project["path"])
            for r in self._rows("status NOT IN ('done', 'cancelled')", cols=LIGHT):
                if _norm(r["path"]) == key:
                    if r["user_id"] == user["id"]:
                        raise ValueError(f"这个项目还有一件没做完：「{r['title']}」。先把它做完或点「不做了」。")
                    raise ValueError(f"{self._names().get(r['user_id'], '')} 正在用这个项目，等它做完再交代新任务")
            now = time.time()
            jid = self.app.db.x(
                "INSERT INTO dispatch_jobs(user_id, conversation_id, project, path, goal, context, jobs, mode, status, "
                "title, stop_mode, project_id, with_chat, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (user["id"], cid, project["name"], project["path"], goal, context, jobs, "plan", "queued",
                 make_title(goal), stop_mode, project["id"], int(with_chat), now, now))
            self.launch(jid, "plan")
            return jid

    # -- the user's decisions --------------------------------------------------------------
    def action(self, user, jid: int, action: str, task_id: str = "", text: str = "") -> dict:
        text, task_id, proc = str(text or "").strip(), str(task_id or ""), None
        if len(text) > 4000:
            raise ValueError("写得太长了，最多 4000 字")
        with self._guard:
            row = self._row(jid, user)
            if not self._registered(row) and action != "cancel":   # 不做了 still ends it (and its badge)
                raise ValueError("这个项目已经从列表里移除了")
            status, active, ids = row["status"], self.is_active(row), _loads(row["task_ids"], [])
            event = {"at": time.time(), "action": action, "task_id": task_id if action in TASK_ACTIONS else "",
                     "text": text, "by": user["display"] or user["name"]}
            if action in TASK_ACTIONS:
                return self._task_action(row, event, active)
            if action == "stop_mode":
                if text not in ("each", "plan"):
                    raise ValueError("不认识的操作")
                self._note(row, event, stop_mode=text)
            elif action == "pause":
                if not active:
                    raise ValueError(NOT_NOW)
                self._stop[jid] = "pause"
                self._note(row, event, status="pausing" if self._alive(jid) else "paused")
                proc = self._processes.get(jid)
            elif action == "cancel":
                if status in FINAL:
                    raise ValueError(NOT_NOW)
                if self._alive(jid):
                    self._stop[jid] = "cancel"
                    self._note(row, event, status="pausing", pending="[]")
                    proc = self._processes.get(jid)
                elif folder_gone(row["path"]):   # moved or deleted: nothing to stop or clean up there
                    self._end_gone(row, event)
                else:
                    self._note(row, event, pending="[]")
                    self.launch(jid, "cancel")
            elif action in ("approve", "replan", "resume"):
                if active:
                    raise ValueError(NOT_NOW)
                if action == "approve":
                    if status != "plan_ready":
                        raise ValueError(NOT_NOW)
                    self._note(row, event)
                    self.launch(jid, "run")
                elif action == "replan":
                    if not (status == "plan_ready" or (status in ("paused", "interrupted", "failed") and not ids)):
                        raise ValueError(NOT_NOW)
                    if status == "plan_ready" and not text:
                        raise ValueError("请写下要怎么改")
                    self._note(row, event)
                    self.launch(jid, "plan", feedback=text, replan=True)
                else:
                    if status not in ("paused", "interrupted", "needs_attention", "failed"):
                        raise ValueError(NOT_NOW)
                    self._note(row, event)
                    plans = _loads(row["plans"], []) or [row["plan_id"]]
                    plan = (_ro(row["path"], "SELECT status FROM plans WHERE id=?", (plans[-1],)) or [None])[0]
                    if ids or (plan and plan["status"] == "approved"):
                        self.launch(jid, "run")
                    elif plan and plan["status"] == "draft":
                        self.update(jid, status="plan_ready", error="")
                    else:   # planning again; a new round the user asked for keeps their words
                        ask = self._open_replan(row)
                        self.launch(jid, "plan", feedback=ask["text"] if ask else "", replan=ask is not None)
            else:
                raise ValueError("不认识的操作")
        if proc is not None:   # the worker's thread notices the end and settles the status
            threading.Thread(target=self.stop_process, args=(proc,), daemon=True).start()
        return {"ok": True, "queued": False}

    @staticmethod
    def _open_replan(row) -> dict | None:
        """The user's 我要改 whose new plan never arrived (stopped or failed on the way), else None."""
        events = _loads(row["events"], [])
        asks = [i for i, e in enumerate(events) if e.get("action") == "replan"]
        if not asks or any(e.get("action") == "approve" for e in events[asks[-1]:]):
            return None
        return events[asks[-1]]

    def _task_action(self, row, event, active) -> dict:
        """merge / revise / discard / retry / accept: always through `pending` (one entry per task)."""
        jid, action, tid = row["id"], event["action"], event["task_id"]
        if row["status"] in FINAL or self._stop.get(jid) == "cancel" or (active and row["mode"] == "cancel"):
            raise ValueError(NOT_NOW)
        if tid not in _loads(row["task_ids"], []):
            raise ValueError("这一项不属于这个任务")
        found = _ro(row["path"], "SELECT status FROM tasks WHERE id=?", (tid,))
        status = found[0]["status"] if found else ""
        if DONE_ALREADY.get(action) == status:
            return {"ok": True, "queued": False}
        if status not in FITS[action]:
            raise ValueError("这一项现在不能这样操作")
        text = event["text"]
        if action == "revise" and not text:
            text = timeline.revise_text_for_merge(row["path"], tid)   # 让它修一下 after a failed merge
            if not text:
                raise ValueError("请写下要怎么改")
        pending = _loads(row["pending"], [])
        same = next((p for p in pending if p.get("task_id") == tid), None)
        if same and same.get("action") == action and same.get("text") == text:
            return {"ok": True, "queued": True}
        entry = {"action": action, "task_id": tid, "text": text, "at": event["at"], "by": event["by"]}
        pending = [p for p in pending if p.get("task_id") != tid] + [entry]
        self._note(row, event, pending=json.dumps(pending, ensure_ascii=False))
        if active:
            return {"ok": True, "queued": True}
        self.launch(jid, "run")
        return {"ok": True, "queued": False}

    def _note(self, row, event, **fields) -> None:
        """Record the user's decision on the row (plus any field changes)."""
        events = _loads(row["events"], []) + [event]
        self.update(row["id"], events=json.dumps(events, ensure_ascii=False), **fields)

    # -- the worker -------------------------------------------------------------------------
    def launch(self, jid: int, mode: str, feedback: str = "", replan: bool = False) -> None:
        """Start a worker for this job; in mode "run" the pending decisions go along."""
        with self._guard:
            row = self._get(jid)
            actions = [{k: p.get(k, "") for k in ("action", "task_id", "text")} for p in _loads(row["pending"], [])]
            payload = {"path": row["path"], "goal": row["goal"], "context": row["context"], "jobs": row["jobs"],
                       "stop_mode": row["stop_mode"], "plan_id": row["plan_id"], "plans": _loads(row["plans"], []),
                       "task_ids": _loads(row["task_ids"], []), "mode": mode, "feedback": feedback,
                       "previous_plan": _loads(row["plan_json"], {}) if replan else {},
                       "actions": actions if mode == "run" else [], "parent_pid": os.getpid()}
            self._stop.pop(jid, None)
            self.update(jid, status="queued", mode=mode, error="", server_pid=os.getpid())
            thread = threading.Thread(target=self._work, args=(jid, payload), daemon=True, name=f"job-{jid}")
            self._threads[jid] = thread
            thread.start()

    @contextmanager
    def _slot(self, lock: threading.Lock, jid: int):
        """One worker per project at a time; give up waiting when the user stops the job."""
        while not lock.acquire(timeout=0.2):
            if jid in self._stop:
                yield False
                return
        try:
            yield jid not in self._stop
        finally:
            lock.release()

    def _work(self, jid: int, payload: dict) -> None:
        proc, final, error = None, None, ""
        row = self._get(jid)
        log = row["log"] or ""
        try:
            with self._slot(self.app.projects.lock_for(row["path"]), jid) as ready:
                with self._guard:
                    if not ready or jid in self._stop:
                        return
                    self.update(jid, status="planning" if payload["mode"] == "plan" else "running")
                env = dict(os.environ, PYTHONIOENCODING="utf-8")
                proc = subprocess.Popen([sys.executable, "-m", "maoweb.job_worker"], cwd=ROOT, env=env,
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        text=True, encoding="utf-8", errors="replace", **_popen_flags())
                with self._guard:
                    self._processes[jid] = proc
                    self.update(jid, worker_pid=proc.pid)
                    stopping = jid in self._stop
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
                        self.update(jid, log=log)
                    elif kind == "finished":
                        final = str(event.get("status") or "needs_attention")
                    elif kind == "failed":
                        final, error = "failed", str(event.get("error") or FAILED)[:1000]
                    elif kind:
                        self._on_event(jid, kind, event)
                proc.wait(timeout=15)
        except Exception as exc:
            traceback.print_exc()
            final, error = "failed", f"{type(exc).__name__}: {exc}"[:1000]
        finally:
            if proc is not None:
                if proc.poll() is None:
                    self.stop_process(proc)
                for stream in (proc.stdin, proc.stdout):
                    if stream and not stream.closed:
                        stream.close()
            self._ended(jid, payload, proc, final, error)

    def _on_event(self, jid: int, kind: str, event: dict) -> None:
        with self._guard:
            row = self._get(jid)
            if kind in ("planning", "plan"):
                pid, plans = str(event.get("plan_id") or ""), _loads(row["plans"], [])
                fields = {"plan_id": pid, "plans": json.dumps(plans + ([pid] if pid and pid not in plans else []))}
                if kind == "plan":
                    plan = event.get("plan") if isinstance(event.get("plan"), dict) else {}
                    fields["plan_json"] = json.dumps(plan, ensure_ascii=False)
                    better = plan_title(row["goal"], str(plan.get("summary") or ""), bool(row["with_chat"]))
                    if better:
                        fields["title"] = better
                self.update(jid, **fields)
            elif kind == "tasks":
                self.update(jid, task_ids=json.dumps([str(t) for t in event.get("task_ids") or []]))
            elif kind == "note" and event.get("code") != "merge_failed":   # orch's own log has merge_failed
                note = {"at": time.time(), "action": "note", "code": str(event.get("code") or ""),
                        "task_id": str(event.get("task_id") or ""), "text": str(event.get("text") or "")[:300],
                        "by": "MAO"}
                self.update(jid, events=json.dumps(_loads(row["events"], []) + [note], ensure_ascii=False))
            elif kind == "applied":
                tid, action, text = event.get("task_id"), event.get("action"), event.get("text") or ""
                pending = [p for p in _loads(row["pending"], []) if not (
                    p.get("task_id") == tid and p.get("action") == action and (p.get("text") or "") == text)]
                self.update(jid, pending=json.dumps(pending, ensure_ascii=False))

    def _ended(self, jid: int, payload: dict, proc, final: str | None, error: str) -> None:
        """After the worker: paused / cancel next / interrupted / failed / relaunch with new decisions / final."""
        with self._guard:
            if proc is not None and self._processes.get(jid) is proc:
                self._processes.pop(jid, None)
            stopped = jid in self._stop
        if stopped and proc is not None:
            self._release(payload["path"], proc.pid)
        landing = getattr(self.app, "landing", None)
        if landing is not None:   # what the button would bring in may have changed: look again on the next poll
            landing.forget(payload["path"])
        with self._guard:
            if self._threads.get(jid) is threading.current_thread():
                self._threads.pop(jid, None)
            stop = self._stop.pop(jid, None)
            if stop == "pause":
                self.update(jid, status="paused")
            elif stop == "cancel":
                self.launch(jid, "cancel")
            elif final is None:
                self.update(jid, status="interrupted", error=LOST)
            elif final == "failed" and payload["mode"] == "cancel" and folder_gone(payload["path"]):
                self._end_gone(self._get(jid))   # the folder went away meanwhile: nothing is left to clean up
            elif final == "failed":
                self.update(jid, status="failed", error=error)
            elif payload["mode"] == "run" and _loads(self._get(jid)["pending"], []):
                self.launch(jid, "run")
            else:
                self.update(jid, status=final)

    def _release(self, path: str, worker_pid: int) -> None:
        """A stopped worker leaves no agent running and no lock behind (unless another orch owns the project)."""
        lock = Path(path) / ".agents" / "orch.lock"
        try:
            holder = int(lock.read_text().strip() or 0)
        except (OSError, ValueError):
            holder = 0
        if holder and holder != worker_pid and pid_alive(holder):
            return   # an orch started elsewhere (the command line) runs this project: its agents are not ours
        for r in _ro(path, "SELECT pid, started_at FROM runs WHERE status='running' AND pid IS NOT NULL"):
            if _ours(r["pid"]) and run_alive(r["pid"], r["started_at"]):   # not a newer program with that pid
                kill_tree(int(r["pid"]))
        if holder and holder == worker_pid:
            try:
                lock.unlink()
            except OSError:
                pass

    @staticmethod
    def stop_process(proc: subprocess.Popen) -> None:
        if proc.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True,
                           timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

    def wait(self, jid: int, timeout: float = 120) -> None:  # tests
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            thread = self._threads.get(jid)
            if thread is None:
                return
            thread.join(0.2)
            if not thread.is_alive() and self._threads.get(jid) is thread:
                return
        raise TimeoutError(f"job {jid} still running")

    # -- what the pages show (built by timeline.py) -------------------------------------------
    def job(self, user, jid: int) -> dict:
        return self.public(self._row(jid, user), user)

    def view(self, user, jid: int, sig: str = "") -> dict:
        job = self.job(user, jid)
        # a finished job: is its merged work in the user's own folder yet, and which landing brought it there
        job["land"] = self._land(job["project_id"], user, job["updated_at"] or 0) if job["status"] in FINAL else None
        return timeline.view(job, sig)

    def _land(self, pid: int, user, since: float | None = None) -> dict | None:
        """The landing state of a project (maoweb/landing.py; cached, cheap on every poll)."""
        landing = getattr(self.app, "landing", None)
        project = next((p for p in self._projects() if p["id"] == pid), None)
        if landing is None or project is None:
            return None
        try:
            return landing.state(project, since, user)
        except Exception:   # the page shows without it
            traceback.print_exc()
            return None

    def run_detail(self, user, jid: int, run_id: str) -> dict:
        return timeline.run_detail(self.job(user, jid), run_id)

    def diff(self, user, jid: int, task_id: str) -> dict:
        return timeline.diff(self.job(user, jid), task_id)

    def for_conversation(self, user, cid: int) -> list[dict]:
        names = self._names()
        return [timeline.brief(self.public(r, user, names))
                for r in self._rows("user_id=? AND conversation_id=?", (user["id"], cid))]

    def waiting_count(self, user) -> int:
        """The user's own jobs that wait for a decision (the badge on 项目)."""
        names, n = self._names(), 0
        for r in self._rows("user_id=? AND status NOT IN ('done', 'cancelled')", (user["id"],)):
            if not self._registered(r):
                continue   # its project left the list: the job is read-only, nothing to decide there
            try:   # /api/state starts the app: a broken job must not stop it
                n += bool(timeline.brief(self.public(r, user, names)).get("waiting"))
            except Exception:
                traceback.print_exc()
        return n

    def cards(self, user) -> list[dict]:
        """GET /api/projects: one card per registered project."""
        names, rows = self._names(), self._rows(cols=LIGHT)
        out = []
        for p in self._projects():
            card = {"id": p["id"], "name": p["name"], "path": p["path"], "ready": False, "problem": "",
                    "state": "broken", "job": None, "last": None, "busy_other": False, "free": []}
            try:
                card.update(self.app.projects.state(p["path"]))
                own = [r for r in rows if r["project_id"] == p["id"] and self._sees(user, r)]
                job = next((r for r in own if r["status"] not in FINAL), None)
                last = next((r for r in own if r["status"] in FINAL), None)
                card["busy_other"] = any(r["project_id"] == p["id"] and r["status"] not in FINAL
                                         and not self._sees(user, r) for r in rows)
                if job:
                    card["job"] = timeline.brief(self.public(self._get(job["id"]), user, names))
                if last:   # a dropped job may have merged some of its items first: say so ("merged" > 0)
                    merged = timeline.brief(self.public(self._get(last["id"]), user, names)).get("merged", 0) \
                        if last["status"] == "cancelled" else 0
                    card["last"] = {"job_id": last["id"], "title": last["title"], "status": last["status"],
                                    "at": last["updated_at"], "merged": merged}
                if card["ready"]:
                    team = timeline.project_team(p["path"])
                    if team.get("problem"):   # the folder or its orch.toml cannot be read: nothing runs here
                        card.update(ready=False, problem=team["problem"])
                    card["free"] = list(dict.fromkeys(m["agent"]["name"] for m in team.get("team") or []
                                                      if m.get("state") == "idle"))
                card["state"] = self._card_state(card)
            except Exception as e:  # one broken project never hides the others
                traceback.print_exc()
                card.update(state="broken", problem=card["problem"] or f"{type(e).__name__}: {e}"[:300])
            out.append(card)
        return out

    @staticmethod
    def _card_state(card: dict) -> str:
        if not card["ready"]:
            return "broken"
        job = card["job"]
        if not job:
            return "busy" if card["busy_other"] else "idle"
        return {"plan": "waiting", "merge": "waiting", "attention": "attention",
                "stopped": "attention"}.get(job.get("waiting"), "busy")

    def project_page(self, user, pid: int) -> dict:
        """GET /api/project: the project, its team, the job going on and the history."""
        p, names = self.app.projects.get(pid), self._names()
        project = {"id": p["id"], "name": p["name"], "path": p["path"], "ready": False, "problem": ""}
        no_roles = {"plan": None, "build": None, "check": None}
        try:
            project.update(self.app.projects.state(p["path"]))
            team = timeline.project_team(p["path"])
        except Exception as e:
            traceback.print_exc()
            team = {"team": [], "roles": no_roles, "problem": f"{type(e).__name__}: {e}"[:300]}
        if team.get("problem") and not project["problem"]:
            project.update(ready=False, problem=team["problem"])
        rows = self._rows("project_id=?", (pid,), cols=LIGHT)
        own = [r for r in rows if self._sees(user, r)]
        active = next((r for r in own if r["status"] not in FINAL), None)
        other = next((r for r in rows if r["status"] not in FINAL and not self._sees(user, r)), None)
        busy_other = None
        if other:
            brief = timeline.brief(self.public(self._get(other["id"]), user, names))
            busy_other = {"owner": names.get(other["user_id"], ""), "title": other["title"],
                          "stage": brief.get("stage"), "since": other["created_at"]}
        return {"project": project, "team": team.get("team") or [], "roles": team.get("roles") or no_roles,
                "active": timeline.brief(self.public(self._get(active["id"]), user, names)) if active else None,
                "jobs": [timeline.brief(self.public(self._get(r["id"]), user, names)) for r in own[:50]],
                "busy_other": busy_other, "land": self._land(p["id"], user)}
