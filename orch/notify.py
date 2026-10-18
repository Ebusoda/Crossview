"""Phone alerts through ntfy (https://ntfy.sh, or the owner's own ntfy server): read-only, minimal text.

Crossview pushes a short line to the owner's phone when something waits for them: a discussion wants a decision (or
stopped, or looks stuck), Cowork reports a blocker / a question / done, a ChatGPT task waits for Send, or Claude Code
waits for a permission. Crossview never clicks or approves anything. A push carries only what happened and a short id
(DS-0003, the end of a job id, the start of a session id): never requirement text, model answers, report text,
file contents, folder or project names, keys or personal data, because ntfy.sh is someone else's server.

Everything lives in Crossview's data folder (orch/datadir.py; the web app's .maoweb):
- notify.json        the config (off until the owner sets it up; a random topic that is hard to guess)
- notify_sent.json   dedup and retry state, the rate window and the recent list (under notify.lock)
- notify_drops.jsonl pushes recorded while another process held the lock (folded in by the next locked write)
- keys.json          the ntfy access token under the name "ntfy" (orch/secrets.py; DPAPI on Windows), never in
                     notify.json, never sent to a browser and never logged

The web app, the MCP server, the Claude Code hook and the `orch notify` CLI all share these files, so dedup, the
rate limit and the recent list hold across processes. Reads never write (load, token, done_keys, recent, retry_due,
and notify() whenever it answers off / old / present).
"""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import re
import secrets
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

from . import datadir

FILE, SENT_FILE, LOCK_FILE, DROPS_FILE, TOKEN_NAME = ("notify.json", "notify_sent.json", "notify.lock",
                                                      "notify_drops.jsonl", "ntfy")
DEFAULT_SERVER = "https://ntfy.sh"
KINDS = ("discuss_waiting", "discuss_failed", "discuss_stuck", "cowork_blocker", "cowork_question", "cowork_done",
         "cowork_silent", "gpt_waiting", "cc_permission", "cc_input", "test")
GROUP = {"discuss_waiting": "discussion", "discuss_failed": "discussion", "discuss_stuck": "discussion",
         "cowork_blocker": "cowork", "cowork_question": "cowork", "cowork_done": "cowork",
         "cowork_silent": "cowork_silent", "gpt_waiting": "gpt", "cc_permission": "cc_permission",
         "cc_input": "cc_input", "test": ""}          # "" = always (test only)
GROUPS = ("discussion", "cowork", "cowork_silent", "gpt", "cc_permission", "cc_input")
PRESENCE_KINDS = frozenset({"cc_permission", "cc_input"})   # skipped while the owner is at the PC
RATE = (10, 600)                 # at most 10 pushes in any 600 s, per data folder, all processes together
BACKOFF = (60, 300, 900, 3600)   # wait after the 1st, 2nd, 3rd and every later failure
GIVE_UP_S = 7200                 # no try is planned later than 2 h after the first one
CC_FRESH_S = 600                 # a Claude Code event is retried from the log for 10 min at most
PENDING_S, LOCK_S, KEEP_DAYS, RECENT_MAX, SEND_TIMEOUT = 60, 5.0, 30, 50, 8.0
LIMITS = {"stuck_minutes": (21, 240), "cowork_silent_hours": (1, 72), "gpt_wait_minutes": (1, 25),
          "cc_away_minutes": (0, 30)}
LANGS = ("zh", "en")
LOOPBACK = ("127.0.0.1", "localhost", "::1")
Sender = Callable[[str, bytes, dict, float], None]   # (url, body, headers, timeout); raises on any failure

DEFAULTS: dict = {
    "version": 1, "enabled": False, "server": DEFAULT_SERVER, "topic": "", "lang": "zh",
    "events": {"discussion": True, "cowork": True, "cowork_silent": False, "gpt": True,
               "cc_permission": True, "cc_input": False},
    "since": {g: 0 for g in GROUPS},
    "thresholds": {"stuck_minutes": 25, "cowork_silent_hours": 6, "gpt_wait_minutes": 2, "cc_away_minutes": 3},
    "updated_at": 0,
}

PRIORITY = {"discuss_waiting": 4, "discuss_failed": 4, "cowork_blocker": 4, "cowork_question": 4, "gpt_waiting": 4,
            "cc_permission": 4, "discuss_stuck": 3, "cowork_done": 3, "cc_input": 3, "test": 3, "cowork_silent": 2}
