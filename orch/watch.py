"""The phone-alert watcher: looks at what may be waiting for the owner and hands it to orch/notify.py.

All of it is read-only: Crossview never clicks, approves or changes anything it watches. The sources:
a. discussions (the web app's rows): waiting for a decision, failed / interrupted, or quiet for too long;
b. Cowork briefs: blocker / question / done reports (the MCP server pushes those at once; this is the retry), and
   optionally a taken brief with no report for hours (a weak signal);
c. ChatGPT tasks from gptbridge that wait for the owner to press Send (skipped once Codex's log shows it was sent);
d. Claude Code hook events whose push failed (notify.retry_due(); the hook has no other source to retry from).

Only the web app runs the periodic loop (start_thread): it is the process that is always on. The loop is idle while
alerts are off. Each source is wrapped on its own, so one broken source never stops the others.
"""
from __future__ import annotations

import json
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import notify

STATE: dict[str, Any] = {"running": False, "last_tick": None, "error": ""}   # per process, shown on the page
ACTIVE = ("queued", "running", "pausing")
JOB_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")
GPT_EXPIRE_S = 30 * 60          # gptbridge itself calls a job expired after this (core.EXPIRE_AFTER)
REPORT_KINDS = ("blocker", "question", "done")
LOG_EVERY_S = 3600              # one stderr line per distinct error per hour


@dataclass(frozen=True)
class Event:
    kind: str
    key: str
    at: float
    ref: str = ""
    extra: dict = field(default_factory=dict)


def _num(v: Any) -> float:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0


# -- a. discussions ----------------------------------------------------------------------------------------
def discussion_events(rows: list[dict], store, cfg: dict, now: float) -> list[Event]:
    """rows = Discussions.watch_rows() (did, status, error, updated_at, ended_at)."""
    from . import discuss

    stuck = int(cfg["thresholds"]["stuck_minutes"])
    out: list[Event] = []
    for r in rows or []:
        did, status = str(r.get("did") or ""), str(r.get("status") or "")
        updated, ended = _num(r.get("updated_at")), _num(r.get("ended_at"))
        if not did:
            continue
        if status == "waiting":
            try:
                n = int(store.summary(did)["last_stage_n"])
            except (discuss.DiscussError, OSError, KeyError, TypeError, ValueError):
                continue
            out.append(Event("discuss_waiting", f"ds:{did}:waiting:{n}", updated, did, {"n": n}))
        elif status == "failed" or (status == "interrupted" and r.get("error") == "lost"):
            if not ended:      # ended before this version recorded ended_at
                continue
            out.append(Event("discuss_failed", f"ds:{did}:ended:{int(ended)}", ended, did))
        elif status in ACTIVE and updated and now - updated >= stuck * 60:
            out.append(Event("discuss_stuck", f"ds:{did}:stuck:{int(updated)}", updated + stuck * 60, did,
                             {"minutes": stuck}))
    return out


# -- b. Cowork briefs --------------------------------------------------------------------------------------
def brief_events(store, cfg: dict, now: float) -> list[Event]:
    from . import discuss

    events, hours = cfg["events"], int(cfg["thresholds"]["cowork_silent_hours"])
    out: list[Event] = []
    for s in store.list(limit=50):
        if s.get("brief_state") not in ("taken", "done"):
            continue
        did = str(s.get("id") or "")
        try:
            b = store.load(did).get("brief") or {}
        except (discuss.DiscussError, OSError, ValueError):
            continue
        reports = [r for r in b.get("reports") or [] if isinstance(r, dict)]
        if events.get("cowork"):
            for r in reports:
                if r.get("kind") in REPORT_KINDS and r.get("id"):
                    out.append(Event(f"cowork_{r['kind']}", f"brief:{did}:{r['id']}", _num(r.get("at")), did))
        if events.get("cowork_silent") and b.get("state") == "taken":
            last = max([_num(b.get("taken_at"))] + [_num(r.get("at")) for r in reports])
            if last and now - last >= hours * 3600:
                out.append(Event("cowork_silent", f"brief:{did}:silent:{int(last)}", last + hours * 3600, did,
                                 {"hours": hours}))
    return out


# -- c. ChatGPT tasks (gptbridge) --------------------------------------------------------------------------
def gpt_events(cfg: dict, now: float, folder: Path | None = None, done: frozenset[str] = frozenset()) -> list[Event]:
    """Jobs waiting for the owner to press Send. Reads the job files with plain json (never list_jobs() or
    refresh(), which write) and Codex's logs through gptbridge.core.peek() (read-only)."""
    try:
        from gptbridge import core
    except ImportError:      # this copy of Crossview has no GPT bridge
        return []
    folder = Path(folder) if folder else core.jobs_dir()
    if not folder.is_dir():
        return []
    wait = int(cfg["thresholds"]["gpt_wait_minutes"])
    files = sorted((f for f in folder.glob("*.json") if JOB_RE.match(f.stem)), reverse=True)[:50]
    out: list[Event] = []
    for f in files:
        try:
            job = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(job, dict) or job.get("status") != "waiting_for_send":
            continue
        started = _num(job.get("started_at"))
        if not (started and wait * 60 <= now - started < GPT_EXPIRE_S):
            continue
        key = f"gpt:{f.stem}"
        if key in done:
            continue
        try:
            if core.peek(job, now):   # Codex's log already has it: the owner pressed Send
                continue
        except Exception:  # noqa: BLE001  (a log that cannot be read does not hide the job)
            pass
        out.append(Event("gpt_waiting", key, started + wait * 60, f.stem[-6:]))
    return out


