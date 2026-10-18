"""Claude Code `Notification` hook: a phone alert when Claude Code waits for a permission (or, if the owner wants,
for input). Read-only: the hook only pushes; it never answers or approves anything.

`install()` adds one hook group to Claude Code's settings.json (<CLAUDE_CONFIG_DIR or ~/.claude>/settings.json),
after a backup, keeping the file's other hooks, indent, line endings and BOM; it is idempotent, and `uninstall()`
removes only MAO's entry. The entry is exec form (a command plus args, no shell), so neither the shell nor the
hook's working folder matters:

    python -X utf8 -P -c "import sys; sys.path.insert(0, '<MAO folder>'); from orch.notify_hook import main;
                          main('<MAO data folder>')" --mao-notify-hook

-P keeps the working folder off sys.path: Claude Code starts hooks in the project folder, and a `secrets.py`,
`json.py` or `email/` there must neither break the hook nor run on every permission prompt.

The interpreter is the owner's choice (`--python` with the full path of pythonw.exe stops a console window
flashing): status() without python= accepts any interpreter that still exists, and install() without python= keeps
it.

`main()` reads the event JSON from stdin, maps notification_type to an alert kind and calls notify.notify() with a
5 s send timeout. It always returns 0 and never writes to stdout: Claude Code hands an async hook's output to the
model on its next turn. The event's message, cwd and transcript_path are never used.
"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import BinaryIO, Callable

from .datadir import REPO

MARK = "--mao-notify-hook"
MATCHER = ("permission_prompt|idle_prompt|elicitation_dialog|elicitation_url_dialog|agent_needs_input"
           "|quota_auto_resume_stale")
KIND_BY_TYPE = {"permission_prompt": "cc_permission", "elicitation_dialog": "cc_permission",
                "elicitation_url_dialog": "cc_permission", "agent_needs_input": "cc_permission",
                "idle_prompt": "cc_input", "quota_auto_resume_stale": "cc_input"}
BUCKET_S = 300                 # at most one push per session and kind per 5 minutes
MAX_STDIN = 64 * 1024
STATES = ("installed", "outdated", "missing", "no_claude", "broken")

# Chinese errors for the web (maoweb/static/i18n.js has their English; the CLI catalog too)
NO_CLAUDE = "没有找到 Claude Code 的设置文件夹。先安装并打开一次 Claude Code。"
BAD_JSON = "Claude Code 的 settings.json 不是有效的 JSON，没有改动它。请先修好这个文件。"
BAD_SHAPE = "Claude Code 的 settings.json 里 hooks 的格式和预期不同，没有改动它。"
NO_PYTHON = "找不到要写进钩子的 Python 程序（python.exe 或 pythonw.exe 的完整路径）。"


def settings_path(claude: Path | str | None = None) -> Path:
    if claude is None:
        from .sessions import claude_dir

        claude = claude_dir()
    return Path(claude) / "settings.json"


def entry(data: Path | str, python: str | None = None) -> dict:
    """MAO's hook group for settings.json. ValueError(NO_PYTHON) when the interpreter is not an existing file."""
    py = sys.executable if python is None else str(python)
    if not py or not Path(py).is_file():
        raise ValueError(NO_PYTHON)
    return _group(os.path.abspath(py), data)


def _group(py: str, data: Path | str) -> dict:
    prog = (f"import sys; sys.path.insert(0, {str(REPO)!r}); from orch.notify_hook import main; "
            f"main({os.path.abspath(str(data))!r})")
    return {"matcher": MATCHER,
            "hooks": [{"type": "command", "command": py, "args": ["-X", "utf8", "-P", "-c", prog, MARK],
                       "async": True, "timeout": 30}]}


def _usable(cmd) -> bool:
    """An interpreter path that can stay in the hook: absolute and still there."""
    return isinstance(cmd, str) and bool(cmd) and os.path.isabs(cmd) and Path(cmd).is_file()


def _ours_in(cfg: dict) -> list[tuple[dict, dict]]:
    """(group, hook) for each of MAO's hook entries."""
    groups = (cfg.get("hooks") or {}).get("Notification") or []
    return [(g, h) for g in groups for h in (g.get("hooks") or []) if _ours(h)]


