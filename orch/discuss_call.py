"""Model calls for discussions: always read-only, never with a tool that changes anything.

- Claude goes through the claude_code adapter in read-only mode: `--permission-mode dontAsk`, the tools
  Read/Glob/Grep only, `--strict-mcp-config` without any `--mcp-config` (no MCP servers), and
  `--no-session-persistence` when the installed CLI has it.
- GPT goes through the codex adapter with `--sandbox read-only`, plus overrides that switch off every MCP server,
  plugin and app codex would otherwise load (MCP calls are not sandboxed).
- The fake executor is only used through a team file (tests: MAO_DISCUSS_TEAM / tests/fixtures/discuss).

The prompt always goes on stdin, so nothing is ever written into the user's folder. A call without a project runs in
a fresh temp folder that is removed afterwards.
"""
from __future__ import annotations

import copy
import os
import re
import tempfile
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import config, discuss, proc
from .adapters import REGISTRY
from .adapters.base import Adapter, RunContext, iter_json
from .models import Task

INSTRUCTION = ("The assignment from Crossview is in the <stdin> block below. You are in read-only mode: "
               "do not create, edit or delete any file, do not run commands that change anything and do not call "
               "tools that change anything. Answer with the JSON object it asks for.")
EXECUTORS = {"gpt": "codex", "claude": "claude_code"}
VENDORS = {"gpt": "openai", "claude": "anthropic"}
CLIS = {"gpt": "codex", "claude": "claude"}
READ_TOOLS = ("Read", "Glob", "Grep")
MCP_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
CAPS_TTL = 600.0
EFFORT_CHOICES = ["low", "medium", "high"]
SUGGEST = {"gpt": ["gpt-6.1-sol"], "claude": ["sonnet", "opus"]}


@dataclass
class CallSpec:
    did: str
    step_id: str
    side: str
    prompt: str
    cwd: Path
    events_path: Path
    timeout_s: float
    model: str = ""
    effort: str = ""
    project: Path | None = None


@dataclass
class CallResult:
    status: str                 # RunStatus value: completed | timeout | rate_limited | auth_error | unavailable | crashed | task_failed | interrupted
    text: str = ""
    tokens_in: int | None = None
    tokens_out: int | None = None
    tokens_cached: int | None = None
    est_cost_usd: float | None = None
    quota: dict | None = None
    resets_at: float | None = None
    error: str = ""
    seconds: float = 0.0
    tool_calls: list[str] = field(default_factory=list)


Caller = Callable[[CallSpec, Callable[[int], None]], CallResult]     # 2nd arg: on_start(pid)


# -- team -------------------------------------------------------------------------------------------------------
def load_team(path: str | Path | None = None, env=os.environ) -> dict | None:
    """The fake team for tests: [pools.gpt] / [pools.claude] with executor, script and vendor (nothing else is read).
    None when neither `path` nor MAO_DISCUSS_TEAM is set."""
    p = path or (env.get("MAO_DISCUSS_TEAM") if env is not None else "") or ""
    if not p:
        return None
    p = Path(p).resolve()
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ValueError(f"cannot read the discussion team file {p}: {e}") from e
    pools = data.get("pools") if isinstance(data.get("pools"), dict) else {}
    out: dict = {"_dir": p.parent, "_path": p}
    for side in discuss.SIDES:
        pool = pools.get(side)
        if not isinstance(pool, dict):
            raise ValueError(f"the discussion team file has no [pools.{side}]")
        executor = str(pool.get("executor") or "")
        if executor not in (EXECUTORS[side], "fake"):
            raise ValueError(f"[pools.{side}] executor must be {EXECUTORS[side]!r} or 'fake', not {executor!r}")
        cfg: dict = {"executor": executor}
        if pool.get("script"):
            script = Path(str(pool["script"]))
            cfg["script"] = str(script if script.is_absolute() else (p.parent / script).resolve())
        if pool.get("vendor"):
            cfg["vendor"] = str(pool["vendor"])
        out[side] = cfg
    return out


def _executor(side: str, team: dict | None) -> str:
    return ((team or {}).get(side) or {}).get("executor") or EXECUTORS[side]


