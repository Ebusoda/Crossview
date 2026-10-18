"""The Agent page: the Claude Code / Codex sessions opened by hand on this PC (orch/sessions.py: times, folders
and token counts only, never what was said), plus, while the old dispatch is on (orch/legacy.py), orch's control
panel (orch/agents.py) for every registered project (项目). Admin only: pausing an agent or moving a task changes
how the whole family's coding work runs.
"""
from __future__ import annotations

from pathlib import Path

from orch import agents as panel_mod
from orch import sessions
from orch.config import Config
from orch.scheduler import Orchestrator

NOT_PREPARED = "这个文件夹还没有为旧的派活功能准备好（缺少 .agents/orch.toml）。请在 项目 页移除它，再用「添加项目」加回来。"


def _open(path: str) -> Orchestrator:
    project = Path(path).resolve()   # only reads and pauses: the process-wide git identity stays untouched
    return Orchestrator(project, say=lambda _msg: None, cfg=Config.load(project, apply_identity=False))


def overview(app, dispatch: bool = False) -> dict:
    """The sessions always; the projects' control panels only while the old dispatch is on (no Orchestrator is
    opened while it is off)."""
    projects = []
    for p in app.projects.list() if dispatch else []:
        folder = Path(p["path"])
        if folder.is_dir() and not (folder / ".agents" / "orch.toml").is_file():
            # registered for discussions only (projects/link): opening it could create .agents/state.db there
            projects.append({"name": p["name"], "error": NOT_PREPARED})
            continue
        try:
            orch = _open(p["path"])
        except Exception as e:  # a moved or broken project should not hide the others
            projects.append({"name": p["name"], "error": f"打不开这个项目：{e}"})
            continue
        try:
            projects.append({"name": p["name"], "panel": panel_mod.panel(orch, manual=False)})
        finally:
            orch.store.close()
    local = sessions.scan(days=1)
    manual = [{k: s[k] for k in ("tool", "project", "model", "last_at", "active", "tokens_today", "turns")
               if k in s} for s in local["sessions"] if s["source"] == "manual"][:20]
    return {"projects": projects, "dispatch": dispatch, "sessions": manual, "summary": local["summary"],
            "codex_quota": local["codex_quota"]}


def act(app, body: dict) -> str:
    name = str(body.get("project", ""))
    proj = next((p for p in app.projects.list() if p["name"] == name), None)
    if proj is None:
        raise ValueError("没有这个项目")
    action, pool = str(body.get("action", "")), str(body.get("pool", ""))
    orch = _open(proj["path"])
    try:
        if action == "pause":
            minutes = float(body["minutes"]) if body.get("minutes") else None
            panel_mod.pause(orch, pool, minutes)
            return f"已暂停 {pool}" + (f" {minutes:g} 分钟" if minutes else "，直到你恢复")
        if action == "resume":
            panel_mod.resume(orch, pool)
            return f"已恢复 {pool}"
        if action == "swap":
            task = str(body.get("task", ""))
            panel_mod.swap(orch, task, pool)
            return f"{task} 改由 {pool} 先做，已重新排队"
        raise ValueError("不认识的操作")
    finally:
        orch.store.close()
