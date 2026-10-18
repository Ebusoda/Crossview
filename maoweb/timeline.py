"""What the 项目 pages show about one job: JobBrief, JobView, one run's record, a task's diff, a project's team.

Pure functions of the job dict maoweb/jobs.py hands over (the dispatch_jobs row with its JSON columns parsed, plus
owner, mine, can_act and active). Everything else comes from orch's own records, read only: .agents/state.db
(sqlite, mode=ro), the run events files (orch.activity), the per-run acceptance, progress and review files, and
git. Each part is built on its own, so a missing file or odd data shows up as a note instead of breaking the page;
caches keep a 2.5 s poll cheap, and view(job, sig) answers {"same": True} while nothing has changed.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import subprocess
import threading
import time
import traceback
from collections import OrderedDict
from pathlib import Path

from orch import activity
from orch.config import Config
from orch.handoff import blocked_from_text
from orch.models import INFRA_FAILURES
from orch.proc import child_env, pid_alive
from orch.providers import describe_pool
from orch.review import parse_verdict

VERSION = 1   # part of every sig: a server with other shapes never answers "same"
MAO = {"pool": "", "name": "Crossview", "vendor": "mao", "initials": "C", "color": "mao"}
NO_ROLES = {"plan": None, "build": None, "check": None}
ACTIVE = ("queued", "planning", "running", "pausing")
FINAL = ("done", "cancelled")
OPEN_END = ("merged", "cancelled")           # task statuses nothing more happens to
LIVE_ITEMS = 60                              # activity rows sent per run of the open section
ITEM_KEYS = ("id", "seq", "t", "kind", "target", "detail", "ok", "exit_code", "added", "removed", "tool", "sub",
             "task_file")
QUIET = frozenset({"think", "start", "done"})   # never shown as activity rows
FIX_MODES = frozenset({"address_review", "review_takeover", "fix_verify", "user_feedback", "feedback_takeover"})
INFRA = frozenset(s.value for s in INFRA_FAILURES)
DECISIONS = frozenset({"approve", "replan", "merge", "revise", "discard", "retry", "accept", "cancel", "pause",
                       "resume", "stop_mode"})
RESULTS = {"merged": "merged", "cancelled": "discarded", "verified": "waiting", "running": "running",
           "blocked": "blocked", "failed": "failed", "queued": "queued"}
LEVELS = {"review_skipped": "info", "escalated": "info", "cancelled": "info", "blocked": "warn", "interrupted": "warn",
          "action_failed": "warn", "dropped": "warn", "failed": "error", "merge_failed": "error",
          "plan_failed": "error", "orch_error": "error"}
GROUP = {"plan": "plan", "build": "build", "fix": "build", "conflict": "build", "check": "check"}
NO_FOLDER = "找不到这个项目的文件夹"
NO_RECORDS = "读不到这个项目的记录"
NO_CONFIG = "读不到这个项目的设置"
NO_RUN = "没有这条记录"
NO_TARGET = frozenset({"", "（无）", "(none)", "（なし）", "none"})   # orch's "escalate" row when no AI is left
NOT_OURS = "这一项不属于这个任务"
MERGE_FIX = ("Merging this work into the integration branch failed. Fix it so it merges cleanly and the acceptance "
             "commands pass there.\n\n")
MAX_PATCH = 200_000
TEAM_TTL = 3.0      # seconds a project's team is reused (every card asks for it on every poll)
ADAPTER_TTL = 60.0  # seconds "is this CLI installed" is reused
_SUR = re.compile("[\ud800-\udfff]")
_FENCE = re.compile(r"```[A-Za-z]*[ \t]*\n.*?(?:\n```|\Z)", re.S)
_NO_WINDOW = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if os.name == "nt" else {}
_MISS = object()


# --- small helpers ------------------------------------------------------------------------------

def _s(v) -> str:
    return v if isinstance(v, str) else "" if v is None else str(v)


def _clip(v, n: int) -> str:
    s = _s(v).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _f(v) -> float | None:
    """A finite number, else None (NaN/Infinity may not reach the browser)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    try:
        f = float(v)
    except OverflowError:
        return None
    return f if math.isfinite(f) else None


def _n(v) -> int:
    f = _f(v)
    return int(f) if f is not None else 0


def _loads(text, default):
    if isinstance(text, type(default)):
        return text
    try:
        value = json.loads(text) if isinstance(text, str) and text else default
    except ValueError:
        return default
    return value if isinstance(value, type(default)) else default


def _strip_fences(text) -> str:
    """An agent's message without its fenced JSON (the plan or verdict is shown on its own)."""
    return re.sub(r"\n{3,}", "\n\n", _FENCE.sub("", _s(text))).strip()


def _clean(o):
    """In place: no lone surrogates (an emoji cut in half) and no NaN/Infinity, so the page's JSON always encodes."""
    pairs = o.items() if isinstance(o, dict) else enumerate(o) if isinstance(o, list) else ()
    for k, v in list(pairs):
        if isinstance(v, str):
            if _SUR.search(v):
                o[k] = _SUR.sub("�", v)
        elif isinstance(v, float):
            if not math.isfinite(v):
                o[k] = None
        elif isinstance(v, (dict, list)):
            _clean(v)
    return o


def _part(fn, default):
    """One part of a page; a bug in it leaves the default and a traceback on the console, not a broken page."""
    try:
        return fn()
    except Exception:
        traceback.print_exc()
        return default


class _LRU:
    def __init__(self, size: int):
        self.size, self.data, self.lock = size, OrderedDict(), threading.Lock()

    def get(self, key):
        with self.lock:
            if key not in self.data:
                return _MISS
            self.data.move_to_end(key)
            return self.data[key]

    def put(self, key, value):
        with self.lock:
            self.data[key] = value
            self.data.move_to_end(key)
            while len(self.data) > self.size:
                self.data.popitem(last=False)
        return value


_PROJECTS, _ADAPTERS, _FILES = _LRU(64), _LRU(256), _LRU(1024)
_VERDICTS, _MODES, _VIS = _LRU(1024), _LRU(4096), _LRU(512)
_DIFFS, _PATCHES, _VIEWS, _BRIEFS, _TEAMS = _LRU(512), _LRU(16), _LRU(64), _LRU(512), _LRU(64)


def _stat(p) -> tuple | None:
    try:
        st = os.stat(p)
    except (OSError, ValueError, TypeError):
        return None
    return st.st_mtime_ns, st.st_size


def _text(p, limit: int | None = None) -> str:
    """A text file (its last `limit` characters), '' when it cannot be read."""
    try:
        with open(p, "rb") as f:
            if limit:
                f.seek(0, os.SEEK_END)
                f.seek(max(0, f.tell() - 4 * limit))
            data = f.read()
    except (OSError, ValueError, TypeError):
        return ""
    s = data.decode("utf-8", "replace")
    return s[-limit:] if limit else s


def _tests(p) -> dict | None:
    """Test counts of an acceptance log, cached by (path, mtime, size)."""
    st = _stat(p)
    if st is None:
        return None
    key = ("tests", str(p), st)
    hit = _FILES.get(key)
    return hit if hit is not _MISS else _FILES.put(key, activity.test_counts(_text(p)))


def _question(p) -> str | None:
    """The agent's question when its PROGRESS.md starts with "BLOCKED: ..." (None when not), by orch's own rule:
    what orch counts as a question is what the page shows as one."""
    st = _stat(p)
    if st is None:
        return None
    key = ("blocked", str(p), st)
    hit = _FILES.get(key)
    if hit is not _MISS:
        return hit
    found = blocked_from_text(_text(p))      # a BOM (PowerShell) is dropped there too
    if found is not None:
        found = "" if found == "no reason given" else _clip(found.strip(" *`"), 300)
    return _FILES.put(key, found)


def _connect(path) -> sqlite3.Connection | None:
    """orch's state.db, read only (never created, never written)."""
    try:
        db = Path(path) / ".agents" / "state.db"
        if not path or not db.is_file():
            return None
        con = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    except (sqlite3.Error, OSError, ValueError, RuntimeError):
        return None
    con.row_factory = sqlite3.Row
    return con


def _git(cwd, *args, timeout: float = 30) -> bytes | None:
    try:
        p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, env=child_env({"GIT_TERMINAL_PROMPT": "0"}), **_NO_WINDOW)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return p.stdout if p.returncode == 0 else None


def _job_ids(job: dict) -> tuple[list[str], list[str]]:
    tasks = list(dict.fromkeys(str(t) for t in job.get("task_ids") or [] if isinstance(t, str) and t))
    plans = list(dict.fromkeys(str(p) for p in job.get("plans") or [] if isinstance(p, str) and p))
    pid = job.get("plan_id")
    if isinstance(pid, str) and pid and pid not in plans:
        plans.append(pid)
    return tasks, plans


def _row(it: dict, output: bool = False) -> dict:
    d = {k: it.get(k) for k in ITEM_KEYS}
    if output:
        d["output"] = _s(it.get("output"))
    return d


def _both(a, b):
    """ok of a folded row: False if any failed, None while any runs, else True."""
    return False if a is False or b is False else None if a is None or b is None else True


def _visible(items: list[dict], strip: bool) -> list[dict]:
    """A run's rows as shown: no think/start/done; the orchestrator's own files read in one row; plan and review
    runs say their message without the fenced JSON."""
    out, fold, targets = [], None, []
    for it in items:
        kind = it.get("kind")
        if kind in QUIET:
            continue
        if kind == "say" and strip:
            text = _strip_fences(it.get("detail"))
            if not text:
                continue
            if text != it.get("detail"):
                it = {**it, "detail": text}
        if kind == "read" and it.get("task_file"):
            if fold is None:
                fold = {**it, "detail": "", "output": ""}
                out.append(fold)
            else:
                fold["ok"] = _both(fold.get("ok"), it.get("ok"))
            targets.append(_s(it.get("target")))
            continue
        out.append(it)
    if fold is not None:
        fold["target"] = _clip(", ".join(dict.fromkeys(t for t in targets if t)), activity.MAX_TARGET)
    return out