# -- the working folder -----------------------------------------------------------------------------------------
def guard_folder(folder: Path, store, is_temp: bool) -> str:
    """"" when a model may run in this folder, else the reason code."""
    folder = folder.resolve()
    for df in discuss.data_folders(store):
        if discuss.contains(df, folder) or discuss.contains(folder, df):
            return "unsafe_cwd"
    for parent in folder.parents:
        if (parent / "turns").is_dir():
            return "unsafe_cwd"
        if is_temp and (parent / ".git").exists():
            return "unsafe_cwd"
    return ""


def call_folder(state: dict, store) -> tuple[Path, bool]:
    """(folder, is_temp): the project folder, or a new temp folder for a discussion without a project. Raises
    StepFailed("unavailable", "unsafe_cwd") before anything starts when the folder is not safe."""
    proj = state.get("project")
    if proj and proj.get("path"):
        folder, is_temp = Path(proj["path"]).resolve(), False
        if not folder.is_dir():
            raise discuss.StepFailed("", "", "unavailable", "project_missing")
    else:
        folder, is_temp = Path(tempfile.mkdtemp(prefix="crossview-discuss-")).resolve(), True
    reason = guard_folder(folder, store, is_temp)
    if reason:
        if is_temp:
            import shutil

            shutil.rmtree(folder, ignore_errors=True)
        raise discuss.StepFailed("", "", "unavailable", reason)
    return folder, is_temp


# -- codex: no MCP servers, plugins or apps -----------------------------------------------------------------
def _read_toml(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        raise ValueError("codex_config_unreadable") from None


def _server_names(cfg: dict | None) -> set[str]:
    servers = (cfg or {}).get("mcp_servers")
    return {str(k) for k in servers} if isinstance(servers, dict) else set()


def _same_folder(a: str, b: Path) -> bool:
    try:
        return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(b))
    except (OSError, ValueError):
        return False


def _has(folder: Path, name: str) -> bool:
    try:
        p = folder / name
        return p.exists() or p.is_symlink()
    except (OSError, ValueError):
        return False


def _root_markers(cfg: dict | None) -> list[str]:
    """codex's `project_root_markers` (a list of names; [] stops the walk at the folder itself), else [".git"]."""
    m = (cfg or {}).get("project_root_markers")
    if isinstance(m, list) and all(isinstance(x, str) for x in m):
        return [x for x in m if x]
    return [".git"]


def _nearest(folder: Path, markers: list[str]) -> Path | None:
    """The folder itself or its nearest parent that holds one of `markers`."""
    if not markers:
        return None
    for d in (folder, *folder.parents):
        if any(_has(d, m) for m in markers):
            return d
    return None


def _main_checkout(checkout: Path) -> Path | None:
    """For a linked worktree (its `.git` is a file `gitdir: <repo>/.git/worktrees/<name>`), the main checkout's
    folder, where codex looks up the trust the worktree inherits. None for anything else."""
    dot = checkout / ".git"
    try:
        if not dot.is_file():
            return None
        lines = dot.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        if not lines or not lines[0].startswith("gitdir:"):
            return None
        admin = Path(lines[0][len("gitdir:"):].strip())
        admin = (admin if admin.is_absolute() else checkout / admin).resolve()
        if not admin.is_dir():
            return None
        common_file = admin / "commondir"
        if common_file.is_file():
            rel = common_file.read_text(encoding="utf-8", errors="replace").strip()
            common = (Path(rel) if Path(rel).is_absolute() else admin / rel).resolve()
        else:
            common = admin.parent.parent
        if common.name != ".git" or not common.is_dir():
            return None
        return common.parent
    except (OSError, ValueError):
        return None


def _layer_dirs(cwd: Path, root: Path | None) -> list[Path]:
    """The folders whose .codex/config.toml codex loads for a call in `cwd`: from cwd up to the project root."""
    if root is None or not discuss.contains(root, cwd):
        return [cwd]
    out = [cwd]
    for d in cwd.parents:
        if not discuss.contains(root, d):
            break
        out.append(d)
    return out


