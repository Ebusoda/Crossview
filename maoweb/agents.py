"""The Agent page: orch's control panel (orch/agents.py) for every project listed under 写程序,
plus the Claude Code / Codex sessions opened by hand on this PC (orch/sessions.py: times, folders and
token counts only, never what was said). Admin only: pausing an agent or moving a task changes how the
whole family's coding work runs.
"""
from __future__ import annotations

from pathlib import Path

from orch import agents as panel_mod
from orch import sessions
from orch.scheduler import Orchestrator


def _open(path: str) -> Orchestrator:
    return Orchestrator(Path(path), say=lambda _msg: None)


def overview(app) -> dict:
    projects = []
    for p in app.coding.projects():
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
    return {"projects": projects, "sessions": manual, "summary": local["summary"],
            "codex_quota": local["codex_quota"]}


def act(app, body: dict) -> str:
    name = str(body.get("project", ""))
    proj = next((p for p in app.coding.projects() if p["name"] == name), None)
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