def _note(code: str, text, at, task_id="") -> dict:
    return {"type": "note", "at": _f(at), "level": LEVELS.get(code, "warn"), "code": code, "task_id": _s(task_id),
            "text": _clip(text, 300)}


def _event_entry(e: dict) -> dict | None:
    """A user's decision (or a note Crossview wrote) from the job's events list."""
    action = _s(e.get("action"))
    if action in DECISIONS:
        return {"type": "decision", "at": _f(e.get("at")), "action": action, "task_id": _s(e.get("task_id")),
                "text": _s(e.get("text")), "by": _s(e.get("by"))}
    if action == "note":
        return _note(_s(e.get("code")) or "orch_error", e.get("text"), e.get("at"), e.get("task_id"))
    if action == "dropped":
        return _note("dropped", e.get("text"), e.get("at"))
    return None


def _problem(rows: list[tuple], since: float) -> tuple[str, str]:
    """(code, detail) of the newest merge_failed row newer than the task's last work run."""
    newer = [m for ts, m in rows if (_f(ts) or 0) > since]
    if not newer:
        return "", ""
    code, _, detail = _s(newer[-1]).partition(":")
    code = code.strip()
    if code not in ("conflict", "tests_failed", "error"):
        code, detail = "error", _s(newer[-1])
    return code, _clip(detail, 300)


def _quota(text, now: float) -> dict | None:
    """{"five_hour": 22, "seven_day": 40}: percent used of the subscription windows that have not reset yet."""
    out = {}
    for name, w in _loads(text, {}).items():
        if not isinstance(w, dict) or (_f(w.get("resets_at")) or 0) <= now:
            continue
        u = _f(w.get("utilization"))
        if u is not None:
            out[str(name)] = round(u * 100 if u <= 1 else u)
    return out or None


# --- a project's settings --------------------------------------------------------------------------

class _Project:
    """A project's orch.toml as the pages show it (kept until the file changes)."""

    def __init__(self, path: Path):
        self.path = path
        self.problem = ""
        try:
            self.cfg = Config.load(path, apply_identity=False)
        except Exception:
            self.cfg, self.problem = None, NO_CONFIG
        self._desc: dict[str, dict] = {}

    @property
    def integ(self) -> str:
        try:
            return str(self.cfg.data["project"]["integration_branch"]) or "orch/integration"
        except (AttributeError, KeyError, TypeError):
            return "orch/integration"

    @property
    def manual_risks(self) -> list:
        try:
            return list(self.cfg.data.get("approval", {}).get("manual_merge_risk", ["high", "critical"]) or [])
        except (AttributeError, TypeError):
            return ["high", "critical"]

    @property
    def review_on(self) -> bool:
        try:
            return bool(self.cfg.data.get("review", {}).get("enabled", True))
        except (AttributeError, TypeError):
            return True

    def describe(self, pool: str) -> dict:
        d = self._desc.get(pool)
        if d is None:
            try:
                d = describe_pool(self.cfg, pool) if self.cfg is not None else None
            except Exception:
                d = None
            if not isinstance(d, dict):
                d = {"pool": pool, "name": pool or "?", "vendor": pool.lower(), "model": "", "billing": "subscription",
                     "initials": pool[:2], "color": "other"}
            self._desc[pool] = d
        return d

    def agent(self, pool) -> dict | None:
        if not pool:
            return None
        d = self.describe(str(pool))
        return {k: _s(d.get(k)) for k in ("pool", "name", "vendor", "initials", "color")}

    def billing(self, pool) -> str:
        return _s(self.describe(str(pool or "")).get("billing")) or "subscription"

    def adapter(self, pool: str) -> dict | None:
        """{"available", "tool_free", "vendor"} of a pool (cached a minute: it looks for the CLI)."""
        key = (str(self.path), pool)
        hit = _ADAPTERS.get(key)
        if hit is not _MISS and time.monotonic() - hit[0] < ADAPTER_TTL:
            return hit[1]
        info = None
        try:
            from orch.adapters import make_adapter

            a = make_adapter(self.cfg, pool)
            info = {"available": bool(a.available()), "tool_free": bool(a.tool_free), "vendor": _s(a.vendor)}
        except Exception:
            info = None
        _ADAPTERS.put(key, (time.monotonic(), info))
        return info

    def pick(self, route, tools: bool = True, not_vendor: str | None = None, closed=()) -> str | None:
        """The pool Crossview would take from a route now (installed, not paused or cooling)."""
        for pool in route or []:
            if self.cfg is None or pool not in self.cfg.pools or pool in closed:
                continue
            a = self.adapter(pool)
            if not a or not a["available"] or (tools and a["tool_free"]) or (not_vendor and a["vendor"] == not_vendor):
                continue
            return pool
        return None

    def builder(self, ttype: str = "code_change", difficulty: str = "M", closed=()) -> str | None:
        try:
            return self.pick(self.cfg.route_for(ttype or "code_change", difficulty or "M"), closed=closed)
        except Exception:
            return None

    def checker(self, builder: str | None, difficulty: str = "M", closed=()) -> str | None:
        """The reviewer: from another company than the builder (orch never lets a vendor check itself)."""
        if self.cfg is None or not self.review_on:
            return None
        vendor = (self.adapter(builder) or {}).get("vendor") if builder else None
        try:
            route = self.cfg.role_pools("review", "review", "pools", difficulty or "M")
        except Exception:
            return None
        return self.pick(route, tools=False, not_vendor=vendor, closed=closed)

    def roles(self, closed=()) -> dict:
        if self.cfg is None:
            return dict(NO_ROLES)
        try:
            plan = self.pick(self.cfg.route_for("plan", "M"), closed=closed)
        except Exception:
            plan = None
        build = self.builder(closed=closed)
        return {"plan": self.agent(plan), "build": self.agent(build),
                "check": self.agent(self.checker(build, closed=closed))}

    def pools(self) -> list[str]:
        """The pools some route names (in route order), else every pool."""
        if self.cfg is None:
            return []
        out: list[str] = []

        def add(v):
            if isinstance(v, str):
                out.append(v)
            elif isinstance(v, (list, tuple)):
                for x in v:
                    add(x)
            elif isinstance(v, dict):
                for x in v.values():
                    add(x)
        routing = self.cfg.data.get("routing") or {}
        add(routing)
        if "review" not in routing and self.review_on:
            add((self.cfg.data.get("review") or {}).get("pools"))
        return [p for p in dict.fromkeys(out) if p in self.cfg.pools] or list(self.cfg.pools)


def _project(path) -> _Project:
    p = Path(path)
    key = (str(p), _stat(p / ".agents" / "orch.toml"))
    hit = _PROJECTS.get(key)
    return hit if hit is not _MISS else _PROJECTS.put(key, _Project(p))


def integration_branch(path) -> str:
    """orch's integration branch of this project (orch.toml [project] integration_branch)."""
    return _project(path).integ


# --- git -----------------------------------------------------------------------------------------

def _diff_key(proj: _Project, tid: str, t: dict) -> tuple:
    return str(proj.path), proj.integ, tid, t.get("status"), t.get("updated_at")


def _numstat(proj: _Project, tid: str, t: dict) -> list[dict]:
    """Files the task's branch changes against the integration branch (cached until the task changes)."""
    key = ("numstat", *_diff_key(proj, tid, t))
    hit = _DIFFS.get(key)
    if hit is not _MISS:
        return hit
    out = _git(proj.path, "-c", "core.quotepath=off", "diff", "--numstat", "-z", "--no-renames",
               f"{proj.integ}...task/{tid}")
    files = []
    for chunk in (out or b"").split(b"\0"):
        parts = chunk.lstrip(b"\r\n").split(b"\t", 2)
        if len(parts) != 3 or not parts[2]:
            continue
        a, r = parts[0].strip(), parts[1].strip()
        binary = a == b"-" or r == b"-"
        try:
            added, removed = (None, None) if binary else (int(a), int(r))
        except ValueError:
            continue
        files.append({"path": parts[2].decode("utf-8", "replace"), "added": added, "removed": removed,
                      "binary": binary})
    return _DIFFS.put(key, files)


def _rank(f: dict) -> int:
    """Which file the merge card shows a few lines of: code first, then tests, then documents."""
    p = f["path"].replace("\\", "/").lower()
    name = p.rsplit("/", 1)[-1]
    if name.endswith((".md", ".txt", ".rst")):
        return 2
    if name.startswith("test") or "_test." in name or ".test." in name or ".spec." in name or "/tests/" in "/" + p:
        return 1
    return 0


def _preview(proj: _Project, tid: str, t: dict, files: list[dict]) -> dict | None:
    """Up to 14 changed lines of the most telling file: {"path", "lines": ["+ def sub(a, b):", ...]}."""
    cand = sorted((f for f in files if not f["binary"] and (f["added"] or 0) + (f["removed"] or 0) > 0), key=_rank)
    if not cand:
        return None
    f = cand[0]
    key = ("preview", *_diff_key(proj, tid, t), f["path"])
    hit = _DIFFS.get(key)
    if hit is not _MISS:
        return hit
    out = _git(proj.path, "-c", "core.quotepath=off", "--literal-pathspecs", "diff", "--no-ext-diff", "--no-textconv",
               "--no-color", f"{proj.integ}...task/{tid}", "--", f["path"])
    lines, body = [], False
    for ln in (out or b"").decode("utf-8", "replace").splitlines():
        if ln.startswith("@@"):
            body = True
        elif body and ln[:1] in ("+", "-"):
            lines.append(_clip(ln[0] + " " + ln[1:], 200) if ln[1:].strip() else ln[0])
            if len(lines) >= 14:
                break
    return _DIFFS.put(key, {"path": f["path"], "lines": lines} if lines else None)