def _ours(hook) -> bool:
    if not isinstance(hook, dict):
        return False
    args = hook.get("args")
    return (isinstance(args, list) and MARK in args) or MARK in str(hook.get("command") or "")


def _read(path: Path) -> tuple[dict, dict]:
    """(settings, format). Raises ValueError(NO_CLAUDE / BAD_JSON / BAD_SHAPE)."""
    if not path.parent.is_dir():
        raise ValueError(NO_CLAUDE)
    fmt = {"bom": False, "crlf": False, "indent": 2, "raw": None}
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {}, fmt
    except OSError:
        raise ValueError(BAD_JSON) from None
    fmt["raw"] = raw
    fmt["bom"] = raw.startswith(b"\xef\xbb\xbf")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError(BAD_JSON) from None
    fmt["crlf"] = "\r\n" in text
    for line in text.splitlines()[1:]:
        m = re.match(r"^([ \t]+)\S", line)
        if m:
            ws = m.group(1)
            fmt["indent"] = "\t" if ws.startswith("\t") else len(ws)
            break
    if not text.strip():
        return {}, fmt
    try:
        cfg = json.loads(text)
    except ValueError:
        raise ValueError(BAD_JSON) from None
    if not isinstance(cfg, dict):
        raise ValueError(BAD_JSON)
    hooks = cfg.get("hooks")
    if hooks is not None:
        if not isinstance(hooks, dict):
            raise ValueError(BAD_SHAPE)
        groups = hooks.get("Notification")
        if groups is not None:
            if not isinstance(groups, list) or not all(isinstance(g, dict) and isinstance(g.get("hooks", []), list)
                                                       for g in groups):
                raise ValueError(BAD_SHAPE)
    return cfg, fmt


def _without_ours(cfg: dict) -> tuple[dict, bool]:
    """A copy without MAO's hook entries (groups emptied that way are dropped), and whether any were found."""
    new = copy.deepcopy(cfg)
    groups = (new.get("hooks") or {}).get("Notification")
    if not groups:
        return new, False
    found, kept = False, []
    for g in groups:
        hooks = g.get("hooks", [])
        left = [h for h in hooks if not _ours(h)]
        if len(left) != len(hooks):
            found = True
            if not left:
                continue
            g = {**g, "hooks": left}
        kept.append(g)
    new["hooks"]["Notification"] = kept
    return new, found


def _write(path: Path, cfg: dict, fmt: dict, now: float | None) -> str:
    """Back up the original (when there was one), then write cfg atomically in the original's format. Returns the
    backup's path ("" when there was no file)."""
    backup = ""
    if fmt["raw"] is not None:
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(time.time() if now is None else now))
        base = path.with_name(f"{path.name}.bak-{stamp}")
        cand, n = base, 1
        while cand.exists():
            n += 1
            cand = base.with_name(f"{base.name}-{n}")
        cand.write_bytes(fmt["raw"])
        backup = str(cand)
    text = json.dumps(cfg, indent=fmt["indent"], ensure_ascii=False) + "\n"
    if fmt["crlf"]:
        text = text.replace("\n", "\r\n")
    data = (b"\xef\xbb\xbf" if fmt["bom"] else b"") + text.encode("utf-8")
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        if fmt["raw"] is not None:
            try:
                shutil.copymode(path, tmp)    # keep the file's permissions (a temp file starts private)
            except OSError:
                pass
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:   # Windows: Claude Code is reading the file right now
                if attempt == 4:
                    raise
                time.sleep(0.05)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
    return backup