def codex_mcp_off_args(project: Path | None, codex_home: Path | None = None) -> list[str]:
    """`-c` overrides that switch off plugins, apps and every MCP server codex would load for this call: the ones
    in $CODEX_HOME/config.toml, and for a trusted project those in every .codex/config.toml from the project folder
    up to its project root, as codex finds them. The project root is the nearest folder with a root marker (`.git`
    unless `project_root_markers` says otherwise). The call counts as trusted when the folder, the project root, the
    git root or, for a linked worktree, the main checkout is marked trusted (codex inherits the trust of the git
    root and of the main checkout). Fails closed with ValueError("codex_config_unreadable" | "mcp_name_unsupported").
    Reads files only; starts nothing."""
    from . import sessions

    home = Path(codex_home) if codex_home else sessions.codex_dir()
    args = ["-c", "features.plugins=false", "-c", "features.apps=false"]
    cfg = _read_toml(home / "config.toml")
    names = _server_names(cfg)
    if cfg is not None and project is not None:
        cwd = Path(project).resolve()
        root = _nearest(cwd, _root_markers(cfg))
        git_root = _nearest(cwd, [".git"])
        main = _main_checkout(git_root) if git_root is not None else None
        candidates = [x for x in (cwd, root, git_root, main) if x is not None]
        projects = cfg.get("projects") if isinstance(cfg.get("projects"), dict) else {}
        trusted = any(isinstance(v, dict) and v.get("trust_level") == "trusted" and _same_folder(k, c)
                      for k, v in projects.items() for c in candidates)
        if trusted:
            for d in _layer_dirs(cwd, root):
                names |= _server_names(_read_toml(d / ".codex" / "config.toml"))
    for name in sorted(names):
        if not MCP_NAME.match(name):
            raise ValueError("mcp_name_unsupported")
        args += ["-c", f"mcp_servers.{name}.enabled=false"]
    return args


# -- adapter -------------------------------------------------------------------------------------------------------
def make_adapter(side: str, model: str = "", effort: str = "", team: dict | None = None, cwd: Path = Path("."),
                 project: Path | None = None, caps: dict | None = None) -> Adapter:
    """The read-only adapter for one side. Never reads a project's orch.toml, so a project cannot widen access."""
    if side not in discuss.SIDES:
        raise ValueError(f"unknown side {side!r}")
    executor = _executor(side, team)
    if executor not in (EXECUTORS[side], "fake"):
        raise ValueError(f"executor {executor!r} is not allowed for {side}")
    pool_cfg: dict = {"model": model or "", "effort": effort or "",
                      "vendor": ((team or {}).get(side) or {}).get("vendor") or VENDORS[side]}
    if executor == "fake":
        pool_cfg["script"] = team[side]["script"]
    exec_cfg = copy.deepcopy(config.DEFAULTS["executors"].get(executor, {}))
    if executor == "claude_code":
        args = list(exec_cfg.get("args") or [])
        if "--strict-mcp-config" not in args:
            args.append("--strict-mcp-config")
        if ((caps or {}).get("claude") or {}).get("no_session_persistence"):
            args.append("--no-session-persistence")
        exec_cfg["args"] = args
        exec_cfg["readonly_tools"] = list(READ_TOOLS)
    elif executor == "codex":
        exec_cfg["args"] = [*(exec_cfg.get("args") or []), "--skip-git-repo-check", *codex_mcp_off_args(project)]
    exec_cfg["prompt_via_stdin"] = True
    root = Path(team["_dir"]) if executor == "fake" else Path(cwd)
    return REGISTRY[executor](side, pool_cfg, exec_cfg, root)