def _patch(proj: _Project, tid: str, t: dict) -> tuple[str, bool]:
    key = ("patch", *_diff_key(proj, tid, t))
    hit = _PATCHES.get(key)
    if hit is not _MISS:
        return hit
    out = _git(proj.path, "-c", "core.quotepath=off", "diff", "--no-ext-diff", "--no-textconv", "--no-color",
               f"{proj.integ}...task/{tid}", timeout=60)
    text = (out or b"").decode("utf-8", "replace")
    return _PATCHES.put(key, (text[:MAX_PATCH], len(text) > MAX_PATCH))


# --- one job's records ---------------------------------------------------------------------------

class _Job:
    """One job's records, read once per request, and what the pages make of them."""

    def __init__(self, job: dict):
        self.job = job
        self.path = _s(job.get("path"))
        self.status = _s(job.get("status"))
        self.active = bool(job.get("active"))
        self.cancelling = self.active and bool(job.get("cancelling"))
        self.stop_mode = _s(job.get("stop_mode")) or "each"
        self.task_ids, self.plan_ids = _job_ids(job)
        # the plan was approved and the worker is creating its items: past 商量 already
        self.approving = self.active and not self.task_ids and _s(job.get("mode")) == "run"
        self.pending = {_s(p.get("task_id")): p for p in job.get("pending") or []
                        if isinstance(p, dict) and p.get("task_id")}
        self.events = [e for e in job.get("events") or [] if isinstance(e, dict)]
        self.tasks: dict[str, dict] = {}
        self.plans: dict[str, dict] = {}
        self.runs: list[dict] = []
        self.logs: list[dict] = []
        self.error, self.proj = "", None
        self._snaps: dict[str, tuple] = {}
        self._nows: dict[str, dict | None] = {}
        self._predicted: dict[tuple, str | None] = {}
        self._load()
        self.order = [t for t in self.task_ids if t in self.tasks]
        self.index = {t: i + 1 for i, t in enumerate(self.order)}
        self.runs_by: dict[str, list[dict]] = {}
        self.logs_by: dict[str, list[dict]] = {}
        for r in self.runs:
            self.runs_by.setdefault(r["task_id"], []).append(r)
        for row in self.logs:
            self.logs_by.setdefault(_s(row.get("task_id")), []).append(row)

    # -- reading --
    def _load(self) -> None:
        p = Path(self.path) if self.path else None
        try:
            here = p is not None and p.is_dir()
        except OSError:
            here = False
        if not here:
            self.error = NO_FOLDER
            return
        self.proj = _project(p)
        con = _connect(p)
        if con is None:
            if self.task_ids or self.plan_ids:
                self.error = NO_RECORDS
            return
        try:
            ids = self.task_ids + self.plan_ids
            if not ids:
                return
            marks = ",".join("?" * len(ids))
            if self.task_ids:
                tm = ",".join("?" * len(self.task_ids))
                for r in con.execute(f"SELECT * FROM tasks WHERE id IN ({tm})", self.task_ids):
                    t = dict(r)
                    for k in ("scope", "verify", "depends_on"):
                        t[k] = [_s(x) for x in _loads(t.get(k), [])]
                    self.tasks[t["id"]] = t
            if self.plan_ids:
                pm = ",".join("?" * len(self.plan_ids))
                self.plans = {r["id"]: dict(r) for r in con.execute(f"SELECT * FROM plans WHERE id IN ({pm})",
                                                                     self.plan_ids)}
            runs = [dict(r) for r in con.execute(f"SELECT * FROM runs WHERE task_id IN ({marks})", ids)]
            self.logs = [dict(r) for r in con.execute(f"SELECT * FROM log WHERE task_id IN ({marks}) ORDER BY id", ids)]
        except sqlite3.Error:
            self.error = NO_RECORDS
            return
        finally:
            con.close()
        runs.sort(key=lambda r: (_f(r.get("started_at")) or 0, len(_s(r.get("id"))), _s(r.get("id"))))
        worked: set[str] = set()
        for r in runs:
            r["task_id"], r["pool"] = _s(r.get("task_id")), _s(r.get("pool"))
            r["verified"] = None if r.get("verified") is None else bool(r["verified"])   # sqlite keeps 0/1
            kind = r["kind"] = _s(r.get("kind")) or "work"
            if kind in ("plan", "review"):
                mode = kind
            else:
                mode = self._mode(r) or ("takeover" if r["task_id"] in worked else "fresh")
                worked.add(r["task_id"])
            r["mode"] = mode
            r["role"] = ({"plan": "plan", "review": "check"}.get(kind)
                         or ("conflict" if mode == "resolve_conflict" else "fix" if mode in FIX_MODES else "build"))
            r["live"] = r.get("status") == "running" and self.active
        self.runs = runs

    def _mode(self, r: dict) -> str:
        """How a work run started (orch keeps it in the run's context manifest)."""
        text = r.get("context_manifest")
        if not text:
            return ""
        key = (self.path, r.get("id"), r.get("started_at"))
        hit = _MODES.get(key)
        return hit if hit is not _MISS else _MODES.put(key, _s(_loads(text, {}).get("mode")))

    def agent(self, pool) -> dict | None:
        if not pool:
            return None
        if self.proj is not None:
            return self.proj.agent(pool)
        pool = str(pool)
        return {"pool": pool, "name": pool, "vendor": pool.lower(), "initials": pool[:2], "color": "other"}

    def billing(self, pool) -> str:
        return self.proj.billing(pool) if self.proj is not None else "subscription"

    def plan_data(self, pid: str) -> dict:
        return _loads((self.plans.get(pid) or {}).get("plan_json"), {})

    def snap(self, r: dict) -> tuple:
        """(items, ended, usage, final_text) of the run's events file (orch.activity keeps the feed)."""
        if r["id"] not in self._snaps:
            path, root = self._events(r)
            self._snaps[r["id"]] = activity.feed(path, root).snapshot() if path else ([], False, None, "")
        return self._snaps[r["id"]]

    def _events(self, r: dict) -> tuple[str, str | None]:
        agents = Path(self.path) / ".agents"
        tid, rid = r["task_id"], _s(r.get("id"))
        if r["kind"] == "plan":
            derived = agents / "plans" / tid / f"{rid}.jsonl"
            cfg = self.proj.cfg if self.proj is not None else None
            root = str(cfg.worktrees_dir / "_planning") if cfg is not None else None
        else:
            name = f"{rid}.review.jsonl" if r["kind"] == "review" else f"{rid}.jsonl"
            derived = agents / "tasks" / tid / "runs" / name
            root = _s((self.tasks.get(tid) or {}).get("worktree")) or None
        path = _s(r.get("events_path"))
        if not path or (not os.path.exists(path) and derived.exists()):   # a project folder moved since
            path = str(derived)
        return path, root

    def items(self, r: dict) -> list[dict]:
        """The run's activity rows as shown (shared rows: copy before changing one)."""
        snap = self.snap(r)
        key = (self.path, r["id"], r.get("started_at"))
        hit = _VIS.get(key)
        if hit is not _MISS and hit[0] is snap:
            return hit[1]
        return _VIS.put(key, (snap, _visible(snap[0], r["kind"] in ("plan", "review"))))[1]

    def run_tokens(self, r: dict) -> int:
        usage = self.snap(r)[2] if r["live"] else None
        if usage:
            return _n(usage.get("tokens_in")) + _n(usage.get("tokens_out"))
        return _n(r.get("tokens_in")) + _n(r.get("tokens_out"))

    def run_cost(self, r: dict) -> float:
        cost = _f(r.get("est_cost_usd"))
        if cost is None and r["live"]:
            cost = _f((self.snap(r)[2] or {}).get("cost_usd"))
        return cost or 0.0

    def item_tokens(self, tid: str) -> int:
        return sum(self.run_tokens(r) for r in self.runs_by.get(tid, []))

    def work(self, tid: str) -> list[dict]:
        """The item's build and fix runs (conflict resolution is part of merging)."""
        return [r for r in self.runs_by.get(tid, []) if r["kind"] == "work" and r["role"] != "conflict"]

    def status_of(self, tid: str) -> str:
        return _s((self.tasks.get(tid) or {}).get("status"))

    def runnable(self, tid: str) -> bool:
        return all(self.status_of(d) == "merged" for d in (self.tasks.get(tid) or {}).get("depends_on") or [])

    def commands(self, tid: str) -> list[str]:
        return [activity.simplify_command(c) for c in (self.tasks.get(tid) or {}).get("verify") or [] if c]

    def predicted(self, ttype: str, difficulty: str) -> tuple[str | None, str | None]:
        """(builder, reviewer) Crossview would pick for an item of this type and size."""
        key = (ttype, difficulty)
        if key not in self._predicted:
            b = self.proj.builder(ttype, difficulty) if self.proj is not None else None
            c = self.proj.checker(b, difficulty) if self.proj is not None else None
            self._predicted[key] = (b, c)
        return self._predicted[key]

    def merge_problem(self, tid: str) -> tuple[str, str]:
        since = max((_f(r.get("started_at")) or 0 for r in self.runs_by.get(tid, []) if r["kind"] == "work"), default=0)
        return _problem([(row.get("ts"), row.get("message")) for row in self.logs_by.get(tid, [])
                         if row.get("kind") == "merge_failed"], since)

    def auto_merges(self, tid: str) -> bool:
        """orch merges this verified item by itself (stop mode plan): its risk is not held back for a person and
        no merge of it failed."""
        manual = self.proj.manual_risks if self.proj is not None else ["high", "critical"]
        return ((self.tasks.get(tid) or {}).get("risk") or "normal") not in manual and not self.merge_problem(tid)[0]

    def holds(self, tid: str) -> bool:
        """A verified item waits for the user (gets a merge checkpoint)."""
        return not (self.active and self.stop_mode == "plan" and self.auto_merges(tid))

    def run_tests(self, r: dict | None) -> dict | None:
        if not r or r.get("verified") is None:
            return None
        d = Path(self.path) / ".agents" / "tasks" / r["task_id"]
        p = d / "runs" / f"{r['id']}.verify.log"
        if not p.is_file():
            done = [x for x in self.work(r["task_id"]) if x.get("verified") is not None]
            if not done or done[-1] is not r:
                return None
            p = d / "verify.log"   # runs from before orch kept a log per run: only the newest one's survives
        counts = _tests(p)
        return dict(counts) if counts else None   # a copy: the cached one is shared

    def tests(self, r: dict | None) -> dict | None:
        counts = self.run_tests(r)
        return {"ok": bool(r.get("verified")), **counts} if counts else None

    def verdict(self, r: dict) -> dict | None:
        if r["kind"] != "review" or r.get("status") != "completed":
            return None
        key = (self.path, r["id"], r.get("ended_at"))
        hit = _VERDICTS.get(key)
        if hit is not _MISS:
            return hit
        try:
            v = parse_verdict(_s(r.get("summary")))
            out = {"result": v["verdict"], "summary": _s(v["summary"]),
                   "issues": [{k: _s(i.get(k)) for k in ("severity", "file", "detail")} for i in v["issues"]]}
        except Exception:
            out = {"result": "unreadable", "summary": "", "issues": []}
        return _VERDICTS.put(key, out)

    def question(self, tid: str) -> str | None:
        work = self.work(tid)
        return self.asked(work[-1]) if work else None

    def asked(self, r: dict) -> str | None:
        """The question a work run ended with (its own copy of PROGRESS.md), None when it asked none."""
        return _question(Path(self.path) / ".agents" / "tasks" / r["task_id"] / "runs" / f"{r['id']}.progress.md")

    # -- what is happening now --
    def _mao(self, kind: str, role: str, tid: str, since, target: str = "", run_id: str = "") -> dict:
        return {"agent": dict(MAO), "role": role, "kind": kind, "target": target, "live": True, "since": _f(since),
                "run_id": run_id, "task_id": tid, "tokens": self.item_tokens(tid)}

    def _live_now(self, r: dict) -> dict:
        vis = self.items(r)
        last = vis[-1] if vis else None
        kind = _s(last.get("kind")) if last else "start"
        return {"agent": self.agent(r["pool"]), "role": r["role"], "kind": kind,
                "target": _s(last.get("target")) if last and kind != "say" and not last.get("task_file") else "",
                "live": True,
                "since": _f(r.get("started_at")), "run_id": r["id"], "task_id": r["task_id"],
                "tokens": self.item_tokens(r["task_id"])}

    def task_now(self, tid: str) -> dict | None:
        """What happens on this item right now: its live run, or Crossview between runs (only while the job is
        active)."""
        if tid in self._nows:
            return self._nows[tid]
        now = None
        runs = self.runs_by.get(tid, [])
        live = [r for r in runs if r["live"]]
        t = self.tasks.get(tid) or {}
        if live:
            now = self._live_now(live[-1])
        elif self.active and t.get("status") == "running":
            work = self.work(tid)
            lw, last = (work[-1] if work else None), (runs[-1] if runs else None)
            if (lw is not None and lw is last and lw.get("verified") is None
                    and lw.get("status") not in INFRA | {"running"}):
                cmds = self.commands(tid)
                now = self._mao("verify", "check", tid, lw.get("ended_at"), cmds[0] if cmds else "", lw["id"])
            else:
                role = "check" if lw is not None and lw.get("verified") is True and last is lw else "build"
                now = self._mao("wait", role, tid, (last or {}).get("ended_at") or t.get("updated_at"))
        elif self.active and t.get("status") == "verified" and (
                _s((self.pending.get(tid) or {}).get("action")) == "merge" or not self.holds(tid)):
            now = self._mao("merge", "merge", tid, t.get("updated_at"))
        self._nows[tid] = now
        return now

    def now(self) -> dict | None:
        """The newest live run; else Crossview between runs; else Crossview waiting while the job is active. While
        不做了 is on its way, Crossview stopping it."""
        if self.cancelling:
            return {"agent": dict(MAO), "role": "", "kind": "cancel", "target": "", "live": True,
                    "since": _f(self.job.get("updated_at")), "run_id": "", "task_id": "", "tokens": 0}
        live = [r for r in self.runs if r["live"]]
        if live:
            return self._live_now(max(live, key=lambda r: _f(r.get("started_at")) or 0))
        between = [n for n in (self.task_now(t) for t in self.order) if n]
        if between:
            rank = {"verify": 0, "merge": 1, "wait": 2}
            return min(between, key=lambda n: (rank.get(n["kind"], 3), -(n["since"] or 0)))
        if self.active:
            at = (self.current() or "") if self.task_ids else (self.plan_ids[-1] if self.plan_ids else "")
            role = "build" if self.task_ids or self.approving else "plan"
            return self._mao("wait", role, at, self.job.get("updated_at"))
        return None

    def current(self) -> str | None:
        """The item the headline is about: the one at work now, else the first waiting for the user, else the next."""
        live = [(n["since"] or 0, -self.index[t], t) for t in self.order if (n := self.task_now(t))]
        if live:
            return max(live)[2]
        for wanted in (("verified", "blocked", "failed"), ("queued", "running")):
            for t in self.order:
                if self.status_of(t) in wanted and (self.status_of(t) != "verified" or self.holds(t)):
                    return t
        return self.order[-1] if self.order else None

    # -- the stage bar --
    def approved_at(self) -> float | None:
        at = [_f(e.get("at")) for e in self.events if e.get("action") == "approve"]
        if at and at[-1]:
            return at[-1]
        ok = [p for p in self.plan_ids if (self.plans.get(p) or {}).get("status") == "approved"]
        return _f(self.plans[ok[-1]].get("updated_at")) if ok else None

    def _step(self, cur: str | None) -> str:
        if not self.task_ids:
            return "build" if self.approving else "plan_gate" if self.status == "plan_ready" else "plan"
        if cur is None:
            return "build"
        n = self.task_now(cur)
        if n:
            return {"check": "check", "conflict": "merge_gate", "merge": "merge_gate"}.get(n["role"], "build")
        st = self.status_of(cur)
        if st in ("verified", "merged"):
            return "merge_gate"
        if st in ("blocked", "failed"):
            runs = self.runs_by.get(cur, [])
            return "check" if runs and runs[-1]["kind"] == "review" else "build"
        return "build"

    def missing(self) -> bool:
        """The project folder (or orch's records) went away while the job was not finished."""
        return self.error in (NO_FOLDER, NO_RECORDS) and not self.active and self.status not in FINAL

    def stage(self) -> dict:
        cur = self.current()
        step = "done" if self.status == "done" else self._step(cur)
        if self.status == "done":
            key = "done"
        elif self.status == "cancelled" or self.missing() or (not self.active and self.status not in ("plan_ready",
                                                                                                      "waiting")):
            key = "stopped"
        else:
            key = step
        has = bool(self.task_ids) or self.approving
        gate = {"state": "done" if has else "waiting" if self.status == "plan_ready" else "todo",
                "at": self.approved_at() if has else None}
        merge = {"state": "todo", "at": None}
        if cur:
            st, pend = self.status_of(cur), self.pending.get(cur) or {}
            if st == "merged":
                ts = [_f(row.get("ts")) for row in self.logs_by.get(cur, []) if row.get("kind") == "merged"]
                merge = {"state": "done", "at": ts[-1] if ts else None}
            elif st == "verified" and _s(pend.get("action")) == "merge":
                merge = {"state": "done", "at": _f(pend.get("at"))}
            elif st == "verified" and self.holds(cur) and not self.cancelling:
                merge = {"state": "waiting", "at": None}
        return {"key": key, "step": step, "plan_gate": gate, "merge_gate": merge,
                "item": {"index": self.index[cur], "total": len(self.order)} if has and cur else None}

    def progress(self) -> list[float]:
        """Four segments 商量 动手 检查 合并, 0..1 each, for the card's bar."""
        has_plan = bool(self.task_ids) or self.status == "plan_ready" or self.approving
        tried = any(self.runs_by.get(p) for p in self.plan_ids)
        p_plan = 1.0 if has_plan else 0.5 if (self.active or tried) else 0.0
        items = [t for t in self.order if self.status_of(t) != "cancelled"] or self.order
        if not items:
            return [p_plan, 0.0, 0.0, 0.0]
        b = c = m = 0.0
        for t in items:
            st, runs, n = self.status_of(t), self.runs_by.get(t, []), self.task_now(t)
            if st in ("verified", "merged"):
                b, c = b + 1, c + 1
            else:
                b += 0.5 if (st == "running" or any(r["kind"] == "work" for r in runs)) else 0
                c += 0.5 if (any(r["kind"] == "review" for r in runs) or (n and n["role"] == "check")) else 0
            m += st == "merged"
        k = len(items)
        return [p_plan, round(b / k, 2), round(c / k, 2), round(m / k, 2)]

    def recent(self) -> dict | None:
        """The last notable thing: {"agent", "what": planned|approved|changes|verified|asked|failed|merged, "at"}."""
        best = None

        def offer(at, agent, what):
            nonlocal best
            at = _f(at)
            if at is not None and agent and (best is None or at >= best["at"]):
                best = {"agent": agent, "what": what, "at": at}
        for pid in self.plan_ids:
            done = [r for r in self.runs_by.get(pid, []) if r.get("status") == "completed"]
            if (self.plans.get(pid) or {}).get("status") in ("draft", "approved", "rejected") and done:
                offer(done[-1].get("ended_at"), self.agent(done[-1]["pool"]), "planned")
        for r in self.runs:
            if r["live"] or r["kind"] == "plan" or not r.get("ended_at") or r.get("status") == "interrupted":
                continue   # stopped by a pause, 不做了 or a restart: nothing the agent did
            if r["kind"] == "review" and r.get("verified") is not None:
                offer(r["ended_at"], self.agent(r["pool"]), "approved" if r["verified"] else "changes")
            elif r["kind"] == "work" and r["role"] != "conflict" and self.asked(r) is not None:
                offer(r["ended_at"], self.agent(r["pool"]), "asked")   # it stopped to ask the user something
            elif r.get("status") not in ("completed", "running") or (r["role"] != "conflict"
                                                                       and r.get("verified") is False):
                offer(r["ended_at"], self.agent(r["pool"]), "failed")
        for row in self.logs:
            tid, kind = _s(row.get("task_id")), row.get("kind")
            if kind == "merged":
                offer(row.get("ts"), dict(MAO), "merged")
            elif kind == "task_verified" and not _s(row.get("message")).startswith("reviewed by"):
                work = self.work(tid)
                offer(row.get("ts"), self.agent(work[-1]["pool"]) if work else dict(MAO), "verified")
        return best

    # -- what the user can decide now --
    def checkpoints(self, light: bool = False) -> list[dict]:
        if self.cancelling:   # 不做了 is on its way: nothing is left for anyone to decide
            return []
        out = []
        if self.status == "plan_ready" and not self.task_ids:
            drafts = [p for p in self.plan_ids if (self.plans.get(p) or {}).get("status") == "draft"]
            if drafts:
                tasks = self.plan_data(drafts[-1]).get("tasks")
                out.append({"kind": "plan", "plan_id": drafts[-1], "title": "",
                            "count": len(tasks) if isinstance(tasks, list) else 0,
                            "actions": ["approve", "replan", "cancel"]})
        for tid in self.order:
            st = self.status_of(tid)
            try:
                if st == "verified" and self.holds(tid):
                    out.append(self._merge_card(tid, light))
                elif st in ("blocked", "failed"):
                    out.append(self._attention_card(tid, light))
            except Exception:
                traceback.print_exc()
        stop = self._stopped_card(bool(out))
        if stop:
            out.append(stop)
        return out

    def _card(self, kind: str, tid: str) -> dict:
        return {"kind": kind, "task_id": tid, "index": self.index[tid], "total": len(self.order),
                "title": _s(self.tasks[tid].get("title"))}

    def _queued(self, tid: str) -> str | None:
        return _s((self.pending.get(tid) or {}).get("action")) or None

    def _merge_card(self, tid: str, light: bool) -> dict:
        card = self._card("merge", tid)
        if light:
            return card
        t, runs, work = self.tasks[tid], self.runs_by.get(tid, []), self.work(tid)
        lw = work[-1] if work else None
        code, detail = self.merge_problem(tid)
        files = self.files(tid)
        # the time the agents worked on it (not the hours it waited for the user between rounds)
        spent = [max(0.0, e - b) for b, e in ((_f(r.get("started_at")), _f(r.get("ended_at"))) for r in runs)
                 if b is not None and e is not None]
        cost, note = 0.0, {"api": [], "subscription": []}
        for r in runs:
            b = "api" if self.billing(r["pool"]) == "api" else "subscription"
            cost += self.run_cost(r) if b == "api" else 0.0
            name = (self.agent(r["pool"]) or {}).get("name")
            if name and name not in note[b]:
                note[b].append(name)
        rv = [r for r in runs if r["kind"] == "review" and (lw is None or (_f(r.get("started_at")) or 0)
                                                             >= (_f(lw.get("started_at")) or 0))]
        rv = rv[-1] if rv else None
        if rv is not None and rv.get("verified") is True:
            review = {"result": "approve", "minor": _n(rv.get("issues_minor")), "agent": self.agent(rv["pool"])}
        else:
            review = {"result": "skipped", "minor": 0, "agent": None}
        nxt = next((x for x in self.order if x != tid and self.status_of(x) not in OPEN_END), None)
        card.update({
            "files": files, "preview": _preview(self.proj, tid, t, files) if self.proj is not None else None,
            "tests": self.tests(lw),
            "minutes": max(1, round(sum(spent) / 60)) if spent else None,
            "cost_usd": round(cost, 6), "billing_note": note, "review": review,
            "next": {"index": self.index[nxt], "title": _s(self.tasks[nxt].get("title"))} if nxt else None,
            "merge_note": _clip(t.get("note"), 300) if code else "", "merge_problem": code, "merge_detail": detail,
            # after a failed merge: 让它修一下 first; 再合并一次 too unless git itself conflicted (a check that
            # failed may pass now, e.g. when the earlier one was cut short or another item has been fixed since)
            "actions": ["merge", "revise", "discard"] if not code else
            ["revise", "discard"] + (["merge"] if code in ("error", "tests_failed") else []),
            "queued": self._queued(tid)})
        return card

    def reason(self, tid: str) -> tuple[str, str]:
        """Why an item stopped: (reason, the agent's question when it asked one)."""
        note = _s(self.tasks[tid].get("note")).strip().lower()
        if "still not approved after" in note:
            return "review_rejected", ""
        q = self.question(tid)
        if q is not None:
            return "agent_blocked", q
        for start, reason in (("budget:", "budget"), ("too many runs", "too_many_runs"),
                              ("reached max_runs", "max_runs"), ("no available pool", "no_pool"),
                              ("no routing rule", "no_route"), ("orchestrator error", "orch_error")):
            if note.startswith(start):
                m = re.match(r"no available pool at escalation level (\d+) of \[(.*)\]", note)
                if m and int(m.group(1)) >= len([p for p in m.group(2).split(",") if p.strip()]):
                    return "max_runs", ""     # every AI on the route had its turns and acceptance still failed
                return reason, ""
        return "other", ""

    def _attention_card(self, tid: str, light: bool) -> dict:
        card = self._card("attention", tid)
        if light:
            return card
        reason, question = self.reason(tid)
        issues = []
        if reason == "review_rejected":
            rv = [r for r in self.runs_by.get(tid, []) if r["kind"] == "review" and r.get("verified") is not None]
            issues = list((self.verdict(rv[-1]) or {}).get("issues") or []) if rv else []
        work = self.work(tid)
        accept = reason != "agent_blocked" and bool(work) and work[-1].get("verified") is True
        card.update({"status": self.status_of(tid), "reason": reason, "note": _clip(self.tasks[tid].get("note"), 300),
                     "issues": issues, "question": question,
                     "actions": ["revise", "retry", "discard"] + (["accept"] if accept else []),
                     "queued": self._queued(tid)})
        return card

    def stalled(self) -> bool:
        """Only queued items are left and none can start (a dependency was dropped)."""
        left = [t for t in self.order if self.status_of(t) not in OPEN_END]
        return bool(left) and all(self.status_of(t) == "queued" and not self.runnable(t) for t in left)

    def would_run(self) -> bool:
        return bool(self.pending) or any(
            self.status_of(t) == "verified" or (self.status_of(t) in ("queued", "running") and self.runnable(t))
            for t in self.order)

    def _stopped_card(self, others: bool) -> dict | None:
        st, has = self.status, bool(self.task_ids)
        if self.missing():   # nothing can go on until the folder is back; 不做了 ends it
            return {"kind": "stopped", "reason": "missing", "error": self.error, "has_tasks": has, "actions": ["cancel"]}
        if self.active or st in FINAL or st in ("plan_ready", "waiting"):
            return None
        if st == "failed":
            reason = "failed" if has else "plan_failed"
        elif st in ("paused", "pausing"):
            reason = "paused"
        elif st == "interrupted" or st in ACTIVE:   # an active status nobody works on: the server went away
            reason = "interrupted"
        elif self.stalled():
            reason = "stalled"
        elif not others:
            reason = "paused"   # runnable items remain (the card offers 继续做)
        else:
            return None
        error = _s(self.job.get("error"))
        if not error and reason == "plan_failed":
            failed = [p for p in self.plan_ids if (self.plans.get(p) or {}).get("status") == "failed"]
            error = _s(self.plans[failed[-1]].get("error")) if failed else ""
        if reason == "stalled":
            actions = ["cancel"]
        elif not has:
            actions = (["resume"] if reason in ("paused", "interrupted") else []) + ["replan", "cancel"]
        else:
            actions = (["resume"] if self.would_run() else []) + ["cancel"]
        return {"kind": "stopped", "reason": reason, "error": _clip(error, 300), "has_tasks": has, "actions": actions}

    # -- the rest of the page --
    def files(self, tid: str) -> list[dict]:
        """What the item changes: git for finished work, the agents' edit rows while it runs."""
        t = self.tasks.get(tid) or {}
        if t.get("status") == "running":
            acc: dict[str, dict] = {}
            for r in self.work(tid):
                for it in self.items(r):
                    target = _s(it.get("target"))
                    if it.get("kind") in ("edit", "write", "delete") and target and not it.get("task_file"):
                        f = acc.setdefault(target, {"path": target, "added": None, "removed": None, "binary": False})
                        for k in ("added", "removed"):
                            if isinstance(it.get(k), int):
                                f[k] = (f[k] or 0) + it[k]
            return [dict(f) for f in acc.values()]
        if self.proj is None or not t.get("branch"):
            return []
        return [dict(f) for f in _numstat(self.proj, tid, t)]

    def result(self) -> dict | None:
        """Where merged work sits: orch's integration branch, in its own folder next to the project, and whether it
        is in the user's own folder yet (land: maoweb/landing.py, handed over by jobs.py for a finished job)."""
        if self.proj is None:
            return None
        folder = ""
        try:
            wt = self.proj.cfg.worktrees_dir / "_integration"
            folder = str(wt) if wt.is_dir() else ""
        except (AttributeError, OSError, TypeError, ValueError):
            pass
        land = self.job.get("land")
        return {"branch": self.proj.integ, "folder": folder, "land": land if isinstance(land, dict) else None}

    def job_part(self) -> dict:
        j = self.job
        return {"id": j.get("id"), "project_id": j.get("project_id") or 0, "project": _s(j.get("project")),
                "title": _s(j.get("title")), "goal": _s(j.get("goal")), "status": self.status,
                "stop_mode": self.stop_mode, "created_at": _f(j.get("created_at")),
                "updated_at": _f(j.get("updated_at")),
                "error": self.error or _s(j.get("error")), "with_chat": bool(j.get("with_chat")),
                "conversation_id": j.get("conversation_id") or 0, "mine": bool(j.get("mine")),
                "can_act": bool(j.get("can_act")), "owner": _s(j.get("owner")), "active": self.active,
                "cancelling": self.cancelling,
                "pending": [{k: p.get(k, "") for k in ("action", "task_id", "text", "at", "by")}
                            for p in j.get("pending") or [] if isinstance(p, dict)]}

    def plan_view(self) -> dict | None:
        ok = [p for p in self.plan_ids if (self.plans.get(p) or {}).get("status") == "approved"]
        drafted = [p for p in self.plan_ids if (self.plans.get(p) or {}).get("plan_json")]
        pid = (ok or drafted or [None])[-1]
        if pid:
            row, data = self.plans[pid], self.plan_data(pid)
            status, tmap = _s(row.get("status")), _loads(row.get("task_map"), {})
        elif isinstance(self.job.get("plan_json"), dict) and self.job["plan_json"].get("tasks"):
            pid, data, tmap = _s(self.job.get("plan_id")), self.job["plan_json"], {}   # orch's records are gone
            status = _s((self.plans.get(pid) or {}).get("status")) or ("draft" if self.status == "plan_ready" else "")
        else:
            return None
        tasks = []
        for t in data.get("tasks") or []:
            if not isinstance(t, dict):
                continue
            key = _s(t.get("key"))
            tid = _s(tmap.get(key))
            runs = self.runs_by.get(tid, []) if tid else []
            built = [r for r in runs if r["kind"] == "work"]
            seen = [r for r in runs if r["kind"] == "review"]
            builder, checker = self.predicted(_s(t.get("type")), _s(t.get("difficulty")))
            tasks.append({"key": key, "task_id": tid, "title": _s(t.get("title")), "spec": _clip(t.get("spec"), 1000),
                          "type": _s(t.get("type")), "difficulty": _s(t.get("difficulty")), "risk": _s(t.get("risk")),
                          "scope": [_s(x) for x in t.get("scope") or [] if x],
                          "verify": [activity.simplify_command(_s(x)) for x in t.get("verify") or [] if x],
                          "depends_on": [_s(tmap.get(d, d)) for d in t.get("depends_on") or [] if d],
                          "agent": self.agent(built[-1]["pool"] if built else builder),
                          "reviewer": self.agent(seen[-1]["pool"] if seen else checker)})
        return {"id": pid, "status": status, "summary": _s(data.get("summary")),
                "warnings": [_s(w) for w in data.get("warnings") or []], "tasks": tasks}

    def tasks_view(self) -> list[dict]:
        keys = {}
        for p in self.plan_ids:
            for k, v in _loads((self.plans.get(p) or {}).get("task_map"), {}).items():
                keys[_s(v)] = _s(k)
        out = []
        for tid in self.order:
            t, runs, work = self.tasks[tid], self.runs_by.get(tid, []), self.work(tid)
            n = self.task_now(tid)
            sha = [_s(row.get("message")) for row in self.logs_by.get(tid, []) if row.get("kind") == "merged"]
            starts = [x for x in (_f(r.get("started_at")) for r in runs) if x]
            ends = [x for x in (_f(r.get("ended_at")) for r in runs) if x]
            out.append({"id": tid, "key": keys.get(tid, ""), "index": self.index[tid], "title": _s(t.get("title")),
                        "status": self.status_of(tid), "depends_on": list(t.get("depends_on") or []),
                        "agent": self.agent(work[-1]["pool"] if work else
                                            self.predicted(_s(t.get("type")), _s(t.get("difficulty")))[0]),
                        "files": self.files(tid), "started_at": min(starts) if starts else None,
                        "ended_at": max(ends) if ends and not n else None, "note": _s(t.get("note")),
                        "merged_sha": "" if not sha or sha[-1] == "nothing to merge" else sha[-1],
                        "live": {k: n[k] for k in ("role", "agent", "kind", "target", "since")} if n else None})
        return out

    def usage(self) -> dict:
        by: dict[str, dict] = {}
        tokens, cost, api, sub = 0, 0.0, 0, 0
        for r in self.runs:
            b = "api" if self.billing(r["pool"]) == "api" else "subscription"
            tk, c = self.run_tokens(r), (self.run_cost(r) if b == "api" else 0.0)
            tokens, cost = tokens + tk, cost + c
            api, sub = api + (b == "api"), sub + (b != "api")
            a = by.setdefault(r["pool"], {"agent": self.agent(r["pool"]), "billing": b, "tokens": 0, "cost_usd": 0.0})
            a["tokens"] += tk
            a["cost_usd"] = round(a["cost_usd"] + c, 6)
        return {"tokens": tokens, "cost_usd": round(cost, 6), "api_runs": api, "subscription_runs": sub,
                "by_agent": list(by.values())}

    def team(self) -> list[dict]:
        members: dict[tuple, dict] = {}
        for r in self.runs:
            key = (r["pool"], GROUP.get(r["role"], "build"))
            m = members.setdefault(key, {"agent": self.agent(r["pool"]), "role": key[1], "state": "idle"})
            if r["live"]:
                m["state"] = "working"
        final = self.status in FINAL
        all_done = bool(self.order) and all(self.status_of(t) in OPEN_END for t in self.order)
        merged = any(self.status_of(t) == "merged" for t in self.order)
        planned = bool(self.task_ids) or self.status == "plan_ready" or self.approving
        for m in members.values():
            if m["state"] == "working":
                continue
            if self.status == "cancelled":   # dropped: only a part that was finished is done (a plan, merged items)
                m["state"] = "done" if (planned if m["role"] == "plan" else merged) else "idle"
            elif final or (m["role"] == "plan" and planned) or (m["role"] != "plan" and all_done):
                m["state"] = "done"
        if not final and self.proj is not None:
            have = {role for _, role in members}
            roles = self.proj.roles()
            for role in ("plan", "build", "check"):
                if role in have or (role == "plan" and planned) or not roles.get(role):
                    continue
                members[(roles[role]["pool"], role)] = {"agent": roles[role], "role": role, "state": "waiting"}
        order = {"plan": 0, "build": 1, "check": 2}
        return sorted(members.values(), key=lambda m: order.get(m["role"], 3))

    # -- the sections of the timeline --
    def sections(self) -> list[dict]:
        cur = self.current()
        secs = [_part(self._plan_section, None) or {"key": "plan", "title": "", "state": "stopped", "summary": {},
                                                     "entries": [_note("orch_error", "plan section", None)]}]
        for tid in self.order:
            try:
                s = self._task_section(tid, cur)
            except Exception as e:
                traceback.print_exc()
                s = {"key": tid, "title": _s(self.tasks[tid].get("title")), "index": self.index[tid],
                     "state": "stopped", "summary": {}, "entries": [_note("orch_error", f"{type(e).__name__}: {e}",
                                                                          None, tid)]}
            if s:
                secs.append(s)
        self._place(secs)
        start = _f(self.job.get("created_at")) or 0
        for s in secs:
            s["entries"].sort(key=lambda e: e.get("at") if e.get("at") is not None else start)
        return secs

    def _plan_section(self) -> dict:
        runs = [r for p in self.plan_ids for r in self.runs_by.get(p, [])]
        if any(r["live"] for r in runs) or (self.active and not self.task_ids and not self.approving
                                            and not self.cancelling):
            state = "live"
        elif self.status == "plan_ready" and not self.task_ids:
            state = "waiting"
        elif self.task_ids or self.status == "done" or self.approving:
            state = "done"
        else:
            state = "stopped"
        if state == "waiting" and self.missing():
            state = "stopped"
        full = state in ("live", "waiting")
        entries = [{"type": "ask", "at": _f(self.job.get("created_at")), "text": _s(self.job.get("goal")),
                    "with_chat": bool(self.job.get("with_chat")), "by": _s(self.job.get("owner"))}]
        failed = False
        for pid in self.plan_ids:
            for r in self.runs_by.get(pid, []):
                entries += self._run_entries(r, full, None)
            row = self.plans.get(pid) or {}
            if row.get("status") == "failed":
                failed = True
                entries.append(_note("plan_failed", row.get("error"), row.get("updated_at"), pid))
        if self.status == "failed" and not self.task_ids and not failed:
            entries.append(_note("plan_failed", self.job.get("error"), self.job.get("updated_at"),
                                 self.plan_ids[-1] if self.plan_ids else ""))
        entries += [e for e in map(_event_entry, (e for e in self.events if e.get("action") in ("approve", "replan")))
                    if e]
        planner = runs[-1]["pool"] if runs else None
        if planner is None and self.proj is not None:
            planner = (self.proj.roles().get("plan") or {}).get("pool")
        return {"key": "plan", "title": "", "state": state,
                "summary": {"rounds": len(self.plan_ids), "approved_at": self.approved_at(),
                            "planner": self.agent(planner)},
                "entries": entries}

    def _task_section(self, tid: str, cur: str | None) -> dict | None:
        t, runs, logs = self.tasks[tid], self.runs_by.get(tid, []), self.logs_by.get(tid, [])
        events = [e for e in self.events if _s(e.get("task_id")) == tid]
        n = self.task_now(tid)
        notes = self._log_entries(tid, logs, events)
        if not runs and not events and not notes and not n and not (self.active and tid == cur):
            return None
        st = self.status_of(tid)
        if n:
            state = "live"
        elif st in OPEN_END:
            state = "done"
        elif st in ("verified", "blocked", "failed"):
            state = "waiting"
        elif st == "queued" and self.active and tid == cur:
            state = "live"
        else:
            state = "stopped"
        full = state in ("live", "waiting")
        entries = []
        for r in runs:
            entries += self._run_entries(r, full, n)
        entries += notes
        entries += [e for e in map(_event_entry, events) if e]
        work = self.work(tid)
        lw = work[-1] if work else None
        return {"key": tid, "title": _s(t.get("title")), "index": self.index[tid], "state": state,
                "summary": {"agent": self.agent(lw["pool"] if lw else
                                                self.predicted(_s(t.get("type")), _s(t.get("difficulty")))[0]),
                            "files": len(self.files(tid)), "tests": self.tests(lw),
                            "review_rounds": sum(1 for r in runs
                                                 if r["kind"] == "review" and r.get("verified") is not None),
                            "result": RESULTS.get(st, st)},
                "entries": entries}

    def _run_entries(self, r: dict, full: bool, now: dict | None) -> list[dict]:
        """A run's bubble, plus Crossview's acceptance check after a build or fix."""
        try:
            out = [self._run_entry(r, full)]
            if r["kind"] == "work" and r["role"] != "conflict":
                verify = {"type": "verify", "task_id": r["task_id"], "run_id": r["id"], "at": _f(r.get("ended_at")),
                          "commands": self.commands(r["task_id"])}
                if r.get("verified") is not None:
                    out.append({**verify, "ok": r["verified"], "live": False, "tests": self.run_tests(r)})
                elif now and now["kind"] == "verify" and now["run_id"] == r["id"]:
                    out.append({**verify, "ok": None, "live": True, "tests": None})
            return out
        except Exception as e:
            traceback.print_exc()
            return [_note("orch_error", f"{type(e).__name__}: {e}", r.get("started_at"), r["task_id"])]

    def _run_entry(self, r: dict, full: bool) -> dict:
        vis = self.items(r)
        sent = vis[-LIVE_ITEMS:] if full else []
        status = _s(r.get("status"))
        if status == "running" and not r["live"]:
            status = "interrupted"   # nobody is running it any more
        verdict = self.verdict(r)
        final = self.snap(r)[3]
        if r["kind"] == "plan":
            text = self._plan_text(r, final)
        elif r["kind"] == "review":
            text = verdict["summary"] if verdict and verdict["result"] != "unreadable" and verdict["summary"] else \
                _strip_fences(r.get("summary") or final)
        else:
            text = _s(r.get("summary")) or (final if r["live"] else "")
        error = "" if status in ("running", "completed") else (_s(r.get("error")).strip()[-300:] or status)
        return {"type": "run", "role": r["role"], "run_id": r["id"], "task_id": r["task_id"],
                "agent": self.agent(r["pool"]), "at": _f(r.get("started_at")),
                "end": None if r["live"] else _f(r.get("ended_at")), "live": r["live"], "status": status,
                "mode": r["mode"], "text": _clip(text, 1500), "activity": [_row(it) for it in sent],
                "hidden": len(vis) - len(sent), "verdict": verdict, "tokens": self.run_tokens(r),
                "cost_usd": round(self.run_cost(r), 6), "billing": self.billing(r["pool"]), "error": error}

    def _plan_text(self, r: dict, final: str) -> str:
        row = self.plans.get(r["task_id"]) or {}
        done = [x for x in self.runs_by.get(r["task_id"], []) if x.get("status") == "completed"]
        if row.get("status") in ("draft", "approved", "rejected") and done and done[-1] is r:
            summary = _s(self.plan_data(r["task_id"]).get("summary"))
            if summary:
                return summary
        return _strip_fences(r.get("summary") or final)

    def _log_entries(self, tid: str, logs: list[dict], events: list[dict]) -> list[dict]:
        """orch's own log rows for the item, as notes and merged rows."""
        out, prev = [], None
        dropped = any(e.get("action") == "discard" for e in events)   # the user's decision already says it
        simple = {"task_blocked": "blocked", "task_failed": "failed", "recovered": "interrupted",
                  "merge_failed": "merge_failed", "escalate": "escalated", "orch_error": "orch_error"}
        pools = set(self.proj.cfg.pools) if self.proj is not None and self.proj.cfg is not None else None
        for row in logs:
            kind, msg, ts = row.get("kind"), _s(row.get("message")), row.get("ts")
            if kind == "escalate":   # "<pool> -> <next>": no next AI means the blocked note that follows says it all
                target = msg.rpartition("->")[2].strip() if "->" in msg else ""
                if target.lower() in NO_TARGET or (pools is not None and target not in pools):
                    prev = row
                    continue
            if kind == "merged":
                out.append({"type": "merged", "task_id": tid, "at": _f(ts),
                            "sha": "" if msg == "nothing to merge" else msg})
            elif kind == "review_skipped" or (kind == "task_verified" and "review skipped" in msg
                                              and (prev or {}).get("kind") != "review_skipped"):
                out.append(_note("review_skipped", msg, ts, tid))
            elif kind in simple:
                out.append(_note(simple[kind], msg, ts, tid))
            elif kind == "task_cancelled" and not dropped:
                out.append(_note("cancelled", msg, ts, tid))
            prev = row
        return out

    def _place(self, secs: list[dict]) -> None:
        """Decisions about the whole job (pause, cancel, ...) go where the job was at that moment."""
        loose = []
        for e in self.events:
            if e.get("action") in ("approve", "replan") or _s(e.get("task_id")) in self.index:
                continue
            entry = _event_entry(e)
            if entry:
                loose.append(entry)
        at = _f(self.job.get("updated_at"))
        if self.status == "interrupted" and not self.active:
            loose.append(_note("interrupted", self.job.get("error"), at))
        elif self.status == "failed" and self.task_ids and not self.active:
            loose.append(_note("orch_error", self.job.get("error"), at))
        starts = [min((e["at"] for e in s["entries"] if e.get("at") is not None), default=None) for s in secs]
        for entry in loose:
            target = secs[0]
            for s, start in zip(secs[1:], starts[1:]):
                if start is not None and start <= (entry.get("at") or 0):
                    target = s
            target["entries"].append(entry)


