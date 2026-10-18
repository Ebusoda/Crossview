"""Personal chat through the unmodified, subscription-authenticated Claude CLI.

Claude owns its login. We never read credentials or call its private endpoints.
Web history is passed per request, so CLI sessions cannot mix web conversations.
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

from orch.llm import LLMError, Reply


class Subscription:
    def __init__(self, environ, workdir: Path | None = None):
        self.environ = dict(environ)
        self.workdir = workdir or Path(tempfile.gettempdir()) / "mao-claude-chat"
        self._lock = threading.Lock()
        self._chat_lock = threading.Lock()
        self._checked = 0.0
        self._status = {}
        self._quota = {}

    def limit_message(self, info):
        message = "Claude 订阅额度暂时用完，请等待额度恢复。"
        reset = info.get("resetsAt") or info.get("resets_at")
        if isinstance(reset, (int, float)):
            message += "恢复时间：" + datetime.fromtimestamp(reset).astimezone().strftime("%m月%d日 %H:%M") + "（本机时间）。"
        return message

    def public_status(self):
        result = dict(self._status)
        reset = self._quota.get("resetsAt") or 0
        if result.get("ready") and self._quota.get("status") == "rejected" and reset > time.time():
            result["message"] += "。" + self.limit_message(self._quota)
        return result

    def environment(self):
        env = dict(self.environ)
        # A subscription provider must not silently use API billing or a proxy.
        for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
                     "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_API_KEY_HELPER", "CLAUDECODE",
                     "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
                     "CLAUDE_CODE_USE_ANTHROPIC_AWS"):
            env.pop(name, None)
        env["CLAUDE_CODE_SAFE_MODE"] = "1"
        return env

    def executable(self):
        return shutil.which("claude", path=self.environ.get("PATH", self.environ.get("Path", "")))

    def status(self, refresh=False):
        with self._lock:
            if not refresh and time.monotonic() - self._checked < 60 and self._status:
                return self.public_status()
            exe = self.executable()
            result = {"ready": False, "message": "未找到 Claude Code，请在本机安装后重新打开 MAO。"}
            if exe:
                try:
                    proc = subprocess.run([exe, "auth", "status"], env=self.environment(),
                                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                                          timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    auth = json.loads(proc.stdout)
                    ready = bool(proc.returncode == 0 and auth.get("loggedIn")
                                 and auth.get("authMethod") == "claude.ai" and auth.get("subscriptionType"))
                    result = {"ready": ready, "plan": str(auth.get("subscriptionType") or ""),
                              "message": "已连接本机 Claude 订阅" if ready else
                              "请先在本机运行 claude auth login，用你的 Claude 订阅账号登录。"}
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    result = {"ready": False, "message": "Claude 登录状态检查失败，请在本机运行 claude auth status。"}
            self._status, self._checked = result, time.monotonic()
            return self.public_status()

    def chat_stream(self, model, messages, timeout=300):
        if not self.status()["ready"]:
            raise LLMError(self.status()["message"], "auth")
        self.workdir.mkdir(parents=True, exist_ok=True)
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        # Web chat runs with every tool switched off; without this note the model still tries to read
        # files the user mentions and prints the raw tool call as its answer.
        system += ("\n\n这是网页聊天：你没有任何工具，不能读取文件、浏览文件夹或运行命令，也不要输出工具调用。"
                   "需要看文件或改代码时，直接根据对话回答，并说明网页聊天看不到文件。"
                   "\nThis is web chat: you have no tools and cannot read files, browse folders or run commands; "
                   "never print tool calls. If files must be read or changed, answer from the conversation and "
                   "say that web chat cannot see files.")
        prompt = ("以下 JSON 是当前网页对话的历史消息。请接着回答最后一条 user 消息，"
                  "只输出给用户的回答。\n" + json.dumps(
                      [m for m in messages if m["role"] != "system"], ensure_ascii=False))
        cmd = [self.executable(), "-p", "--output-format", "stream-json", "--verbose",
               "--include-partial-messages", "--model", model, "--tools", "",
               "--permission-mode", "dontAsk", "--safe-mode", "--strict-mcp-config",
               "--mcp-config", '{"mcpServers":{}}', "--no-chrome", "--no-session-persistence",
               "--setting-sources", "",
               "--append-system-prompt", system]
        proc = None
        events = queue.Queue(maxsize=256)
        stopped = threading.Event()
        deadline = time.monotonic() + timeout
        parts = []
        if not self._chat_lock.acquire(blocking=False):
            raise LLMError("当前已有一个 Claude 订阅聊天正在回复，请等它完成后再发送。", "rate_limit")

        def write_input():
            try:
                proc.stdin.write(prompt)
                proc.stdin.close()
            except (OSError, ValueError):
                pass

        def read_output():
            try:
                for line in proc.stdout:
                    while not stopped.is_set():
                        try:
                            events.put(line, timeout=0.2)
                            break
                        except queue.Full:
                            pass
                    if stopped.is_set():
                        return
            except (OSError, ValueError):
                pass
            finally:
                while not stopped.is_set():
                    try:
                        events.put(None, timeout=0.2)
                        break
                    except queue.Full:
                        pass

        try:
            proc = subprocess.Popen(cmd, cwd=self.workdir, env=self.environment(), stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                    encoding="utf-8", errors="replace", bufsize=1,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            threading.Thread(target=read_output, daemon=True).start()
            threading.Thread(target=write_input, daemon=True).start()
            while True:
                if time.monotonic() >= deadline:
                    raise LLMError("Claude 回复超时，请稍后重试。", "timeout")
                try:
                    line = events.get(timeout=1)
                except queue.Empty:
                    yield "ping", None
                    continue
                if line is None:
                    raise LLMError("Claude 未返回完整回复，请检查本机 Claude Code 的连接和登录状态。")
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("type") == "stream_event":
                    event = ev.get("event") or {}
                    delta = event.get("delta") or {}
                    if event.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                        text = delta.get("text") or ""
                        parts.append(text)
                        yield "delta", text
                elif ev.get("type") == "rate_limit_event":
                    info = ev.get("rate_limit_info") or {}
                    with self._lock:
                        self._quota = {"status": info.get("status"), "resetsAt": info.get("resetsAt")}
                    if info.get("status") == "rejected":
                        raise LLMError(self.limit_message(info), "rate_limit")
                elif ev.get("type") == "result":
                    text = ev.get("result") or "".join(parts)
                    if ev.get("is_error"):
                        detail = str(text).lower()
                        kind = "rate_limit" if (ev.get("api_error_status") == 429 or
                            any(s in detail for s in ("usage limit", "session limit", "quota", "rate limit"))) else "crashed"
                        raise LLMError("Claude 订阅额度暂时用完，请等待额度恢复。" if kind == "rate_limit"
                                       else "Claude 未完成回复，请检查本机 Claude Code 的状态。", kind)
                    if not text:
                        raise LLMError("Claude 返回了空回复，请重试。")
                    if not parts:
                        yield "delta", text
                    usage = ev.get("usage") or {}
                    tin = sum(int(usage.get(k) or 0) for k in
                              ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
                    yield "done", Reply(text, model, tin or None, usage.get("output_tokens"))
                    return
        except OSError as exc:
            raise LLMError("无法启动本机 Claude Code，请确认安装正常。") from exc
        finally:
            stopped.set()
            try:
                if proc is not None:
                    if proc.poll() is None:
                        proc.terminate()
                        try:
                            proc.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait(timeout=5)
                    for stream in (proc.stdin, proc.stdout):
                        if stream is not None and not stream.closed:
                            stream.close()
            finally:
                self._chat_lock.release()
