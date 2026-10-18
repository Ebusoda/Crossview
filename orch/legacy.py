"""The legacy switch: Crossview's old task dispatch (派活) is off unless the owner turns it on.

The old dispatch is Crossview splitting work into tasks, handing them to coding agents and merging the results: the web
Projects page and 交给项目, the Agent page's control panel, the `task / run / plan ...` commands and the `orch_*` MCP
task tools. Development now goes to Claude Cowork, so all of that is off by default. Nothing is deleted: its data
stays, and one switch brings it back.

Where the switch lives: <data>/legacy.json = {"dispatch": bool, "changed_at": epoch, "changed_by": "web"|"cli"},
<data> being the web data folder (MAO_WEB_DATA, else <Crossview folder>/.maoweb). A missing or unreadable file
means off.
MAO_LEGACY_DISPATCH (1/on/true/yes or 0/off/false/no) overrides the file, for tests and emergencies; while it is
set, nobody can change the switch from the web or the CLI. The file is read on every call (no cache): the CLI can
flip it while the web app runs.
"""
from __future__ import annotations

import argparse
import os
import time
from pathlib import Path
from typing import Mapping

from . import datadir

ENV = "MAO_LEGACY_DISPATCH"
FILE = "legacy.json"

# top-level commands of `python -m orch`: each is in exactly one of these sets (tests/test_legacy.py checks it)
CLI_COMMANDS = frozenset({"init", "task", "run", "status", "report", "context", "memory", "merge", "agents",
                          "breaker", "plan", "decide", "route", "usage", "handoff", "handoff-test", "demo", "probe"})
STAY_ON = frozenset({"lang", "doctor", "key", "connect", "api", "sessions", "sheet", "doc", "trace", "find",
                     "office", "mcp", "legacy", "notify"})
# the MCP tools of the old dispatch (orch_sessions is monitoring and stays)
MCP_TOOLS = frozenset({"orch_list_projects", "orch_list_tasks", "orch_task", "orch_add_task", "orch_run",
                       "orch_report", "orch_context", "orch_merge", "orch_agents", "orch_pause", "orch_resume",
                       "orch_swap", "orch_retry", "orch_cancel"})

WEB_OFF = "旧的派活功能已关闭。管理员可以在 设置 → 旧功能 里打开。"
ENV_LOCKED = "现在由环境变量 MAO_LEGACY_DISPATCH 决定，这里改不了"
MCP_OFF = ("{name} belongs to Crossview's old task dispatch, which is off. The owner can turn it on in the Crossview "
           "web app (Settings > Legacy) or with: python -m orch legacy on")


def _env(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def state(data: Path | None = None, environ: Mapping[str, str] | None = None) -> dict:
    """{"enabled": bool, "source": "env" | "file" | "default"}. Reads only; creates no folder or file."""
    env = _env(environ)
    forced = datadir.flag(env.get(ENV))
    if forced is not None:
        return {"enabled": forced, "source": "env"}
    saved = datadir.read_json(Path(data or datadir.data_dir(env)) / FILE, None)
    if isinstance(saved, dict) and isinstance(saved.get("dispatch"), bool):
        return {"enabled": saved["dispatch"], "source": "file"}
    return {"enabled": False, "source": "default"}


def enabled(data: Path | None = None, environ: Mapping[str, str] | None = None) -> bool:
    return state(data, environ)["enabled"]


def set_enabled(on: bool, data: Path | None = None, environ: Mapping[str, str] | None = None,
                by: str = "cli") -> dict:
    """Turn the old dispatch on or off (writes legacy.json); ValueError(ENV_LOCKED) while the env decides."""
    env = _env(environ)
    if datadir.flag(env.get(ENV)) is not None:
        raise ValueError(ENV_LOCKED)
    datadir.write_json(Path(data or datadir.data_dir(env)) / FILE,
                       {"dispatch": bool(on), "changed_at": time.time(), "changed_by": by})
    return state(data, environ)


def gated(cmd: str, args: argparse.Namespace | None = None) -> bool:
    """This CLI command belongs to the old dispatch. `connect PROVIDER` does too: it only adds coding or review
    pools to .agents/orch.toml (plain `connect` lists what can be used and stays on)."""
    if cmd in CLI_COMMANDS:
        return True
    return cmd == "connect" and bool(getattr(args, "provider", None))