# --- the public functions --------------------------------------------------------------------------

def _sig(job: dict) -> str:
    """Changes whenever anything the job view shows may have changed (cheap: a few small queries and stats)."""
    tasks, plans = _job_ids(job)
    path = _s(job.get("path"))
    parts: list = [VERSION, job.get("id"), job.get("status"), job.get("updated_at"), bool(job.get("active")),
                   bool(job.get("cancelling")),
                   bool(job.get("mine")), bool(job.get("can_act")), job.get("owner"), job.get("title"),
                   job.get("stop_mode"), job.get("error"), job.get("project"), tuple(tasks), tuple(plans),
                   json.dumps(job.get("pending") or [], sort_keys=True, default=str, ensure_ascii=False),
                   json.dumps(job.get("events") or [], sort_keys=True, default=str, ensure_ascii=False)[-2000:],
                   len(job.get("events") or []), _stat(Path(path) / ".agents" / "orch.toml") if path else None,
                   json.dumps(job.get("land"), sort_keys=True, default=str, ensure_ascii=False)]
    con = _connect(path) if path else None
    if con is None:
        parts.append(("no records", bool(path) and os.path.isdir(path)))
    else:
        try:
            parts.append(con.execute("SELECT MAX(id) FROM log").fetchone()[0])
            ids = tasks + plans
            if ids:
                marks = ",".join("?" * len(ids))
                parts.append(tuple(con.execute(
                    "SELECT COUNT(*), SUM(status != 'running'), SUM(verified IS NOT NULL), MAX(ended_at), "
                    f"MAX(started_at), SUM(COALESCE(tokens_in, 0)) FROM runs WHERE task_id IN ({marks})",
                    ids).fetchone()))
                live = f"SELECT id, events_path FROM runs WHERE status='running' AND task_id IN ({marks})"
                for r in con.execute(live, ids):
                    parts.append((r["id"], _stat(r["events_path"]) if r["events_path"] else None))
            if tasks:
                tm = ",".join("?" * len(tasks))
                parts.append(tuple(con.execute(f"SELECT COUNT(*), MAX(updated_at) FROM tasks WHERE id IN ({tm})",
                                               tasks).fetchone()))
            if plans:
                pm = ",".join("?" * len(plans))
                parts.append(tuple(con.execute(f"SELECT COUNT(*), MAX(updated_at) FROM plans WHERE id IN ({pm})",
                                               plans).fetchone()))
        except sqlite3.Error as e:
            parts.append(("db error", str(e)))
        finally:
            con.close()
    return hashlib.sha1(repr(parts).encode("utf-8", "replace")).hexdigest()[:16]