def status(claude: Path | str | None = None, data: Path | str | None = None, python: str | None = None) -> str:
    """installed | outdated (ours, but not what install() would write now: MAO or its data folder moved, the
    interpreter is gone, or an older hook) | missing | no_claude (Claude Code's folder is absent) | broken
    (settings.json cannot be read as expected). Without python=, any interpreter that still exists counts (the owner
    may have installed it with pythonw.exe); with python=, it must be that one."""
    path = settings_path(claude)
    try:
        cfg, _ = _read(path)
    except ValueError as e:
        return "no_claude" if str(e) == NO_CLAUDE else "broken"
    ours = _ours_in(cfg)
    if not ours:
        return "missing"
    if data is None:
        from .datadir import data_dir

        data = data_dir()
    if len(ours) != 1:
        return "outdated"
    (group, hook), = ours
    if python is None:              # any interpreter that is still there (python.exe, pythonw.exe, another Python)
        want = _group(hook.get("command"), data)
        ok = _usable(hook.get("command"))
    else:
        try:
            want, ok = entry(data, python), True
        except ValueError:
            return "outdated"
    if ok and group.get("matcher") == want["matcher"] and hook == want["hooks"][0]:
        return "installed"
    return "outdated"


def install(data: Path | str | None = None, claude: Path | str | None = None, python: str | None = None,
            now: float | None = None) -> dict:
    """Add (or update) MAO's hook. {"changed": bool, "path": str, "backup": str}. Writes nothing when it is already
    there as it should be. Without python=, the interpreter already in MAO's hook is kept while it still exists
    (else sys.executable). ValueError(NO_CLAUDE / BAD_JSON / BAD_SHAPE / NO_PYTHON)."""
    if data is None:
        from .datadir import data_dir

        data = data_dir()
    path = settings_path(claude)
    cfg, fmt = _read(path)
    if python is None:
        python = next((h["command"] for _, h in _ours_in(cfg) if _usable(h.get("command"))), None)
    want = entry(data, python)
    new, _ = _without_ours(cfg)
    if not isinstance(new.get("hooks"), dict):                  # missing, or "hooks": null
        new["hooks"] = {}
    if not isinstance(new["hooks"].get("Notification"), list):  # missing, or "Notification": null
        new["hooks"]["Notification"] = []
    new["hooks"]["Notification"].append(want)
    if new == cfg:
        return {"changed": False, "path": str(path), "backup": ""}
    return {"changed": True, "path": str(path), "backup": _write(path, new, fmt, now)}


def uninstall(claude: Path | str | None = None, now: float | None = None) -> dict:
    """Remove MAO's hook (and an emptied Notification list and hooks object). Writes nothing when it was not
    there."""
    path = settings_path(claude)
    cfg, fmt = _read(path)
    new, found = _without_ours(cfg)
    if not found:
        return {"changed": False, "path": str(path), "backup": ""}
    if not new["hooks"]["Notification"]:
        del new["hooks"]["Notification"]
        if not new["hooks"]:
            del new["hooks"]
    return {"changed": True, "path": str(path), "backup": _write(path, new, fmt, now)}


def main(data: str | None = None, stdin: BinaryIO | None = None, sender=None, now: float | None = None,
         idle: Callable[[], float | None] | None = None) -> int:
    """The hook itself. Always returns 0; never writes to stdout."""
    try:
        stream = stdin
        if stream is None:
            if sys.stdin is None:          # pythonw.exe without handles
                return 0
            stream = getattr(sys.stdin, "buffer", sys.stdin)
        raw = stream.read(MAX_STDIN)
        if isinstance(raw, str):
            raw = raw.encode("utf-8", errors="replace")
        event = json.loads((raw or b"").decode("utf-8", errors="replace"))
        if not isinstance(event, dict):
            return 0
        kind = KIND_BY_TYPE.get(str(event.get("notification_type") or ""))
        if not kind:
            return 0
        sid8 = re.sub(r"[^0-9A-Za-z]", "", str(event.get("session_id") or ""))[:8] or "?"
        from . import notify
        from .datadir import data_dir

        t = time.time() if now is None else float(now)
        notify.notify(kind, f"cc:{sid8}:{kind}:{int(t // BUCKET_S)}", ref=sid8, at=t,
                      data=Path(data) if data else data_dir(), sender=sender, via="hook", now=t, timeout=5,
                      idle=idle if idle is not None else notify.idle_seconds)
    except Exception as e:  # noqa: BLE001  (the hook never breaks Claude Code)
        if sys.stderr is not None:
            try:
                print(f"MAO notify hook: {type(e).__name__}", file=sys.stderr)
            except Exception:  # noqa: BLE001
                pass
    return 0