TAGS = {"discuss_waiting": "speech_balloon", "discuss_failed": "warning", "discuss_stuck": "warning",
        "cowork_blocker": "warning", "cowork_question": "question", "cowork_done": "white_check_mark",
        "cowork_silent": "hourglass", "gpt_waiting": "outbox_tray", "cc_permission": "bell", "cc_input": "bell",
        "test": "tada"}
# (title, message) per kind and language; {ref} is a short id, never a name or a text
MESSAGES: dict[str, dict[str, tuple[str, str]]] = {
    "discuss_waiting": {"zh": ("Crossview 讨论", "{ref} 第 {n} 阶段小结好了，等你决定"),
                        "en": ("Crossview discussion", "{ref}: stage {n} summary is ready for your decision")},
    "discuss_failed": {"zh": ("Crossview 讨论", "{ref} 停下了（出错或中断），打开 Crossview 看看"),
                       "en": ("Crossview discussion", "{ref} stopped (error or interruption); open Crossview to look")},
    "discuss_stuck": {"zh": ("Crossview 讨论", "{ref} 已经 {minutes} 分钟没有动静，可能卡住了"),
                      "en": ("Crossview discussion", "{ref}: no progress for {minutes} minutes; it may be stuck")},
    "cowork_blocker": {"zh": ("Cowork", "{ref}：Cowork 遇到阻碍，需要你看一下"),
                       "en": ("Cowork", "{ref}: Cowork is blocked and needs you")},
    "cowork_question": {"zh": ("Cowork", "{ref}：Cowork 有个问题要问你"),
                        "en": ("Cowork", "{ref}: Cowork has a question for you")},
    "cowork_done": {"zh": ("Cowork", "{ref}：Cowork 报告做完了，请你核对"),
                    "en": ("Cowork", "{ref}: Cowork reports it is done; please check")},
    "cowork_silent": {"zh": ("Cowork（弱信号）", "{ref} 领走后 {hours} 小时没有回报。Cowork 可能停了，也可能还在做"),
                      "en": ("Cowork (weak signal)", "{ref}: no report for {hours} hours since it was taken. Cowork "
                                                     "may have stopped, or may still be working")},
    "gpt_waiting": {"zh": ("ChatGPT", "有一个任务等你在 ChatGPT 里按发送（{ref}）"),
                    "en": ("ChatGPT", "A task is waiting for you to press Send in ChatGPT ({ref})")},
    "cc_permission": {"zh": ("Claude Code", "在等你批准或回答（会话 {ref}）"),
                      "en": ("Claude Code", "is waiting for your approval or answer (session {ref})")},
    "cc_input": {"zh": ("Claude Code", "停下来在等你继续（会话 {ref}）"),
                 "en": ("Claude Code", "has stopped and is waiting for you to continue (session {ref})")},
    "test": {"zh": ("Crossview", "测试提醒：手机能收到 Crossview 的提醒了"),
             "en": ("Crossview", "Test: Crossview notifications reach this phone")},
}
FIELDS = ("ref", "n", "minutes", "hours")      # the only values a push text is filled with

# Chinese errors for the web (maoweb/static/i18n.js has their English; the CLI catalog too)
BAD_SERVER = "服务器地址要以 https:// 开头，不能带用户名、密码、? 或 #（本机测试可以用 http://127.0.0.1）"
BAD_TOPIC = "主题只能用字母、数字、- 和 _，12 到 64 个字符"
BAD_LANG = "提醒的语言只能是中文或 English"
BAD_NUMBER = "提醒的时间设置不在允许的范围里"
NO_TOPIC = "还没有主题，先生成一个"
BAD_TOKEN = "访问令牌里不能有空格、换行或中文等字符"
TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]{12,64}$")
TOKEN_RE = re.compile(r"^[\x21-\x7e]{1,512}$")       # printable ASCII, no spaces: it goes into an HTTP header


class NotifyError(Exception):
    """A push did not get through (the text never contains the token)."""


def _data(data: Path | str | None) -> Path:
    return Path(data) if data else datadir.data_dir()


def _say(text: str) -> None:
    if sys.stderr is not None:
        try:
            print(text[:300], file=sys.stderr)
        except Exception:  # noqa: BLE001  (a closed stderr must not break anything)
            pass