def _fallback_view(job: dict, e: Exception) -> dict:
    """What is left to show when building the view failed outright (a bug): the job itself."""
    j = _Job.__new__(_Job)
    j.job, j.status, j.active, j.error = job, _s(job.get("status")), bool(job.get("active")), ""
    j.cancelling = j.active and bool(job.get("cancelling"))
    j.stop_mode = _s(job.get("stop_mode")) or "each"
    part = j.job_part()
    part["error"] = part["error"] or f"{type(e).__name__}: {e}"[:300]
    key = "done" if j.status == "done" else "stopped"
    return {"job": part, "stage": {"key": key, "step": "done" if key == "done" else "plan",
                                   "plan_gate": {"state": "todo", "at": None},
                                   "merge_gate": {"state": "todo", "at": None}, "item": None},
            "now": None, "plan": None, "tasks": [], "sections": [], "checkpoints": [],
            "usage": {"tokens": 0, "cost_usd": 0.0, "api_runs": 0, "subscription_runs": 0, "by_agent": []}, "team": []}


def view(job: dict, sig: str = "") -> dict:
    """JobView (GET /api/job), or {"same": True, "sig"} when nothing changed since the client's sig."""
    s = _part(lambda: _sig(job), "")
    if sig and s and sig == s:
        return {"same": True, "sig": s}
    key = (job.get("id"), _s(job.get("path")), s)
    if s:
        hit = _VIEWS.get(key)
        if hit is not _MISS:
            return hit
    try:
        j = _Job(job)
        v = {"job": j.job_part(), "stage": _part(j.stage, None), "now": _part(j.now, None),
             "plan": _part(j.plan_view, None), "tasks": _part(j.tasks_view, []), "sections": _part(j.sections, []),
             "checkpoints": _part(j.checkpoints, []),
             "usage": _part(j.usage, {"tokens": 0, "cost_usd": 0.0, "api_runs": 0, "subscription_runs": 0,
                                      "by_agent": []}),
             "team": _part(j.team, []), "result": _part(j.result, None)}
        if v["stage"] is None:
            v["stage"] = _fallback_view(job, RuntimeError())["stage"]
        ok = True
    except Exception as e:
        traceback.print_exc()
        v, ok = _fallback_view(job, e), False
    v["sig"] = s
    _clean(v)
    return _VIEWS.put(key, v) if s and ok else v


