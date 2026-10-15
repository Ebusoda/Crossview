"""Chat providers: API services and a personal Claude Code subscription transport.

A provider is usable once it has an API key: from its environment variable (wins), or from the key
the admin typed on the settings page. Model lists can be edited on the settings page too.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .db import DB
from orch.secrets import KeyStore
from .subscription import Subscription


@dataclass
class Provider:
    id: str
    name: str
    base_url: str
    env: str
    models: list[str] = field(default_factory=list)
    note: str = ""
    key_headers: tuple[str, ...] = ()        # extra headers that carry the key (Anthropic wants x-api-key)
    transport: str = "api"


# Model ids as of 2026-10; each provider's console has the current list ("测试连接" fetches it).
PROVIDERS = [
    Provider("claude_subscription", "Claude（我的订阅）", "", "", ["sonnet", "opus", "haiku"],
             "使用本机 Claude Code 的订阅登录，共享订阅额度；仅管理员账号可用。", transport="cli"),
    Provider("deepseek", "DeepSeek", "https://api.deepseek.com", "DEEPSEEK_API_KEY",
             ["deepseek-flash", "deepseek-v4-pro"], "platform.deepseek.com 申请"),
    Provider("doubao", "豆包（火山方舟）", "https://ark.cn-beijing.volces.com/api/v3", "ARK_API_KEY",
             [], "console.volcengine.com/ark 申请；模型名在方舟控制台的「模型广场 / 在线推理」里查"),
    Provider("qwen", "通义千问（阿里云百炼）", "https://dashscope.aliyuncs.com/compatible-mode/v1", "DASHSCOPE_API_KEY",
             ["qwen-plus", "qwen-max", "qwen-turbo"], "bailian.console.aliyun.com 申请"),
    # overseas providers through their OpenAI-compatible endpoints
    Provider("openai", "GPT（OpenAI）", "https://api.openai.com/v1", "OPENAI_API_KEY",
             ["gpt-5-mini", "gpt-5"], "platform.openai.com 申请；可用的模型以「测试连接」列出的为准"),
    Provider("anthropic", "Claude（Anthropic）", "https://api.anthropic.com/v1", "ANTHROPIC_API_KEY",
             ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-4-5"], "console.anthropic.com 申请",
             key_headers=("x-api-key",)),
    Provider("gemini", "Gemini（Google）", "https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY",
             ["gemini-2.5-flash", "gemini-2.5-pro"], "aistudio.google.com 申请；可用的模型以「测试连接」列出的为准"),
]
BY_ID = {p.id: p for p in PROVIDERS}


class Registry:
    def __init__(self, db: DB, keys: KeyStore, environ: dict[str, str], subscription=None):
        self.db, self.keys, self.environ = db, keys, environ
        self.subscription = subscription or Subscription(environ)

    def key(self, pid: str) -> tuple[str | None, str]:
        """(key, source) where source is env / saved / none. The key itself never leaves the server."""
        p = BY_ID[pid]
        if p.transport == "cli":
            return None, "subscription" if self.subscription.status()["ready"] else "none"
        env = (self.environ.get(p.env) or "").strip()
        if env:
            return env, "env"
        saved = self.keys.get(pid)
        return (saved, "saved") if saved else (None, "none")

    def base_url(self, pid: str) -> str:
        """MAO_WEB_BASE_<ID> overrides the address (tests, or a proxy the owner sets up)."""
        return (self.environ.get(f"MAO_WEB_BASE_{pid.upper()}") or BY_ID[pid].base_url).rstrip("/")

    def headers(self, pid: str, key: str) -> dict[str, str]:
        p = BY_ID[pid]
        h = {name: key for name in p.key_headers}
        if pid == "anthropic":
            h["anthropic-version"] = "2023-06-01"
        return h

    def models(self, pid: str) -> list[str]:
        custom = self.db.setting(f"models.{pid}")
        return list(custom) if isinstance(custom, list) else list(BY_ID[pid].models)

    def available(self, include_subscription=False) -> list[dict]:
        out = []
        for p in PROVIDERS:
            if p.transport == "cli":
                if include_subscription and self.subscription.status()["ready"]:
                    out += [{"id": f"{p.id}/{m}", "provider": p.name, "model": m} for m in self.models(p.id)]
                continue
            key, _ = self.key(p.id)
            if key:
                out += [{"id": f"{p.id}/{m}", "provider": p.name, "model": m} for m in self.models(p.id)]
        return out

    def resolve(self, model_id: str) -> tuple[Provider, str, str]:
        """'qwen/qwen-plus' -> (provider, model, key). Raises KeyError / ValueError."""
        pid, _, model = model_id.partition("/")
        p = BY_ID[pid]
        if model not in self.models(pid):
            raise ValueError(f"模型 {model_id} 没有在设置里启用")
        if p.transport == "cli":
            status = self.subscription.status()
            if not status["ready"]:
                raise ValueError(status["message"])
            return p, model, ""
        key, _ = self.key(pid)
        if not key:
            raise ValueError(f"{p.name} 还没有设置 API key")
        return p, model, key

    def describe(self) -> list[dict]:
        """For the settings page: no key values, only whether one is set and where from."""
        out = []
        for p in PROVIDERS:
            _, source = self.key(p.id)
            out.append({"id": p.id, "name": p.name, "base_url": p.base_url, "env": p.env, "note": p.note,
                        "key_source": source, "models": self.models(p.id), "transport": p.transport,
                        **({"status": self.subscription.status()} if p.transport == "cli" else {})})
        return out