# -- config ---------------------------------------------------------------------------------------------
def clean_server(raw) -> str:
    """The server address without a trailing /, or ValueError(BAD_SERVER). The rejected text is never echoed."""
    s = raw if isinstance(raw, str) else ""
    s = s.strip()
    if not s or len(s) > 200 or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in s):
        raise ValueError(BAD_SERVER)
    try:
        u = urllib.parse.urlsplit(s)
        host = u.hostname or ""
        port = u.port          # a bad port raises ValueError
    except ValueError:
        raise ValueError(BAD_SERVER) from None
    del port
    if (u.scheme not in ("https", "http") or not host or u.username is not None or u.password is not None
            or "@" in u.netloc or "?" in s or "#" in s or u.query or u.fragment):
        raise ValueError(BAD_SERVER)
    if u.scheme == "http" and host not in LOOPBACK:
        raise ValueError(BAD_SERVER)
    return s.rstrip("/")


def _int(value, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(BAD_NUMBER)
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    elif isinstance(value, str) and re.fullmatch(r"\s*\d{1,4}\s*", value):
        value = int(value)
    if not isinstance(value, int):
        raise ValueError(BAD_NUMBER)
    lo, hi = LIMITS[name]
    if not lo <= value <= hi:
        raise ValueError(BAD_NUMBER)
    return value


def _merge(raw) -> dict:
    """DEFAULTS with every valid stored value; a value that is invalid keeps its default."""
    cfg = copy.deepcopy(DEFAULTS)
    if not isinstance(raw, dict):
        return cfg
    if isinstance(raw.get("enabled"), bool):
        cfg["enabled"] = raw["enabled"]
    try:
        cfg["server"] = clean_server(raw.get("server"))
    except ValueError:
        pass
    if isinstance(raw.get("topic"), str) and TOPIC_RE.match(raw["topic"]):
        cfg["topic"] = raw["topic"]
    if raw.get("lang") in LANGS:
        cfg["lang"] = raw["lang"]
    for k, v in (raw.get("events") or {}).items() if isinstance(raw.get("events"), dict) else ():
        if k in cfg["events"] and isinstance(v, bool):
            cfg["events"][k] = v
    for k, v in (raw.get("since") or {}).items() if isinstance(raw.get("since"), dict) else ():
        if k in cfg["since"] and isinstance(v, (int, float)) and not isinstance(v, bool):
            cfg["since"][k] = float(v)
    for k, v in (raw.get("thresholds") or {}).items() if isinstance(raw.get("thresholds"), dict) else ():
        if k in cfg["thresholds"]:
            try:
                cfg["thresholds"][k] = _int(v, k)
            except ValueError:
                pass
    if isinstance(raw.get("updated_at"), (int, float)) and not isinstance(raw.get("updated_at"), bool):
        cfg["updated_at"] = float(raw["updated_at"])
    if not cfg["topic"]:
        cfg["enabled"] = False      # nothing can be pushed without a topic
    return cfg


def load(data: Path | str | None = None) -> dict:
    """The config, DEFAULTS merged in. An unreadable file means the defaults (off). Writes nothing."""
    return _merge(datadir.read_json(_data(data) / FILE, None))


def save(changes: dict, data: Path | str | None = None, now: float | None = None) -> dict:
    """Validate and store `changes` (enabled, server, topic, lang, events, thresholds; other keys are ignored).
    A group's `since` becomes now when that group becomes effectively on, so turning alerts on does not push
    that group's old events. Raises a Chinese ValueError; a rejected value is never stored."""
    if not isinstance(changes, dict):
        raise ValueError("bad body")
    data = _data(data)
    now = time.time() if now is None else float(now)
    old = load(data)
    cfg = copy.deepcopy(old)
    if "server" in changes:
        cfg["server"] = clean_server(changes["server"])
    if "topic" in changes:
        topic = changes["topic"]
        if not isinstance(topic, str) or not TOPIC_RE.match(topic):
            raise ValueError(BAD_TOPIC)
        cfg["topic"] = topic
    if "lang" in changes:
        if changes["lang"] not in LANGS:
            raise ValueError(BAD_LANG)
        cfg["lang"] = changes["lang"]
    if "thresholds" in changes:
        th = changes["thresholds"]
        if not isinstance(th, dict):
            raise ValueError(BAD_NUMBER)
        for k, v in th.items():
            if k in cfg["thresholds"]:
                cfg["thresholds"][k] = _int(v, k)
    if isinstance(changes.get("events"), dict):
        for k, v in changes["events"].items():
            if k in cfg["events"] and isinstance(v, bool):
                cfg["events"][k] = v
    if "enabled" in changes:
        cfg["enabled"] = bool(changes["enabled"])
        if cfg["enabled"] and not cfg["topic"]:
            raise ValueError(NO_TOPIC)
    for g in GROUPS:
        if cfg["enabled"] and cfg["events"][g] and not (old["enabled"] and old["events"][g]):
            cfg["since"][g] = now
    if cfg != old:
        cfg["updated_at"] = now
        datadir.write_json(data / FILE, cfg)
    return cfg


def new_topic() -> str:
    return "crossview-" + secrets.token_urlsafe(18)


def ensure_topic(data: Path | str | None = None) -> dict:
    """The config, with a new random topic written once when there is none."""
    cfg = load(data)
    if not cfg["topic"]:
        cfg = save({"topic": new_topic()}, data)
    return cfg


def _keys(data: Path | str | None):
    from .secrets import KeyStore

    return KeyStore(_data(data) / "keys.json")


def token(data: Path | str | None = None) -> str:
    try:
        return _keys(data).get(TOKEN_NAME) or ""
    except Exception:  # noqa: BLE001  (a broken key file means no token)
        return ""


def set_token(value: str, data: Path | str | None = None) -> None:
    """Store the ntfy access token (encrypted on Windows); "" clears it."""
    value = (value if isinstance(value, str) else "").strip()
    if value and not TOKEN_RE.match(value):
        raise ValueError(BAD_TOKEN)
    _keys(data).set(TOKEN_NAME, value)


# -- texts and publishing --------------------------------------------------------------------------------
def render(kind: str, lang: str, ref: str = "", extra: dict | None = None) -> tuple[str, str]:
    """(title, message) of a push, filled only with ref, n, minutes and hours."""
    texts = MESSAGES[kind]
    title, message = texts[lang if lang in texts else "zh"]
    values = {f: "" for f in FIELDS}
    values["ref"] = str(ref or "?")[:40]
    for f in FIELDS[1:]:
        if extra and f in extra:
            values[f] = str(extra[f])[:12]
    return title.format_map(values), message.format_map(values)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is an error: it could carry the token to another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401, ANN001
        return None


def _urlopen_sender(url: str, body: bytes, headers: dict, timeout: float) -> None:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    handlers: list = [_NoRedirect]
    if host in LOOPBACK:            # a local test server is never reached through a proxy
        handlers.append(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.build_opener(*handlers).open(req, timeout=timeout) as resp:
            code = getattr(resp, "status", 200)
            resp.read(4096)
    except urllib.error.HTTPError as e:
        e.close()                    # its response holds the connection
        raise NotifyError(f"HTTP {e.code}") from None
    if not 200 <= int(code) < 300:
        raise NotifyError(f"HTTP {code}")


def _describe(e: BaseException) -> str:
    if isinstance(e, NotifyError):
        return str(e) or "error"
    if isinstance(e, urllib.error.HTTPError):
        return f"HTTP {e.code}"
    if isinstance(e, (socket.timeout, TimeoutError)):
        return "network: timeout"
    if isinstance(e, urllib.error.URLError):
        reason = e.reason
        if isinstance(reason, (socket.timeout, TimeoutError)):
            return "network: timeout"
        return f"network: {reason}"
    return f"network: {e}" if str(e) else f"network: {type(e).__name__}"


def publish(cfg: dict, title: str, message: str, kind: str, *, token: str = "", sender: Sender | None = None,
            timeout: float = SEND_TIMEOUT) -> None:
    """POST one push to <server>/ as ntfy JSON. Raises NotifyError ("HTTP 403", "network: ...")."""
    url = str(cfg.get("server") or DEFAULT_SERVER).rstrip("/") + "/"
    body = json.dumps({"topic": cfg.get("topic", ""), "title": title, "message": message,
                       "priority": PRIORITY[kind], "tags": [TAGS[kind]]}, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        (sender or _urlopen_sender)(url, body, headers, timeout)
    except Exception as e:  # noqa: BLE001  (every failure becomes a short NotifyError)
        text = _describe(e)
        if token:
            text = text.replace(token, "***")
        raise NotifyError(text[:200]) from None


def idle_seconds() -> float | None:
    """Windows: seconds since the last keyboard or mouse input in this session; elsewhere, or on any error, None
    (= unknown, which never blocks a push)."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32   # type: ignore[attr-defined]
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        kernel32.GetTickCount.restype = wintypes.DWORD
        tick = int(kernel32.GetTickCount())
        return float(((tick - int(info.dwTime)) & 0xFFFFFFFF) / 1000.0)   # the 49.7-day wrap is harmless
    except Exception:  # noqa: BLE001
        return None


# -- the sent log ----------------------------------------------------------------------------------------
def _read_log(data: Path) -> dict:
    raw = datadir.read_json(data / SENT_FILE, None)
    log = raw if isinstance(raw, dict) else {}
    keys = log.get("keys") if isinstance(log.get("keys"), dict) else {}
    window = [float(t) for t in log.get("window") or [] if isinstance(t, (int, float)) and not isinstance(t, bool)] \
        if isinstance(log.get("window"), list) else []
    recent_ = [r for r in log.get("recent") or [] if isinstance(r, dict)] if isinstance(log.get("recent"), list) else []
    return {"keys": {k: v for k, v in keys.items() if isinstance(v, dict)}, "window": window, "recent": recent_}


def _drop(data: Path, entry: dict) -> None:
    """Append one line to notify_drops.jsonl without the lock (one write on an O_APPEND descriptor)."""
    try:
        data.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(data / DROPS_FILE), os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0),
                     0o600)
        try:
            os.write(fd, (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8"))
        finally:
            os.close(fd)
    except OSError:
        pass


def _parse_drops(raw: bytes) -> list[dict]:
    out = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _fold_drops(data: Path) -> list[dict]:
    """Take the drops file away (rename, read, delete). When the rename fails (Windows: someone is appending right
    now), it stays for the next write."""
    src = data / DROPS_FILE
    if not src.exists():
        return []
    tmp = data / f"notify_drops.{os.getpid()}.tmp"
    try:
        os.replace(src, tmp)
    except OSError:
        return []
    try:
        return _parse_drops(tmp.read_bytes())
    except OSError:
        return []
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _by_time(entries: list[dict]) -> list[dict]:
    def at(r: dict) -> float:
        v = r.get("at")
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0

    return sorted(entries, key=at, reverse=True)


def _write_log(data: Path, log: dict, now: float, add: list[dict] | None = None) -> None:
    """Fold the drops, add entries to recent, prune old keys and the window, write (under the lock)."""
    log["recent"] = _by_time(list(add or []) + _fold_drops(data) + log["recent"])[:RECENT_MAX]
    cut = now - KEEP_DAYS * 86400
    log["keys"] = {k: v for k, v in log["keys"].items()
                   if float(v.get("last") or v.get("first") or 0) >= cut}
    log["window"] = [t for t in log["window"] if t > now - RATE[1]]
    datadir.write_json(data / SENT_FILE, log)


def done_keys(data: Path | str | None = None) -> set[str]:
    """Keys that were pushed or given up. Writes nothing."""
    keys = _read_log(_data(data))["keys"]
    return {k for k, v in keys.items() if v.get("ok") or v.get("gave_up")}


def retry_due(data: Path | str | None = None, now: float | None = None) -> list[dict]:
    """Claude Code keys that failed, waited on the rate limit or were left pending, whose time has come and whose
    event is younger than CC_FRESH_S: the hook has no other source to retry them from. Writes nothing."""
    now = time.time() if now is None else float(now)
    out = []
    for key, v in _read_log(_data(data))["keys"].items():
        if v.get("kind") not in PRESENCE_KINDS or v.get("ok") or v.get("gave_up"):
            continue
        at = float(v.get("at") or 0)
        if now - at >= CC_FRESH_S:
            continue
        if v.get("pending"):
            if now - float(v.get("last") or 0) < PENDING_S:
                continue
        elif not v.get("next") or float(v["next"]) > now:
            continue
        out.append({"key": key, "kind": v["kind"], "ref": str(v.get("ref") or ""), "at": at,
                    "extra": v.get("extra") if isinstance(v.get("extra"), dict) else {}})
    return out


def recent(data: Path | str | None = None, limit: int = 20) -> list[dict]:
    """The newest recorded pushes (drops not folded yet included), newest first. Writes nothing."""
    data = _data(data)
    entries = _read_log(data)["recent"]
    try:
        entries = entries + _parse_drops((data / DROPS_FILE).read_bytes())
    except OSError:
        pass
    out = []
    for r in _by_time(entries)[:max(int(limit), 0)]:
        out.append({"at": r.get("at"), "kind": str(r.get("kind") or ""), "ref": str(r.get("ref") or ""),
                    "ok": bool(r.get("ok")), "error": str(r.get("error") or ""), "via": str(r.get("via") or "")})
    return out


# -- one push --------------------------------------------------------------------------------------------
def _send_and_record(cfg: dict, data: Path, kind: str, key: str | None, ref: str, extra: dict | None,
                     sender: Sender | None, via: str, now: float | None, timeout: float) -> str:
    """Send outside the lock, then record the result under it. Returns the error text ("" = sent)."""
    title, message = render(kind, cfg["lang"], ref, extra)
    err = ""
    try:
        publish(cfg, title, message, kind, token=token(data), sender=sender, timeout=timeout)
    except NotifyError as e:
        err = str(e) or "error"
    t = time.time() if now is None else now
    entry = {"at": t, "kind": kind, "ref": ref, "ok": not err, "error": err, "via": via}
    with datadir.file_lock(data / LOCK_FILE, LOCK_S) as held:
        if not held:     # the key stays pending and is tried again after PENDING_S (better twice than never)
            _drop(data, entry)
            return err
        log = _read_log(data)
        if key is not None:
            k = log["keys"].setdefault(key, {"kind": kind, "ref": ref, "at": t, "extra": extra or {}, "first": t,
                                             "last": t, "tries": 1, "ok": False, "pending": True, "next": 0,
                                             "gave_up": False})
            k["pending"] = False
            if not err:
                k["ok"] = True
            else:
                nxt = t + BACKOFF[min(max(int(k.get("tries") or 1), 1), len(BACKOFF)) - 1]
                if nxt - float(k.get("first") or t) > GIVE_UP_S:
                    k["gave_up"], k["next"] = True, 0
                else:
                    k["next"] = nxt
        _write_log(data, log, t, [entry])
    return err


def notify(kind: str, key: str, *, ref: str = "", at: float | None = None, extra: dict | None = None,
           data: Path | str | None = None, sender: Sender | None = None, via: str = "", now: float | None = None,
           timeout: float = SEND_TIMEOUT, idle: Callable[[], float | None] | None = idle_seconds) -> dict:
    """Push one event once: {"sent": bool, "reason": "" | "off" | "old" | "present" | "dup" | "later" |
    "rate_limited" | "busy" | "error"}. Never raises."""
    try:
        return _notify(kind, key, ref=ref, at=at, extra=extra, data=_data(data), sender=sender, via=via,
                       now=now, timeout=timeout, idle=idle)
    except Exception as e:  # noqa: BLE001  (a push never breaks its caller)
        _say(f"Crossview phone alerts: {type(e).__name__}: {e}")
        return {"sent": False, "reason": "error"}


def _notify(kind: str, key: str, *, ref: str, at: float | None, extra: dict | None, data: Path,
            sender: Sender | None, via: str, now: float | None, timeout: float,
            idle: Callable[[], float | None] | None) -> dict:
    if kind not in KINDS:
        return {"sent": False, "reason": "error"}
    t = time.time() if now is None else float(now)
    at = t if at is None else float(at)
    ref = str(ref or "")[:40]
    extra = {k: v for k, v in (extra or {}).items() if k in FIELDS[1:]}
    cfg = load(data)
    if kind == "test":
        r = send_test(data, sender=sender, via=via, now=now)
        return {"sent": r["ok"], "reason": "" if r["ok"] else "error"}
    # 1. config, age and presence (writes nothing on these answers)
    group = GROUP[kind]
    if not cfg["enabled"] or not cfg["topic"] or not cfg["events"].get(group):
        return {"sent": False, "reason": "off"}
    if at < float(cfg["since"].get(group) or 0) or at < t - KEEP_DAYS * 86400:
        return {"sent": False, "reason": "old"}
    away = int(cfg["thresholds"]["cc_away_minutes"])
    if kind in PRESENCE_KINDS and away > 0 and idle is not None:
        try:
            seconds = idle()
        except Exception:  # noqa: BLE001
            seconds = None
        if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and seconds < away * 60:
            return {"sent": False, "reason": "present"}
    # 2. dedup, backoff and the rate limit, under the lock
    with datadir.file_lock(data / LOCK_FILE, LOCK_S) as held:
        if not held:
            _drop(data, {"at": t, "kind": kind, "ref": ref, "ok": False, "error": "busy", "via": via})
            return {"sent": False, "reason": "busy"}
        log = _read_log(data)
        k = log["keys"].get(key)
        if k is not None:
            if k.get("ok") or k.get("gave_up"):
                return {"sent": False, "reason": "dup"}
            if k.get("pending") and t - float(k.get("last") or 0) < PENDING_S:
                return {"sent": False, "reason": "dup"}
            if float(k.get("next") or 0) > t:
                return {"sent": False, "reason": "later"}
        window = [w for w in log["window"] if w > t - RATE[1]]
        if len(window) >= RATE[0]:
            was_limited = bool(k and k.get("limited"))
            k = log["keys"].setdefault(key, {"tries": 0, "ok": False, "pending": False, "gave_up": False})
            k.update(kind=kind, ref=ref, at=at, extra=extra, pending=False, limited=True, next=min(window) + RATE[1])
            k.setdefault("first", t)
            if k["next"] - float(k["first"]) > GIVE_UP_S:
                k["gave_up"], k["next"] = True, 0
            log["window"] = window
            add = [] if was_limited else [{"at": t, "kind": kind, "ref": ref, "ok": False, "error": "rate_limited",
                                           "via": via}]
            _write_log(data, log, t, add)
            return {"sent": False, "reason": "rate_limited"}
        k = log["keys"].setdefault(key, {"tries": 0, "ok": False, "next": 0, "gave_up": False})
        k.update(kind=kind, ref=ref, at=at, extra=extra, pending=True, limited=False, last=t,
                 tries=int(k.get("tries") or 0) + 1)
        k.setdefault("first", t)
        window.append(t)
        log["window"] = window
        _write_log(data, log, t)
    # 3. and 4. send outside the lock, record under it
    err = _send_and_record(cfg, data, kind, key, ref, extra, sender, via, now, timeout)
    return {"sent": not err, "reason": "" if not err else "error"}


def send_test(data: Path | str | None = None, sender: Sender | None = None, via: str = "",
              now: float | None = None) -> dict:
    """One test push, even while alerts are off (no dedup, backoff, rate limit or presence guard); recorded in
    recent. {"ok": bool, "error": str}."""
    try:
        data = _data(data)
        cfg = load(data)
        if not cfg["topic"]:
            return {"ok": False, "error": NO_TOPIC}
        err = _send_and_record(cfg, data, "test", None, "", None, sender, via, now, SEND_TIMEOUT)
        return {"ok": not err, "error": err}
    except Exception as e:  # noqa: BLE001
        _say(f"Crossview phone alerts: {type(e).__name__}: {e}")
        return {"ok": False, "error": f"{type(e).__name__}"}


def subscribe_url(cfg: dict) -> str:
    return f"{cfg['server'].rstrip('/')}/{cfg['topic']}" if cfg.get("topic") else ""


def public(data: Path | str | None = None) -> dict:
    """What the settings page shows (the hook state and the watcher are added by the web app). Never the token.
    Writes nothing."""
    cfg = load(data)
    try:
        token_set = _keys(data).has(TOKEN_NAME)
    except Exception:  # noqa: BLE001
        token_set = False
    return {"enabled": cfg["enabled"], "server": cfg["server"], "topic": cfg["topic"],
            "subscribe_url": subscribe_url(cfg), "token_set": bool(token_set), "lang": cfg["lang"],
            "events": dict(cfg["events"]), "thresholds": dict(cfg["thresholds"]),
            "limits": {k: list(v) for k, v in LIMITS.items()},
            "gpt_available": importlib.util.find_spec("gptbridge") is not None,
            "recent": recent(data, 20)}