def _brief(j: _Job) -> dict:
    job, stage, now = j.job, j.stage(), j.now()
    cps = j.checkpoints(light=True)
    waiting = next((k for k in ("plan", "merge", "attention", "stopped") if k in {c["kind"] for c in cps}), None)
    cur = j.current()
    if waiting in ("merge", "attention"):   # the headline names the item that needs the user, not one at work
        cur = next((c["task_id"] for c in cps if c["kind"] == waiting and c.get("task_id") in j.tasks), cur)
    return {"id": job.get("id"), "project_id": job.get("project_id") or 0, "project": _s(job.get("project")),
            "title": _s(job.get("title")), "goal": _clip(job.get("goal"), 300), "status": j.status,
            "stop_mode": j.stop_mode, "created_at": _f(job.get("created_at")), "updated_at": _f(job.get("updated_at")),
            "mine": bool(job.get("mine")), "owner": _s(job.get("owner")), "stage": stage["key"], "item": stage["item"],
            "progress": j.progress(),
            "now": {k: now[k] for k in ("agent", "kind", "target", "live", "since")} if now else None,
            "waiting": waiting, "cancelling": j.cancelling,
            "summary_task": _s(j.tasks[cur].get("title")) if cur else None, "recent": j.recent(),
            "merged": sum(j.status_of(t) == "merged" for t in j.order)}   # a dropped job may have merged some items


