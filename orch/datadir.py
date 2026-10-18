"""MAO's shared data folder (the web app's `.maoweb`) and small, safe helpers for the JSON files kept there.

The CLI, the MCP server, the web app and the Claude Code hook all read the same folder: MAO_WEB_DATA, else
<MAO folder>/.maoweb (gitignored, never published). Reads never create anything; writes are atomic (a temp file in
the same folder, then os.replace), so a reader in another process sees the old file or the new one, never half.
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

REPO = Path(__file__).resolve().parents[1]
ON = ("1", "on", "true", "yes")
OFF = ("0", "off", "false", "no")


def data_dir(environ: Mapping[str, str] | None = None) -> Path:
    """MAO_WEB_DATA, else <MAO folder>/.maoweb (the same folder as maoweb.db.data_dir()). Creates nothing."""
    env = (os.environ if environ is None else environ).get("MAO_WEB_DATA")
    return Path(env) if env else REPO / ".maoweb"


def flag(value: str | None) -> bool | None:
    """1/on/true/yes -> True, 0/off/false/no -> False, anything else (unset, empty, typos) -> None."""
    v = (value or "").strip().lower()
    return True if v in ON else False if v in OFF else None


def read_json(path: Path, default: Any) -> Any:
    """The file's JSON, or a copy of default when it is missing or not valid JSON. Writes nothing."""
    for attempt in range(3):
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except PermissionError:          # Windows: another process is replacing the file right now
            if attempt == 2:
                break
            time.sleep(0.05)
        except (OSError, ValueError):
            break
    return copy.deepcopy(default)


def write_json(path: Path, obj: Any) -> None:
    """Write obj as JSON atomically: a temp file in the same folder, then os.replace."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False, indent=1) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:      # Windows: a reader has the file open
                if attempt == 4:
                    raise
                time.sleep(0.05)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _lock(f) -> None:
    """Non-blocking exclusive OS lock (raises OSError when someone else holds it)."""
    if os.name == "nt":
        import msvcrt

        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(f) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


@contextmanager
def file_lock(path: Path, timeout: float = 2.0) -> Iterator[bool]:
    """Hold an OS lock on `path` (created if missing) for the with-block: yields True when held, False when
    another process or thread kept it for longer than `timeout` seconds (the caller decides what to do then)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + max(0.0, timeout)
    with open(path, "a+b") as f:
        held = False
        while True:
            try:
                _lock(f)
                held = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.05)
        try:
            yield held
        finally:
            if held:
                _unlock(f)
