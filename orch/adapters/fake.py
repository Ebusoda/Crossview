"""Fake executor for the offline demo and tests: runs a Python script inside the worktree.

The script plays the role of an agent: it edits files and prints JSON lines such as
  {"type": "result", "session_id": "fake-1", "text": "done"}
  {"type": "error", "error": "rate_limit", "resets_at": 1760000000}
Costs nothing, so the whole orchestration loop can be exercised before touching quotas.
"""
from __future__ import annotations

import sys
from pathlib import Path

from ..models import RunResult, RunStatus
from ..proc import ProcResult
from .base import Adapter, RunContext, iter_json


class FakeAdapter(Adapter):
    executor = "fake"

    @property
    def script(self) -> Path:
        p = Path(self.pool_cfg.get("script", ""))
        return p if p.is_absolute() else (self.project / p)

    @property
    def command(self) -> str:
        return sys.executable

    def available(self) -> bool:
        return self.script.is_file()

    def build_cmd(self, exe: str, ctx: RunContext) -> list[str]:
        cmd = [exe, str(self.script)]
        if ctx.resume_session:
            cmd += ["--resume", ctx.resume_session]
        return cmd

    def parse(self, pr: ProcResult, ctx: RunContext) -> RunResult:
        """Its own lines, or lines shaped like Claude Code's or Codex's (the demo team imitates real CLIs)."""
        res = RunResult(status=RunStatus.CRASHED)
        said = ""
        for ev in iter_json(pr.tail):
            if ev.get("session_id") or ev.get("thread_id"):
                res.session_id = ev.get("session_id") or ev["thread_id"]
            usage = ev.get("usage") if isinstance(ev.get("usage"), dict) else {}
            if ev.get("type") == "result" or ev.get("type") == "turn.completed":
                res.status = RunStatus.COMPLETED
                res.summary = str(ev.get("text", ev.get("result", said)))
                res.est_cost_usd = float(ev.get("cost_usd", ev.get("total_cost_usd", 0.0)))
                tokens_in = ev.get("tokens_in", usage.get("input_tokens"))
                if isinstance(tokens_in, int):
                    res.tokens_in = tokens_in
                if isinstance(usage.get("output_tokens"), int):
                    res.tokens_out = usage["output_tokens"]
            elif ev.get("type") == "item.completed" and (ev.get("item") or {}).get("type") == "agent_message":
                said = str(ev["item"].get("text", ""))
            elif ev.get("type") == "error":
                res.error = str(ev.get("error", ""))
                res.status = RunStatus.RATE_LIMITED if "rate" in res.error else RunStatus.CRASHED
                if isinstance(ev.get("resets_at"), (int, float)):
                    res.resets_at = float(ev["resets_at"])
        if res.status == RunStatus.COMPLETED and pr.exit_code not in (0, None):
            res.status = RunStatus.CRASHED
        return res