def brief(job: dict) -> dict:
    """JobBrief: one job on a card or in a list (waiting is what Jobs counts for the badge)."""
    try:
        if job.get("status") in FINAL and not job.get("active"):
            key = ("final", job.get("id"), _s(job.get("path")), job.get("status"), job.get("updated_at"),
                   bool(job.get("mine")), job.get("owner"), job.get("title"), job.get("project"))
        else:
            key = ("open", job.get("id"), _s(job.get("path")), _sig(job))
        hit = _BRIEFS.get(key)
        if hit is not _MISS:
            return hit
        return _BRIEFS.put(key, _clean(_brief(_Job(job))))
    except Exception:
        traceback.print_exc()
        status = _s(job.get("status"))
        return _clean({"id": job.get("id"), "project_id": job.get("project_id") or 0, "project": _s(job.get("project")),
                       "title": _s(job.get("title")), "goal": _clip(job.get("goal"), 300), "status": status,
                       "stop_mode": _s(job.get("stop_mode")) or "each", "created_at": _f(job.get("created_at")),
                       "updated_at": _f(job.get("updated_at")), "mine": bool(job.get("mine")),
                       "owner": _s(job.get("owner")), "stage": "done" if status == "done" else "stopped", "item": None,
                       "progress": [0.0, 0.0, 0.0, 0.0], "now": None, "cancelling": False,
                       "waiting": {"plan_ready": "plan", "waiting": "merge"}.get(status), "summary_task": None,
                       "recent": None, "merged": 0})


