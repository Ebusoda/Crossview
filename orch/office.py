"""Which model does the office work (`orch sheet`, `orch doc`), and calling it with fallback.

The order: --provider when given, else the pools under `office = [...]` in the project's [routing]
(direct-API pools only), else every provider you have a key for: DeepSeek, Qwen, GPT, Claude, Gemini.
When one fails (no quota, wrong key, timeout) the next one is tried.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import llm
from .i18n import settings_path, tr

ORDER = ("deepseek", "qwen", "openai", "anthropic", "gemini")
DOMESTIC = ("deepseek", "qwen", "doubao")     # sensitive mode: only these (servers in China)


class OfficeError(ValueError):
    pass


@dataclass
class Endpoint:
    label: str
    base_url: str
    key: str
    model: str
    headers: dict[str, str]
    prices: dict[str, Any] = field(default_factory=dict)


def sensitive_saved() -> bool:
    """`orch office sensitive on` (saved in the same settings file as the language)."""
    try:
        return bool(json.loads(settings_path().read_text(encoding="utf-8")).get("office_sensitive"))
    except (OSError, ValueError, AttributeError):
        return False


def save_sensitive(on: bool) -> None:
    path = settings_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data["office_sensitive"] = bool(on)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def endpoints(project: Path | None = None, provider: str | None = None, model: str | None = None,
              sensitive: bool | None = None) -> list[Endpoint]:
    """The models to try, in order: --provider, else [routing] office in the project, else every
    provider you have a key for. In sensitive mode only DeepSeek, Qwen and Doubao are used."""
    from .providers import BY_ID, headers_for, key_for, preset_for_base_url

    if sensitive is None:
        sensitive = sensitive_saved()
    if provider:
        p = BY_ID.get(provider)
        if p is None:
            raise OfficeError(tr("不认识的服务商 {0}；可以用：{1}", provider, ", ".join(BY_ID)))
        if sensitive and p.id not in DOMESTIC:
            raise OfficeError(tr("敏感模式下只能用国内模型（{0}）；{1} 不行", ", ".join(DOMESTIC), p.name))
        key, _ = key_for(p)
        if not key:
            raise OfficeError(tr("{0} 还没有 key：运行 orch key set {1}", p.name, p.id))
        if not (model or p.api_model):
            raise OfficeError(tr("{0} 没有默认模型，请用 --model 指定", p.name))
        return [Endpoint(p.id, p.base_url, key, model or p.api_model, headers_for(p, key))]

    out: list[Endpoint] = []
    toml = (project / ".agents" / "orch.toml") if project else None
    if toml is not None and toml.is_file():
        from .adapters import make_adapter
        from .config import Config

        cfg = Config.load(project)
        for pool in cfg.data.get("routing", {}).get("office", []) or []:
            if pool not in cfg.pools or cfg.pools[pool].get("executor") != "api":
                continue
            a = make_adapter(cfg, pool)
            preset = BY_ID.get(str(a.pool_cfg.get("provider") or "")) or preset_for_base_url(a._cfg("base_url"))
            if sensitive and (preset is None or preset.id not in DOMESTIC):
                continue
            key = a._key()
            if key:
                out.append(Endpoint(pool, a._cfg("base_url"), key, model or a.model, a._headers(key), a.pool_cfg))
        if out:
            return out
    for pid in ORDER:
        if sensitive and pid not in DOMESTIC:
            continue
        p = BY_ID[pid]
        key, _ = key_for(p)
        if key and p.api_model:
            out.append(Endpoint(p.id, p.base_url, key, model or p.api_model, headers_for(p, key)))
    if not out and sensitive:
        raise OfficeError(tr("敏感模式下没有能用的国内模型：先运行 orch key set deepseek（或 qwen）"))
    if not out:
        raise OfficeError(tr("还没有能用的模型：先运行 orch key set deepseek（或 qwen、openai、anthropic、gemini）"))
    return out


@dataclass
class Result:
    text: str
    endpoint: str
    model: str
    tokens_in: int | None = None
    tokens_out: int | None = None
    tried: list[str] = field(default_factory=list)


def complete(eps: list[Endpoint], messages: list[dict[str, str]], timeout: float = 300,
             temperature: float | None = None) -> Result:
    """The first endpoint that answers with some text; the others are tried in turn."""
    tried: list[str] = []
    for ep in eps:
        try:
            reply = llm.chat(ep.base_url, ep.key, ep.model, messages, timeout=timeout, temperature=temperature,
                             headers=ep.headers)
        except llm.LLMError as e:
            tried.append(f"{ep.label}: {e.kind}")
            continue
        if not reply.text.strip():
            tried.append(f"{ep.label}: empty")
            continue
        tried.append(f"{ep.label}: ok")
        return Result(reply.text, ep.label, reply.model, reply.tokens_in, reply.tokens_out, tried)
    raise OfficeError(tr("没有模型给出回答（{0}）", "; ".join(tried)))