# -- d. Claude Code hook retries ---------------------------------------------------------------------------
def hook_retries(data: Path, now: float) -> list[Event]:
    return [Event(r["kind"], r["key"], r["at"], r["ref"], r["extra"]) for r in notify.retry_due(data, now)]


# -- one look, and the loop --------------------------------------------------------------------------------
def _tick(data: Path, *, rows: list[dict] | None, store, now: float | None, sender, via: str,
          gpt_folder: Path | None, idle) -> tuple[list[dict], list[str]]:
    now = time.time() if now is None else float(now)
    cfg = notify.load(data)
    if not cfg["enabled"]:
        return [], []
    ev = cfg["events"]
    done = notify.done_keys(data)
    sources: list[tuple[str, Callable[[], list[Event]]]] = []
    if ev["discussion"] and rows is not None and store is not None:
        sources.append(("discussions", lambda: discussion_events(rows, store, cfg, now)))
    if (ev["cowork"] or ev["cowork_silent"]) and store is not None:
        sources.append(("briefs", lambda: brief_events(store, cfg, now)))
    if ev["gpt"]:
        sources.append(("chatgpt", lambda: gpt_events(cfg, now, gpt_folder, frozenset(done))))
    sources.append(("claude code", lambda: hook_retries(data, now)))
    events: list[Event] = []
    errors: list[str] = []
    for name, fn in sources:
        try:
            events += fn()
        except Exception as e:  # noqa: BLE001  (one broken source never stops the others)
            errors.append(f"{name}: {type(e).__name__}: {e}"[:300])
    out, seen = [], set()
    kw = {"idle": idle} if idle is not None else {}
    for e in events:
        if e.key in done or e.key in seen:
            continue
        seen.add(e.key)
        r = notify.notify(e.kind, e.key, ref=e.ref, at=e.at, extra=e.extra, data=data, sender=sender, via=via,
                          now=now, **kw)
        out.append({"kind": e.kind, "key": e.key, "ref": e.ref, "sent": r["sent"], "reason": r["reason"]})
    return out, errors


def tick(data: Path, *, rows: list[dict] | None = None, store=None, now: float | None = None, sender=None,
         via: str = "web", gpt_folder: Path | None = None,
         idle: Callable[[], float | None] | None = None) -> list[dict]:
    """Look once: [{"kind", "key", "ref", "sent", "reason"}] for every event that is not done yet."""
    return _tick(Path(data), rows=rows, store=store, now=now, sender=sender, via=via, gpt_folder=gpt_folder,
                 idle=idle)[0]


def loop(data: Path, stop: threading.Event, *, rows_fn: Callable[[], list[dict]] | None = None, store=None,
         interval: float = 60.0, sender=None, via: str = "web") -> None:
    """tick() every `interval` seconds until stop is set. Never raises; errors go to STATE and, once an hour each,
    to stderr."""
    logged: dict[str, float] = {}
    STATE.update(running=True, error="")
    try:
        while not stop.is_set():
            errors: list[str] = []
            try:
                rows = rows_fn() if rows_fn else None
            except Exception as e:  # noqa: BLE001
                rows = None
                errors.append(f"discussions: {type(e).__name__}: {e}"[:300])
            try:
                errors += _tick(Path(data), rows=rows, store=store, now=None, sender=sender, via=via,
                                gpt_folder=None, idle=None)[1]
            except Exception as e:  # noqa: BLE001
                errors.append(f"{type(e).__name__}: {e}"[:300])
            t = time.time()
            STATE.update(last_tick=t, error=errors[0] if errors else "")
            for err in errors:
                if t - logged.get(err, 0.0) >= LOG_EVERY_S:
                    logged[err] = t
                    if sys.stderr is not None:
                        try:
                            print(f"Crossview phone alerts: {err}", file=sys.stderr)
                        except Exception:  # noqa: BLE001
                            pass
            stop.wait(interval)
    finally:
        STATE["running"] = False


def start_thread(data: Path, *, rows_fn: Callable[[], list[dict]] | None, store, sender=None,
                 interval: float = 60.0) -> tuple[threading.Thread, threading.Event]:
    """The web app's watcher: a daemon thread named mao-watch. Set the returned event to stop it."""
    stop = threading.Event()
    thread = threading.Thread(target=loop, args=(Path(data), stop), name="mao-watch", daemon=True,
                              kwargs={"rows_fn": rows_fn, "store": store, "interval": interval, "sender": sender})
    thread.start()
    return thread, stop