def run_detail(job: dict, run_id: str) -> dict:
    """GET /api/job/run: one run's full record (rows with their output, summary, acceptance log, review)."""
    j = _Job(job)
    r = next((x for x in j.runs if x["id"] == run_id), None)
    if r is None:
        raise ValueError(NO_RUN)
    tdir = Path(j.path) / ".agents" / "tasks" / r["task_id"]
    verify = ""
    if r["role"] == "conflict":
        conflicts = [x for x in j.runs_by.get(r["task_id"], []) if x["role"] == "conflict"]
        verify = _text(tdir / "conflict_verify.log", 20000) if conflicts and conflicts[-1] is r else ""
    elif r["kind"] == "work":
        p = tdir / "runs" / f"{r['id']}.verify.log"
        done = [x for x in j.work(r["task_id"]) if x.get("verified") is not None]
        if not p.is_file() and done and done[-1] is r:
            p = tdir / "verify.log"
        verify = _text(p, 20000)
    status = _s(r.get("status"))
    return _clean({"run_id": r["id"], "task_id": r["task_id"], "kind": r["kind"], "role": r["role"], "mode": r["mode"],
                   "agent": j.agent(r["pool"]), "live": r["live"],
                   "status": "interrupted" if status == "running" and not r["live"] else status,
                   "items": [_row(it, output=True) for it in j.items(r)],
                   "summary": (_s(r.get("summary")) or j.snap(r)[3])[-8000:], "error": _s(r.get("error"))[-4000:],
                   "verify_log": verify,
                   "review_md": _text(tdir / "runs" / f"{r['id']}.review.md") if r["kind"] == "review" else ""})


def diff(job: dict, task_id: str) -> dict:
    """GET /api/job/diff: what the item's branch changes against the integration branch."""
    if task_id not in _job_ids(job)[0]:
        raise ValueError(NOT_OURS)
    out = {"task_id": task_id, "files": [], "patch": "", "truncated": False}
    path = _s(job.get("path"))
    con = _connect(path)
    if con is None:
        return out
    try:
        row = con.execute("SELECT status, updated_at, branch FROM tasks WHERE id=?", (task_id,)).fetchone()
    except sqlite3.Error:
        row = None
    finally:
        con.close()
    if row is None or not row["branch"]:
        return out
    proj, t = _project(path), dict(row)
    out["files"] = [dict(f) for f in _numstat(proj, task_id, t)]
    out["patch"], out["truncated"] = _patch(proj, task_id, t)
    return _clean(out)


def merge_problem(path: str, task_id: str) -> tuple[str, str]:
    """("conflict"|"tests_failed"|"error", raw detail) of a merge that failed after the item's last work run."""
    con = _connect(path)
    if con is None:
        return "", ""
    try:
        since = con.execute("SELECT MAX(started_at) FROM runs WHERE task_id=? AND kind='work'",
                            (task_id,)).fetchone()[0]
        rows = con.execute("SELECT ts, message FROM log WHERE task_id=? AND kind='merge_failed' ORDER BY id",
                           (task_id,)).fetchall()
    except sqlite3.Error:
        return "", ""
    finally:
        con.close()
    return _problem([(r["ts"], r["message"]) for r in rows], _f(since) or 0)


def revise_text_for_merge(path: str, task_id: str) -> str:
    """What the agent is told when the user only says 让它修一下 after a failed merge ('' when none failed)."""
    code, detail = merge_problem(path, task_id)
    if not code:
        return ""
    if code == "tests_failed":
        text = _text(Path(path) / ".agents" / "tasks" / task_id / "merge_verify.log", 3000).strip()
    else:
        text = ""
        con = _connect(path)
        if con is not None:
            try:
                row = con.execute("SELECT note FROM tasks WHERE id=?", (task_id,)).fetchone()
                text = _s(row["note"]) if row else ""
            except sqlite3.Error:
                pass
            finally:
                con.close()
    return MERGE_FIX + (text or detail)


def _team(p: Path) -> dict:
    try:
        here = p.is_dir()
    except OSError:
        here = False
    if not here:
        return {"team": [], "roles": dict(NO_ROLES), "problem": NO_FOLDER}
    proj = _project(p)
    if proj.cfg is None:
        return {"team": [], "roles": dict(NO_ROLES), "problem": proj.problem or NO_CONFIG}
    now, pools = time.time(), proj.pools()
    closed, running, today, quota = {}, set(), {}, {}
    con = _connect(p)
    if con is not None:
        try:
            for r in con.execute("SELECT pool, open_until, reason FROM breakers"):
                if (_f(r["open_until"]) or 0) > now:
                    closed[r["pool"]] = _s(r["reason"])
            for r in con.execute("SELECT pool, pid FROM runs WHERE status='running'"):
                if not r["pid"] or pid_alive(r["pid"]):
                    running.add(r["pool"])
            midnight = time.mktime(time.localtime(now)[:3] + (0, 0, 0, 0, 0, -1))
            for r in con.execute("SELECT pool, SUM(COALESCE(est_cost_usd, 0)) AS cost, SUM(COALESCE(tokens_in, 0) + "
                                 "COALESCE(tokens_out, 0)) AS tokens FROM runs WHERE started_at >= ? GROUP BY pool",
                                 (midnight,)):
                today[r["pool"]] = (_f(r["cost"]) or 0.0, _n(r["tokens"]))
            for pool in pools:
                r = con.execute("SELECT quota FROM runs WHERE pool=? AND quota IS NOT NULL AND quota != '' "
                                "ORDER BY started_at DESC LIMIT 1", (pool,)).fetchone()
                quota[pool] = _quota(r["quota"], now) if r else None
        except sqlite3.Error:
            pass
        finally:
            con.close()
    team = []
    for pool in pools:
        a = proj.adapter(pool)
        available = bool(a and a["available"])
        billing = proj.billing(pool)
        if pool in closed:
            state = "paused" if closed[pool] == "paused" else "cooling"
        else:
            state = "working" if pool in running else "idle" if available else "missing"
        cost, tokens = today.get(pool, (0.0, 0))
        team.append({"agent": proj.agent(pool), "billing": billing, "available": available, "state": state,
                     "quota": quota.get(pool), "today_cost_usd": round(cost, 6) if billing == "api" else 0.0,
                     "today_tokens": tokens})
    return {"team": team, "roles": proj.roles(closed=set(closed)), "problem": ""}


def project_team(path: str) -> dict:
    """GET /api/project's team strip and who Crossview would pick for each step now: {"team", "roles", "problem"}."""
    key = str(path)
    hit = _TEAMS.get(key)
    if hit is not _MISS and time.monotonic() - hit[0] < TEAM_TTL:
        return hit[1]
    try:
        team = _clean(_team(Path(path)))
    except Exception:
        traceback.print_exc()
        team = {"team": [], "roles": dict(NO_ROLES), "problem": NO_CONFIG}
    return _TEAMS.put(key, (time.monotonic(), team))[1]