def scan_tool_calls(events_path: Path) -> list[str]:
    """Names of MCP tool calls (Codex) and of tool_use blocks outside Read/Glob/Grep (Claude) in a call's events."""
    try:
        lines = Path(events_path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    names: list[str] = []
    for ev in iter_json(lines):
        t = ev.get("type")
        if t == "item.completed":
            item = ev.get("item") if isinstance(ev.get("item"), dict) else {}
            if item.get("type") == "mcp_tool_call":
                names.append(str(item.get("tool") or item.get("name") or "mcp_tool_call"))
        elif t == "assistant":
            msg = ev.get("message") if isinstance(ev.get("message"), dict) else {}
            for block in msg.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") not in READ_TOOLS:
                    names.append(str(block.get("name") or "tool_use"))
    return names


def adapter_caller(team: dict | None = None) -> Caller:
    """The real caller (or the fake team's): builds the read-only adapter per call."""
    def call(spec: CallSpec, on_start: Callable[[int], None]) -> CallResult:
        t0 = time.time()
        try:
            executor = _executor(spec.side, team)
            caps = capabilities(team) if executor == "claude_code" else None
            adapter = make_adapter(spec.side, spec.model, spec.effort, team, spec.cwd, spec.project, caps)
        except ValueError as e:
            return CallResult(status="unavailable", error=str(e)[:500], seconds=time.time() - t0)
        ctx = RunContext(task=Task(id=spec.did, title="discuss", type="discuss"), worktree=spec.cwd,
                         events_path=spec.events_path, timeout_s=spec.timeout_s, model=adapter.model, readonly=True,
                         instruction=INSTRUCTION, stdin_text=spec.prompt, on_start=on_start)
        res = adapter.run(ctx)
        cost = res.est_cost_usd if spec.side == "claude" and res.est_cost_usd and res.est_cost_usd > 0 else None
        return CallResult(status=res.status.value, text=res.summary or "", tokens_in=res.tokens_in,
                          tokens_out=res.tokens_out, tokens_cached=res.tokens_cached, est_cost_usd=cost,
                          quota=res.quota, resets_at=res.resets_at, error=res.error or "",
                          seconds=round(time.time() - t0, 2), tool_calls=scan_tool_calls(spec.events_path))

    return call


# -- capabilities ----------------------------------------------------------------------------------------------
_CAPS: dict = {}


def clear_capabilities() -> None:
    _CAPS.clear()


def _params(side: str, found: bool, effort_ok: bool) -> dict:
    missing = "" if found else "cli_missing"
    return {
        "model": {"applies": found, "reason": missing, "how": "-m" if side == "gpt" else "--model",
                  "suggest": list(SUGGEST[side])},
        "effort": {"applies": effort_ok, "reason": "" if effort_ok else (missing or "cli_too_old"),
                   "choices": list(EFFORT_CHOICES)},
        "output_limit_words": {"applies": True, "reason": "", "how": "prompt",
                               "min": discuss.WORDS_MIN, "max": discuss.WORDS_MAX},
        "temperature": {"applies": False, "reason": "no_option"},
        "max_output_tokens": {"applies": False, "reason": "no_option_use_words"},
        "tokens": {"applies": True, "reason": ""},
        "cost_usd": ({"applies": True, "reason": "estimate", "estimate": True} if side == "claude"
                     else {"applies": False, "reason": "subscription_no_price"}),
    }


def _probe(side: str, help_fn) -> dict:
    cli = CLIS[side]

    def ask(*args: str) -> tuple[int | None, str]:
        try:
            code, out = help_fn([cli, *args])
        except Exception as e:  # noqa: BLE001
            return None, str(e)
        return code, out or ""

    code, out = ask("--version")
    found = code == 0
    version = out.strip().splitlines()[0].strip()[:80] if found and out.strip() else ""
    help_text = ""
    if found and side == "claude":
        hcode, help_text = ask("--help")
        if hcode != 0:
            help_text = ""
    effort_ok = found and (side == "gpt" or "--effort" in help_text)
    return {"found": found, "version": version, "cli": cli,
            "no_session_persistence": bool(found and side == "claude" and "--no-session-persistence" in help_text),
            "params": _params(side, found, effort_ok)}


def capabilities(team: dict | None = None, help_fn=None, now=time.time) -> dict:
    """{side: {found, version, cli, no_session_persistence, params: {name: {applies, reason, ...}}}}, cached for a
    plain CAPS_TTL seconds. A fake pool reports every setting applying and starts nothing."""
    key = (str((team or {}).get("_path") or ""), help_fn)
    t = now()
    hit = _CAPS.get(key)
    if hit and t - hit[0] < CAPS_TTL:
        return copy.deepcopy(hit[1])
    fn = help_fn or (lambda cmd: proc.run_capture(cmd, timeout=20))   # looked up at call time (tests patch it)
    out = {}
    for side in discuss.SIDES:
        if _executor(side, team) == "fake":
            out[side] = {"found": True, "version": "fake", "cli": CLIS[side], "no_session_persistence": False,
                         "params": _params(side, True, True)}
        else:
            out[side] = _probe(side, fn)
    _CAPS[key] = (t, out)
    return copy.deepcopy(out)


__all__ = ["CallSpec", "CallResult", "Caller", "INSTRUCTION", "EXECUTORS", "load_team", "call_folder",
           "codex_mcp_off_args", "make_adapter", "adapter_caller", "capabilities", "scan_tool_calls"]
