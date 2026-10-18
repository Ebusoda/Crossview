"""One launch of a discussion (maoweb/discuss.py) in its own process: runs the queued steps until the owner is
needed (orch/discuss.py Engine), read-only through orch/discuss_call.py.

stdin: one JSON payload {root, did, parent_pid, timeout_s, team}. stdout: JSON lines {"event": ...}: log,
step_started {step, side, pid}, turn {turn_id, side, step, status}, stage {n}, finished {status: waiting|approved},
failed {code: step_failed|worker_error, detail}. The team file (tests only) comes from the payload, never from this
process's environment. A stop (SIGTERM on POSIX) or the web server going away sets orch.proc.STOP: the CLI is
stopped, the finished turns stay saved and the unfinished step stays queued for 继续.
"""
from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
from pathlib import Path

from orch import discuss, discuss_call
from orch.proc import STOP, pid_alive

_closed = threading.Event()   # stdout is gone: nobody reads what we say any more
_out = threading.Lock()        # the two sides of a group talk at the same time: one whole line each


def emit(event, **data):
    line = (json.dumps({"event": event, **data}, ensure_ascii=False, default=str) + "\n").encode("utf-8", "replace")
    with _out:
        if _closed.is_set():
            return
        try:
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()
        except (OSError, ValueError):
            _closed.set()
            STOP.set()


def watch(parent_pid):
    """Stop the work when the web server is gone."""
    while True:
        if _closed.is_set() or (parent_pid and not pid_alive(parent_pid)):
            STOP.set()
        time.sleep(1)


def run(payload) -> int:
    store = discuss.Store(Path(payload["root"]))
    store.say = lambda t: emit("log", text=str(t))
    team = discuss_call.load_team(payload.get("team") or None, env={})
    engine = discuss.Engine(store, payload["did"], discuss_call.adapter_caller(team),
                            say=lambda t: emit("log", text=str(t)), stop=STOP,
                            timeout_s=float(payload.get("timeout_s") or discuss.DEFAULT_TIMEOUT_S),
                            on_event=lambda kind, **d: emit(kind, **d))
    try:
        emit("finished", status=engine.run())
    except discuss.Interrupted:
        return 0
    except discuss.StepFailed:
        emit("failed", code="step_failed", detail="")     # side, step and status are in last_error
    return 0


def _terminated(signum, frame):
    STOP.set()   # no raise: the engine ends at its next check, run_streaming stops the CLI


def main() -> int:
    if os.name != "nt":
        signal.signal(signal.SIGTERM, _terminated)
    try:
        payload = json.load(sys.stdin)
        threading.Thread(target=watch, args=(int(payload.get("parent_pid") or 0),), daemon=True).start()
        return run(payload)
    except KeyboardInterrupt:
        return 0
    except Exception as exc:  # noqa: BLE001
        emit("failed", code="worker_error", detail=f"{type(exc).__name__}: {exc}"[:1000])
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
