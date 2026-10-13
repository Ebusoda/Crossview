"""Google Gemini CLI (`gemini -p ... --output-format stream-json`).

Uses the user's own login of the unmodified `gemini` binary (a Google account, or GEMINI_API_KEY /
Vertex settings that `gemini` itself reads). The orchestrator never reads Gemini credentials.

Headless mode, as defined in gemini-cli's packages/core/src/output/types.ts (checked 2026-10-13):
JSONL events `init` (session_id, model), `message` (role, content, delta), `tool_use`, `tool_result`,
`error` (severity, message) and a final `result` (status success | error, error, stats with
input_tokens, output_tokens, cached, tool_calls). Exit codes: 0 ok, 1 error, 42 bad input, 53 turn limit.

`--resume` takes "latest" or an index, not a session id, so a failed run is handed over through
HANDOFF.md like every other executor instead of being resumed.
"""
from __future__ import annotations

from ..models import RunResult, RunStatus
from ..proc import ProcResult
from .base import Adapter, RunContext, classify_text, iter_json


class GeminiAdapter(Adapter):
    executor = "gemini"
    default_vendor = "google"

    def supports_resume(self) -> bool:
        return False

    def build_cmd(self, exe: str, ctx: RunContext) -> list[str]:
        args = list(self.exec_cfg.get("args", []))
        if "--output-format" not in args and "-o" not in args:
            args += ["--output-format", "stream-json"]
        if ctx.readonly:
            # plan = read-only mode: tools that change files or run commands are not offered
            args = [a for a in args if a not in ("--yolo", "-y")]
            if "--approval-mode" in args:
                args[args.index("--approval-mode") + 1] = "plan"
            else:
                args += ["--approval-mode", "plan"]
        cmd = [exe, "-p", ctx.instruction, *args]
        if self.model:
            cmd += ["-m", self.model]
        return cmd

    def parse(self, pr: ProcResult, ctx: RunContext) -> RunResult:
        res = RunResult(status=RunStatus.CRASHED)
        result: dict | None = None
        errors: list[str] = []
        tools = 0
        reply: list[str] = []
        for ev in iter_json(pr.tail):
            t = ev.get("type")
            if t == "init" and ev.get("session_id"):
                res.session_id = str(ev["session_id"])
            elif t == "message" and ev.get("role") == "assistant":
                text = str(ev.get("content") or "")
                if ev.get("delta"):
                    reply.append(text)
                else:
                    reply = [text]
            elif t == "tool_use":
                tools += 1
                reply = []          # the summary is what the agent said after its last action
            elif t == "error" and ev.get("severity") == "error":
                errors.append(str(ev.get("message") or ev))
            elif t == "result":
                result = ev
        if reply:
            res.summary = "".join(reply).strip()
        if result is not None:
            stats = result.get("stats") or {}
            res.tokens_in = int(stats.get("input_tokens") or 0) or None
            res.tokens_out = int(stats.get("output_tokens") or 0) or None
            res.tokens_cached = int(stats.get("cached") or 0) if res.tokens_in else None
            calls = stats.get("tool_calls")
            res.model_calls = (int(calls) if calls is not None else tools) + 1
            err = result.get("error") or {}
            if isinstance(err, dict) and err.get("message"):
                errors.append(str(err["message"]))
        res.error = "\n".join(errors)[-2000:]
        if result is not None and result.get("status") == "success":
            res.status = RunStatus.COMPLETED
        elif result is not None or errors:
            res.status = classify_text(res.error + "\n" + pr.stderr_tail) or RunStatus.CRASHED
        else:
            res.status = self.fallback_status(pr, "\n".join(pr.tail[-20:]))
            if res.status == RunStatus.COMPLETED:
                res.status = RunStatus.CRASHED     # no result event: the run did not finish properly
        if pr.exit_code == 53 and not res.error:
            res.error = "gemini: turn limit exceeded (exit 53)"
        return res
