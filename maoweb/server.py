"""HTTP server for Crossview web. Standard library only (http.server), one thread per request.

Security model (it runs on the owner's PC and may be reached from outside through a private
tunnel such as Tailscale):
- binds to 127.0.0.1 unless told otherwise; never meant to face the open internet directly
- every page except login / first-time setup needs a session cookie (HttpOnly, SameSite=Strict)
- every state-changing request must carry the X-MAO header, which other sites cannot add
  without a CORS preflight that this server never grants (CSRF protection)
- passwords: PBKDF2-SHA256; repeated wrong passwords lock that name for 10 minutes
- API keys: only admins can set them; they are never sent back to any browser
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import sys
import traceback
from contextlib import closing
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))  # orch.llm lives next door

from orch import legacy, llm, notify, notify_hook, watch  # noqa: E402
from orch.context import estimate_tokens  # noqa: E402

from . import agents as agent_page  # noqa: E402
from .discuss import ADMIN_ONLY, UNKNOWN, Discussions  # noqa: E402
from .docs import Docs  # noqa: E402
from .db import DB, data_dir, get_media  # noqa: E402
from .jobs import Jobs  # noqa: E402
from .landing import Changed, Landing  # noqa: E402
from .media import IMAGE_SIZES, VIDEO_RATIOS, Media, public  # noqa: E402
from .projects import Projects  # noqa: E402
from .providers import BY_ID, Registry  # noqa: E402
from .remote import client_allowed, tailscale_ips  # noqa: E402
from .subscription import Subscription  # noqa: E402
from orch.secrets import KeyStore  # noqa: E402

STATIC = ROOT / "static"
mimetypes.add_type("application/manifest+json", ".webmanifest")
COOKIE = "mao_session"
MAX_BODY = 2_000_000
MAX_UPLOAD_BODY = 16_000_000   # a 10 MB picture as base64
HISTORY_BUDGET = 12_000          # tokens of earlier conversation sent with a new message
SYSTEM_PROMPT = ("你是一个耐心、可靠的助手。回答用对方使用的语言，先给结论，再给必要的步骤。"
                 "不确定的事情直说不确定。对方可能不太懂电脑，避免术语，必要时一步一步说明。")
NAME_RE = re.compile(r"^[A-Za-z0-9_一-鿿-]{1,32}$")


def conversation_title(text: str) -> str:
    """The first line of the first message, without Markdown marks, as a short title."""
    line = next((ln for ln in text.splitlines() if ln.strip()), "")
    line = re.sub(r"^\s*(?:#+|>|[-*+]|\d+[.)])\s+", "", line)
    line = re.sub(r"[*_`~]+", "", line)
    return re.sub(r"\s+", " ", line).strip()[:30] or "新对话"


class App:
    def __init__(self, data: Path | None = None, environ: dict[str, str] | None = None):
        self.data = data or data_dir()
        self.db = DB(self.data / "web.db")
        self.keys = KeyStore(self.data / "keys.json")
        env = os.environ if environ is None else environ
        self.environ = env                     # the legacy switch's env override is read from here
        self.subscription = Subscription(env, self.data / "claude-chat")
        self.registry = Registry(self.db, self.keys, env, self.subscription)
        self.allow_lan = False                 # set by --lan
        self.media = Media(self)
        self.projects = Projects(self)         # before the jobs: they look projects up at startup
        self.jobs = Jobs(self)
        self.discussions = Discussions(self)   # GPT and Claude discuss, the owner decides (admin only)
        self.landing = Landing(self)
        self.docs = Docs(self)
        self.media.resume()
        self.addresses: list[str] = []         # where the server listens (shown on the settings page)
        self.notify_sender = None              # phone alerts: None = the real ntfy request (tests put a fake here)

    def history_for(self, cid: int, new_text: str) -> list[dict[str, str]]:
        """Minimum necessary context: newest messages first, until HISTORY_BUDGET is used up."""
        msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
        budget = HISTORY_BUDGET - estimate_tokens(new_text)
        kept: list[dict[str, str]] = []
        for m in reversed(self.db.messages(cid)):
            cost = estimate_tokens(m["content"])
            if cost > budget:
                break
            budget -= cost
            kept.append({"role": m["role"], "content": m["content"]})
        return msgs + list(reversed(kept)) + [{"role": "user", "content": new_text}]


class Handler(BaseHTTPRequestHandler):
    app: App
    server_version = "Crossview-web"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:  # keep request lines out of the console (no paths with data)
        pass

    # -- helpers ------------------------------------------------------------------
    def _json(self, code: int, obj) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._started = True                    # from here on an error can only close the connection
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        for c in getattr(self, "_set_cookies", []):
            self.send_header("Set-Cookie", c)
        self.end_headers()
        self.wfile.write(body)

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; "
                         "frame-ancestors 'none'")

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        limit = MAX_UPLOAD_BODY if urlparse(self.path).path in ("/api/uploads", "/api/docs/upload") else MAX_BODY
        if n > limit:
            raise ValueError("请求太大")
        raw = self.rfile.read(n) if n else b"{}"
        data = json.loads(raw.decode("utf-8") or "{}")
        if not isinstance(data, dict):
            raise ValueError("bad body")
        return data

    def _token(self) -> str | None:
        jar = cookies.SimpleCookie(self.headers.get("Cookie") or "")
        return jar[COOKIE].value if COOKIE in jar else None

    def _user(self):
        return self.app.db.session_user(self._token())

    def _cookie(self, token: str, max_age: int) -> str:
        secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
        return f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}{secure}"

    # -- routing ------------------------------------------------------------------
    def _client_ok(self) -> bool:
        if client_allowed(self.client_address[0], self.app.allow_lan):
            return True
        body = "只允许本机和你自己的 Tailscale 设备访问。".encode("utf-8")
        self.send_response(403)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return False

    def do_GET(self) -> None:
        if not self._client_ok():
            return
        path = urlparse(self.path).path
        m = re.fullmatch(r"/media/(\d+)", path)
        if m:
            return self._media_file(int(m.group(1)))
        m = re.fullmatch(r"/docs/(\d+)/([^/]+)", path)
        if m:
            return self._doc_file(int(m.group(1)), unquote(m.group(2)))
        if not path.startswith("/api/"):
            return self._static(path)
        self._api("GET", path)

    def do_POST(self) -> None:
        if self._client_ok():
            self._api("POST", urlparse(self.path).path)

    def do_DELETE(self) -> None:
        if self._client_ok():
            self._api("DELETE", urlparse(self.path).path)

    def _static(self, path: str) -> None:
        name = "index.html" if path in ("/", "/index.html") else path.lstrip("/")
        target = (STATIC / name).resolve()
        if STATIC.resolve() not in target.parents or not target.is_file():
            target = STATIC / "index.html"  # single-page app: unknown paths show the app
        body = target.read_bytes()
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _media_file(self, mid: int) -> None:
        user = self._user()
        row = get_media(self.app.db, mid, user["id"]) if user else None
        if not row or not row["file"] or not self.app.media.path(row).is_file():
            return self._json(404, {"error": "not found"})
        data = self.app.media.path(row).read_bytes()
        start, end, code = 0, len(data) - 1, 200
        rng = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range") or "")
        if rng and data:  # video seeking
            start = int(rng.group(1) or 0)
            end = min(int(rng.group(2)) if rng.group(2) else len(data) - 1, len(data) - 1)
            code = 206
        chunk = data[start:end + 1]
        self.send_response(code)
        self.send_header("Content-Type", row["mime"] or "application/octet-stream")
        self.send_header("Content-Length", str(len(chunk)))
        self.send_header("Accept-Ranges", "bytes")
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
        self.send_header("Cache-Control", "private, max-age=86400")
        self._security_headers()
        self.end_headers()
        self.wfile.write(chunk)

    def _doc_file(self, jid: int, name: str) -> None:
        user = self._user()
        path = self.app.docs.output(user["id"], jid, name) if user else None
        if path is None:
            return self._json(404, {"error": "not found"})
        data = path.read_bytes()
        ctype = {".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                 ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                 ".svg": "image/svg+xml", ".json": "application/json"}.get(path.suffix, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if path.suffix != ".svg":       # charts are shown on the page; the rest are downloads
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(path.name)}")
        self.send_header("Cache-Control", "private, no-cache")
        self._security_headers()
        self.end_headers()
        self.wfile.write(data)

    def _api(self, method: str, path: str) -> None:
        self._set_cookies, self._started = [], False
        try:
            if method != "GET" and self.headers.get("X-MAO") != "1":
                return self._json(403, {"error": "missing X-MAO header"})
            open_routes = {("GET", "/api/state"): self.api_state, ("POST", "/api/setup"): self.api_setup,
                           ("POST", "/api/login"): self.api_login}
            if (method, path) in open_routes:
                return open_routes[(method, path)]()
            user = self._user()
            if user is None:
                return self._json(401, {"error": "请先登录"})
            routes = {
                ("POST", "/api/logout"): self.api_logout,
                ("GET", "/api/models"): self.api_models,
                ("GET", "/api/conversations"): self.api_conversations,
                ("POST", "/api/chat"): self.api_chat,
                ("POST", "/api/password"): self.api_password,
                ("GET", "/api/creative"): self.api_creative,
                ("GET", "/api/media"): self.api_media_list,
                ("POST", "/api/uploads"): self.api_upload,
                ("POST", "/api/images"): self.api_image,
                ("POST", "/api/videos"): self.api_video,
                ("GET", "/api/projects"): self.api_projects,
                ("GET", "/api/project"): self.api_project,
                ("GET", "/api/jobs"): self.api_jobs,
                ("POST", "/api/jobs"): self.api_job_submit,
                ("GET", "/api/job"): self.api_job,
                ("GET", "/api/job/run"): self.api_job_run,
                ("GET", "/api/job/diff"): self.api_job_diff,
                ("POST", "/api/job/action"): self.api_job_action,
                ("POST", "/api/projects/land"): self.api_project_land,
                ("GET", "/api/docs"): self.api_docs,
                ("POST", "/api/docs"): self.api_doc_job,
                ("POST", "/api/docs/upload"): self.api_doc_upload,
                ("GET", "/api/discuss/options"): self.api_discuss_options,
                ("GET", "/api/discussions"): self.api_discussions,
                ("POST", "/api/discussions"): self.api_discussion_create,
                ("GET", "/api/discussion"): self.api_discussion,
                ("GET", "/api/discussion/raw"): self.api_discussion_raw,
                ("POST", "/api/discussion/action"): self.api_discussion_action,
            }
            m = re.fullmatch(r"/api/docs/(\d+)", path)
            if m and method == "DELETE":
                return self.api_doc_delete(user, int(m.group(1)))
            m = re.fullmatch(r"/api/media/(\d+)", path)
            if m and method == "DELETE":
                return self.api_media_delete(user, int(m.group(1)))
            m = re.fullmatch(r"/api/conversations/(\d+)", path)
            if m:
                return self.api_conversation(method, user, int(m.group(1)))
            if (method, path) in routes:
                return routes[(method, path)](user)
            if path.startswith("/api/admin/"):
                if not user["is_admin"]:
                    return self._json(403, {"error": "只有管理员可以操作"})
                admin = {("GET", "/api/admin/settings"): self.admin_settings,
                         ("GET", "/api/admin/remote"): self.admin_remote,
                         ("POST", "/api/admin/key"): self.admin_key,
                         ("POST", "/api/admin/models"): self.admin_models,
                         ("POST", "/api/admin/test"): self.admin_test,
                         ("POST", "/api/admin/users"): self.admin_users,
                         ("POST", "/api/admin/media-models"): self.admin_media_models,
                         ("POST", "/api/admin/projects"): self.admin_projects,
                         ("POST", "/api/admin/projects/check"): self.admin_project_check,
                         ("POST", "/api/admin/projects/add"): self.admin_project_add,
                         ("POST", "/api/admin/projects/rename"): self.admin_project_rename,
                         ("POST", "/api/admin/projects/remove"): self.admin_project_remove,
                         ("GET", "/api/admin/folders"): self.admin_folders,
                         ("GET", "/api/admin/agents"): self.admin_agents,
                         ("POST", "/api/admin/agents"): self.admin_agent_action,
                         ("GET", "/api/admin/legacy"): self.admin_legacy,
                         ("POST", "/api/admin/legacy"): self.admin_legacy_set,
                         ("POST", "/api/admin/projects/link"): self.admin_project_link,
                         ("GET", "/api/admin/notify"): self.admin_notify,
                         ("POST", "/api/admin/notify"): self.admin_notify_set}
                if (method, path) in admin:
                    return admin[(method, path)](user)
            return self._json(404, {"error": "not found"})
        except (ValueError, KeyError, json.JSONDecodeError) as e:
            return self._json(400, {"error": str(e)})
        except Exception as e:  # never drop the connection without an answer
            traceback.print_exc()
            if getattr(self, "_started", False):
                self.close_connection = True
                return
            try:
                self._json(500, {"error": f"Crossview 内部出错了，请稍后再试（{type(e).__name__}）"})
            except OSError:
                self.close_connection = True

    # -- open routes --------------------------------------------------------------
    def api_state(self) -> None:
        user = self._user()
        dispatch = self._dispatch()
        can_code = bool(user and dispatch and (user["is_admin"] or user["can_code"]))
        self._json(200, {"needs_setup": self.app.db.user_count() == 0,
                         "user": {"name": user["name"], "display": user["display"], "admin": bool(user["is_admin"]),
                                  "can_code": can_code, "dispatch": dispatch,
                                  "waiting": self.app.jobs.waiting_count(user) if can_code else 0,
                                  # discussions waiting for the admin (the 讨论 badge; 项目 keeps `waiting`)
                                  "discuss_waiting": self.app.discussions.waiting_count() if user["is_admin"] else 0}
                         if user else None})

    def api_setup(self) -> None:
        """First run only: create the admin account."""
        if self.app.db.user_count() > 0:
            return self._json(403, {"error": "已经设置过了"})
        b = self._body()
        name, pw = str(b.get("name", "")).strip(), str(b.get("password", ""))
        if not NAME_RE.match(name) or len(pw) < 8:
            return self._json(400, {"error": "用户名 1–32 个字（字母、数字、汉字），密码至少 8 位"})
        uid = self.app.db.add_user(name, pw, str(b.get("display") or name), admin=True)
        self._set_cookies = [self._cookie(self.app.db.new_session(uid), 30 * 86400)]
        self._json(200, {"ok": True})

    def api_login(self) -> None:
        b = self._body()
        name, pw = str(b.get("name", "")).strip(), str(b.get("password", ""))
        if self.app.db.recent_failures(name) >= 5:
            return self._json(429, {"error": "密码错误次数太多，请 10 分钟后再试"})
        user = self.app.db.check_password(name, pw)
        if not user:
            self.app.db.note_failure(name)
            return self._json(401, {"error": "用户名或密码不对"})
        self._set_cookies = [self._cookie(self.app.db.new_session(user["id"]), 30 * 86400)]
        self._json(200, {"ok": True})

    # -- signed-in routes ---------------------------------------------------------
    def api_logout(self, user) -> None:
        token = self._token()
        if token:
            self.app.db.end_session(token)
        self._set_cookies = [self._cookie("", 0)]
        self._json(200, {"ok": True})

    def api_password(self, user) -> None:
        b = self._body()
        if not self.app.db.check_password(user["name"], str(b.get("old", ""))):
            return self._json(400, {"error": "原密码不对"})
        new = str(b.get("new", ""))
        if len(new) < 8:
            return self._json(400, {"error": "新密码至少 8 位"})
        self.app.db.set_password(user["id"], new)
        self._set_cookies = [self._cookie(self.app.db.new_session(user["id"]), 30 * 86400)]
        self._json(200, {"ok": True})

    def api_models(self, user) -> None:
        self._json(200, {"models": self.app.registry.available(include_subscription=bool(user["is_admin"])),
                         "default": self.app.db.setting("default_model")})

    def api_conversations(self, user) -> None:
        self._json(200, {"conversations": [dict(r) for r in self.app.db.conversations(user["id"])]})

    def api_conversation(self, method: str, user, cid: int) -> None:
        conv = self.app.db.conversation(user["id"], cid)
        if conv is None:
            return self._json(404, {"error": "没有这个对话"})
        if method == "GET":
            return self._json(200, {"conversation": dict(conv),
                                    "messages": [dict(m) for m in self.app.db.messages(cid)]})
        if method == "DELETE":
            self.app.db.delete_conversation(cid)
            return self._json(200, {"ok": True})
        b = self._body()
        title = str(b.get("title", "")).strip()[:80]
        if title:
            self.app.db.rename(cid, title)
        self._json(200, {"ok": True})

    def api_chat(self, user) -> None:
        """Send one message; the answer streams back as server-sent events."""
        b = self._body()
        text = str(b.get("content", "")).strip()
        model_id = str(b.get("model", ""))
        if not text:
            return self._json(400, {"error": "消息是空的"})
        if model_id.startswith("claude_subscription/") and not user["is_admin"]:
            return self._json(403, {"error": "Claude 订阅仅供管理员本人使用"})
        try:
            provider, model, key = self.app.registry.resolve(model_id)
        except (KeyError, ValueError) as e:
            return self._json(400, {"error": str(e).strip("'")})
        cid = b.get("conversation_id")
        if cid:
            if self.app.db.conversation(user["id"], int(cid)) is None:
                return self._json(404, {"error": "没有这个对话"})
            cid = int(cid)
            messages = self.app.history_for(cid, text)
        else:
            cid = self.app.db.new_conversation(user["id"], conversation_title(text), model_id)
            messages = self.app.history_for(cid, text)
        self.app.db.add_message(cid, "user", text, model_id)
        self.app.db.set_setting(f"last_model.{user['id']}", model_id)

        self._started = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self._security_headers()
        self.end_headers()
        self.close_connection = True

        def send(event: str, data) -> None:
            self.wfile.write(f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8"))
            self.wfile.flush()

        send("start", {"conversation_id": cid})
        parts: list[str] = []
        try:
            stream = (self.app.subscription.chat_stream(model, messages, timeout=300) if provider.transport == "cli"
                      else llm.chat_stream(self.app.registry.base_url(provider.id), key, model, messages, timeout=300,
                                           headers=self.app.registry.headers(provider.id, key)))
            with closing(stream):
                for kind, value in stream:
                    if kind == "delta":
                        parts.append(value)
                        send("delta", {"text": value})
                    elif kind == "done":
                        self.app.db.add_message(cid, "assistant", value.text, model_id, value.tokens_in, value.tokens_out)
                        send("done", {"tokens_in": value.tokens_in, "tokens_out": value.tokens_out})
                    else:
                        send("ping", {})
        except llm.LLMError as e:
            hint = {"auth": "API key 不对或已失效，请管理员在设置里检查",
                    "rate_limit": "调用太频繁或余额不足，请稍后再试",
                    "timeout": "模型太久没有回答，请再试一次"}.get(e.kind, "调用模型失败")
            if provider.transport == "cli":
                hint = str(e)
            if parts:  # keep what arrived
                self.app.db.add_message(cid, "assistant", "".join(parts) + "\n\n（回答中断）", model_id)
            send("error", {"error": hint, "detail": str(e)[:300]})
        except (BrokenPipeError, ConnectionResetError):  # the browser went away
            if parts:
                self.app.db.add_message(cid, "assistant", "".join(parts) + "\n\n（回答中断）", model_id)

    # -- pictures and video -------------------------------------------------------------
    def api_creative(self, user) -> None:
        key, _ = self.app.registry.key("doubao")
        self._json(200, {"ready": bool(key), "image_models": self.app.media.models("image"),
                         "video_models": self.app.media.models("video"), "sizes": IMAGE_SIZES, "ratios": VIDEO_RATIOS})

    def api_media_list(self, user) -> None:
        kind = (urlparse(self.path).query.partition("kind=")[2] or "").split("&")[0]
        sql = "SELECT * FROM media WHERE user_id=?" + (" AND kind=?" if kind else "") + " ORDER BY id DESC LIMIT 200"
        rows = self.app.db.q(sql, (user["id"], kind) if kind else (user["id"],))
        self._json(200, {"items": [public(r) for r in rows]})

    def api_upload(self, user) -> None:
        mid = self.app.media.upload(user["id"], str(self._body().get("data", "")))
        self._json(200, {"item": public(get_media(self.app.db, mid))})

    def api_image(self, user) -> None:
        b = self._body()
        prompt = str(b.get("prompt", "")).strip()
        if not prompt:
            return self._json(400, {"error": "请写下想要的画面"})
        mid = self.app.media.image(user["id"], prompt, str(b.get("model", "")), str(b.get("size", "1024x1024")),
                                   int(b["source_id"]) if b.get("source_id") else None)
        self._json(200, {"item": public(get_media(self.app.db, mid))})

    def api_video(self, user) -> None:
        b = self._body()
        prompt = str(b.get("prompt", "")).strip()
        if not prompt:
            return self._json(400, {"error": "请写下想要的视频内容"})
        mid = self.app.media.video(user["id"], prompt, str(b.get("model", "")), str(b.get("ratio", "16:9")),
                                   int(b.get("duration") or 5), int(b["source_id"]) if b.get("source_id") else None)
        self._json(200, {"item": public(get_media(self.app.db, mid))})

    def api_media_delete(self, user, mid: int) -> None:
        row = get_media(self.app.db, mid, user["id"])
        if not row:
            return self._json(404, {"error": "not found"})
        if row["file"]:
            self.app.media.path(row).unlink(missing_ok=True)
        self.app.db.x("DELETE FROM media WHERE id=?", (mid,))
        self._json(200, {"ok": True})

    # -- projects and jobs (the old dispatch: only while the legacy switch is on) -------------------------------
    def _dispatch(self) -> bool:
        return legacy.enabled(self.app.data, self.app.environ)

    def _legacy_off(self) -> bool:
        """409 while the old dispatch is switched off (True when it answered)."""
        if self._dispatch():
            return False
        self._json(409, {"error": legacy.WEB_OFF, "code": "legacy_off"})
        return True

    def _can_code(self, user) -> bool:
        return bool(user["is_admin"] or user["can_code"])

    def _refused(self, user) -> bool:
        """409 while the old dispatch is off, else 403 unless the admin lets this person give work to the agents."""
        if self._legacy_off():
            return True
        if self._can_code(user):
            return False
        self._json(403, {"error": "管理员还没有允许你派写程序的任务"})
        return True

    def _query(self) -> dict[str, str]:
        from urllib.parse import parse_qs

        return {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}

    @staticmethod
    def _id(value, missing: str) -> int:
        """A record id from the query or body; anything that is not a number is a record that does not exist."""
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            raise ValueError(missing) from None

    def api_projects(self, user) -> None:
        if not self._refused(user):
            self._json(200, {"can_add": bool(user["is_admin"]), "projects": self.app.jobs.cards(user)})

    def api_project(self, user) -> None:
        if not self._refused(user):
            self._json(200, self.app.jobs.project_page(user, self._id(self._query().get("id"), "没有这个项目")))

    def api_jobs(self, user) -> None:
        if self._refused(user):
            return
        cid = self._id(self._query().get("conversation_id"), "没有这个对话")
        if not self.app.db.conversation(user["id"], cid):
            return self._json(404, {"error": "没有这个对话"})
        self._json(200, {"jobs": self.app.jobs.for_conversation(user, cid)})

    def api_job_submit(self, user) -> None:
        if not self._refused(user):
            self._json(200, {"job_id": self.app.jobs.submit(user, self._body())})

    def api_job(self, user) -> None:
        if not self._refused(user):
            q = self._query()
            self._json(200, self.app.jobs.view(user, self._id(q.get("id"), "没有这个任务"), q.get("sig", "")))

    def api_job_run(self, user) -> None:
        if not self._refused(user):
            q = self._query()
            self._json(200, self.app.jobs.run_detail(user, self._id(q.get("id"), "没有这个任务"), q.get("run", "")))

    def api_job_diff(self, user) -> None:
        if not self._refused(user):
            q = self._query()
            self._json(200, self.app.jobs.diff(user, self._id(q.get("id"), "没有这个任务"), q.get("task", "")))

    def api_job_action(self, user) -> None:
        if self._refused(user):
            return
        b = self._body()
        jid = self._id(b.get("job_id"), "没有这个任务")
        self._json(200, self.app.jobs.action(user, jid, str(b.get("action") or ""), str(b.get("task_id") or ""),
                                             str(b.get("text") or "")))

    def api_project_land(self, user) -> None:
        if not self._refused(user):
            b = self._body()
            pid = self._id(b.get("project_id"), "没有这个项目")
            try:
                self._json(200, self.app.landing.land(user, pid, str(b.get("sha") or "")))
            except Changed as e:   # the list the user agreed to is not what would land now: the new one with it
                self._json(400, {"error": str(e), "land": e.land})

    # -- discussions (admin only): GPT and Claude discuss, the owner decides, Cowork takes the brief ----------------
    def _admin_only(self, user) -> bool:
        """403 unless this is the admin (discussions spend the owner's own Max and ChatGPT quota)."""
        if user["is_admin"]:
            return False
        self._json(403, {"error": ADMIN_ONLY})
        return True

    def api_discuss_options(self, user) -> None:
        if not self._admin_only(user):
            self._json(200, self.app.discussions.options(user))

    def api_discussions(self, user) -> None:
        if not self._admin_only(user):
            self._json(200, {"discussions": self.app.discussions.list(user)})

    def api_discussion_create(self, user) -> None:
        if not self._admin_only(user):
            self._json(200, self.app.discussions.create(user, self._body()))

    def api_discussion(self, user) -> None:
        if not self._admin_only(user):
            q = self._query()
            self._json(200, self.app.discussions.view(user, q.get("did", ""), q.get("sig", "")))

    def api_discussion_raw(self, user) -> None:
        if not self._admin_only(user):
            q = self._query()
            self._json(200, self.app.discussions.raw(user, q.get("did", ""), q.get("turn", ""),
                                                     q.get("repair", "0") not in ("", "0", "false")))

    def api_discussion_action(self, user) -> None:
        if self._admin_only(user):
            return
        b = self._body()
        self._json(200, self.app.discussions.action(user, str(b.get("did") or ""), str(b.get("action") or ""),
                                                    str(b.get("text") or ""), str(b.get("kind") or ""),
                                                    str(b.get("ref") or "")))

    # -- documents ------------------------------------------------------------------
    def api_docs(self, user) -> None:
        from .templates import public as templates

        self._json(200, {**self.app.docs.ready(), "templates": templates(), "jobs": self.app.docs.jobs(user["id"])})

    def api_doc_upload(self, user) -> None:
        b = self._body()
        self._json(200, self.app.docs.upload(user["id"], str(b.get("name", "")), str(b.get("data", ""))))

    def api_doc_job(self, user) -> None:
        self._json(200, {"job_id": self.app.docs.submit(user["id"], self._body())})

    def api_doc_delete(self, user, jid: int) -> None:
        if not self.app.docs.delete(user["id"], jid):
            return self._json(404, {"error": "没有这个记录，或者还没做完"})
        self._json(200, {"ok": True})

    # -- admin --------------------------------------------------------------------
    def admin_agents(self, user) -> None:
        self._json(200, agent_page.overview(self.app, dispatch=self._dispatch()))

    def admin_agent_action(self, user) -> None:
        if not self._legacy_off():
            self._json(200, {"ok": True, "message": agent_page.act(self.app, self._body())})

    def _legacy_payload(self) -> dict:
        st = legacy.state(self.app.data, self.app.environ)
        return {"enabled": st["enabled"], "source": st["source"], "env": legacy.ENV,
                "active_jobs": self.app.jobs.active_count()}

    def admin_legacy(self, user) -> None:
        self._json(200, self._legacy_payload())

    def admin_legacy_set(self, user) -> None:
        on = self._body().get("enabled")
        if not isinstance(on, bool):
            raise ValueError("bad body")
        legacy.set_enabled(on, self.app.data, self.app.environ, by="web")   # ENV_LOCKED: a 400
        self._json(200, self._legacy_payload())

    # -- phone alerts (orch/notify.py): ntfy, read-only; the topic is made on the first look -----------------
    def _notify_payload(self) -> dict:
        notify.ensure_topic(self.app.data)
        out = notify.public(self.app.data)
        out["hook"] = {"state": notify_hook.status(data=self.app.data)}
        out["watcher"] = dict(watch.STATE)
        return out

    def admin_notify(self, user) -> None:
        self._json(200, self._notify_payload())

    def admin_notify_set(self, user) -> None:
        b = self._body()
        action, data, extra = b.get("action"), self.app.data, {}
        if action == "save":
            notify.save({k: b[k] for k in ("enabled", "server", "lang", "events", "thresholds") if k in b}, data)
        elif action == "new_topic":   # the phone has to subscribe again (the page says so)
            notify.save({"topic": notify.new_topic()}, data)
        elif action == "token":
            notify.set_token(str(b.get("token") or ""), data)
        elif action == "test":        # a failed send is still a 200, as with admin_test
            extra["test"] = notify.send_test(data, sender=self.app.notify_sender, via="web")
        elif action in ("hook_install", "hook_uninstall"):
            r = notify_hook.install(data=data) if action == "hook_install" else notify_hook.uninstall()
            extra["hook_result"] = {"changed": r["changed"], "backup": Path(r["backup"]).name if r["backup"] else ""}
        else:
            raise ValueError(UNKNOWN)
        self._json(200, {**self._notify_payload(), **extra, "ok": True})

    def admin_settings(self, user) -> None:
        self._json(200, {"providers": self.app.registry.describe(),
                         "users": [dict(u) for u in self.app.db.users()],
                         "default_model": self.app.db.setting("default_model"),
                         "image_models": self.app.media.models("image"), "video_models": self.app.media.models("video")})

    def admin_media_models(self, user) -> None:
        b = self._body()
        kind = str(b.get("kind", ""))
        if kind not in ("image", "video"):
            return self._json(400, {"error": "kind 只能是 image 或 video"})
        models = [m.strip() for m in b.get("models", []) if isinstance(m, str) and m.strip()]
        self.app.db.set_setting(f"models.{kind}", models[:20])
        self._json(200, {"ok": True, "models": models[:20]})

    def admin_projects(self, user) -> None:
        if not self._legacy_off():
            self._json(200, {"ok": True, "projects": self.app.projects.set_all(self._body().get("projects"))})

    def admin_project_check(self, user) -> None:
        if not self._legacy_off():
            self._json(200, self.app.projects.check(str(self._body().get("path") or "")))

    def admin_project_add(self, user) -> None:
        if self._legacy_off():
            return
        b = self._body()
        self._json(200, {"project": self.app.projects.add(
            str(b.get("path") or ""), str(b.get("name") or ""), create=bool(b.get("create")),
            commit_changes=bool(b.get("commit_changes")), lang=str(b.get("lang") or "zh"))})

    def admin_project_link(self, user) -> None:
        """Register a folder for discussions only: no git, no .agents, nothing written into it (works while the
        old dispatch is off)."""
        b = self._body()
        self._json(200, {"project": self.app.projects.link(str(b.get("path") or ""), str(b.get("name") or ""))})

    def admin_project_rename(self, user) -> None:
        b = self._body()
        self.app.projects.rename(b.get("id"), str(b.get("name") or ""))
        self._json(200, {"ok": True})

    def admin_project_remove(self, user) -> None:
        self.app.projects.remove(self._body().get("id"))
        self._json(200, {"ok": True})

    def admin_folders(self, user) -> None:
        from urllib.parse import parse_qs

        if self._legacy_off():   # only the 添加项目 sheet uses the folder browser
            return
        q = parse_qs(urlparse(self.path).query, keep_blank_values=True)
        self._json(200, self.app.projects.browse(q.get("path", [""])[0]))

    def admin_remote(self, user) -> None:
        port = self.server.server_address[1]
        ts = tailscale_ips()
        self._json(200, {"listening": self.app.addresses, "tailscale": ts,
                         "urls": [f"http://{ip}:{port}/" for ip in ts if ip in self.app.addresses]})

    def admin_key(self, user) -> None:
        b = self._body()
        pid = str(b.get("provider", ""))
        if pid not in BY_ID:
            return self._json(400, {"error": "未知的服务商"})
        if BY_ID[pid].transport == "cli":
            return self._json(400, {"error": "Claude 订阅使用本机登录，不需要填写 API key"})
        self.app.keys.set(pid, str(b.get("key", "")))
        self._json(200, {"ok": True, "providers": self.app.registry.describe()})

    def admin_models(self, user) -> None:
        b = self._body()
        if b.get("default_model") is not None:
            self.app.db.set_setting("default_model", str(b["default_model"]))
        pid = str(b.get("provider", ""))
        if pid:
            if pid not in BY_ID:
                return self._json(400, {"error": "未知的服务商"})
            models = [m.strip() for m in b.get("models", []) if isinstance(m, str) and m.strip()]
            self.app.db.set_setting(f"models.{pid}", models[:30])
        self._json(200, {"ok": True, "providers": self.app.registry.describe()})

    def admin_test(self, user) -> None:
        """Check a provider's key: list its models (if the API offers that)."""
        pid = str(self._body().get("provider", ""))
        if pid not in BY_ID:
            return self._json(400, {"error": "未知的服务商"})
        if BY_ID[pid].transport == "cli":
            status = self.app.subscription.status(refresh=True)
            return self._json(200, {"ok": status["ready"], "models": self.app.registry.models(pid),
                                    "message": status["message"], "error": "" if status["ready"] else status["message"]})
        key, _ = self.app.registry.key(pid)
        if not key:
            return self._json(400, {"error": "还没有填 API key"})
        try:
            models = llm.list_models(self.app.registry.base_url(pid), key, timeout=20,
                                     headers=self.app.registry.headers(pid, key))
        except llm.LLMError as e:
            msg = "API key 不对" if e.kind == "auth" else f"连接失败：{str(e)[:200]}"
            return self._json(200, {"ok": False, "error": msg})
        self._json(200, {"ok": True, "models": models[:200]})

    def admin_users(self, user) -> None:
        b = self._body()
        action = b.get("action")
        if action == "add":
            name, pw = str(b.get("name", "")).strip(), str(b.get("password", ""))
            if not NAME_RE.match(name) or len(pw) < 8:
                return self._json(400, {"error": "用户名 1–32 个字（字母、数字、汉字），密码至少 8 位"})
            if any(u["name"] == name for u in self.app.db.users()):
                return self._json(400, {"error": "这个用户名已经有了"})
            self.app.db.add_user(name, pw, str(b.get("display") or name), admin=bool(b.get("admin")))
        elif action == "reset":
            if len(str(b.get("password", ""))) < 8:
                return self._json(400, {"error": "密码至少 8 位"})
            self.app.db.set_password(int(b["id"]), str(b["password"]))
        elif action == "can_code":
            self.app.db.set_can_code(int(b["id"]), bool(b.get("value")))
        elif action == "delete":
            if int(b["id"]) == user["id"]:
                return self._json(400, {"error": "不能删除自己"})
            self.app.db.delete_user(int(b["id"]))
        else:
            return self._json(400, {"error": "unknown action"})
        self._json(200, {"ok": True, "users": [dict(u) for u in self.app.db.users()]})


class Server(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets a second program bind a port that is in use: there a busy port stays busy
    allow_reuse_address = os.name != "nt"


def make_server(host: str = "127.0.0.1", port: int = 8848, app: App | None = None) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"app": app or App()})
    return Server((host, port), handler)


def hold_instance_lock(data: Path):
    """One Crossview per data folder: a second one would take over the jobs the first is running. Returns the open lock
    file (kept for the life of the process; the system lets go of it however the process ends), or None when
    another Crossview holds it."""
    data.mkdir(parents=True, exist_ok=True)
    f = open(data / "server.lock", "a+b")
    try:
        if os.name == "nt":
            import msvcrt

            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


def start_watcher(app: App, interval: float = 60.0):
    """Phone alerts: the web app's one always-on watcher thread (orch/watch.py, named mao-watch). While alerts are off
    it is idle (a look every minute reads notify.json, pushes and writes nothing); it catches every error itself, and
    pushes through app.notify_sender as it is now (None = the real ntfy request). Returns (thread, stop event), or None when the
    thread could not start: the web app then runs on without phone alerts' checks. Never called by App()."""
    try:
        return watch.start_thread(app.data, rows_fn=app.discussions.watch_rows, store=app.discussions.store,
                                  sender=app.notify_sender, interval=interval)
    except Exception as e:  # noqa: BLE001  (phone alerts must never stop the web app)
        print(f"手机提醒的后台检查没有启动（{type(e).__name__}: {e}），网页版照常可用。", file=sys.stderr)
        return None


def main(argv: list[str] | None = None) -> int:
    import argparse
    import threading
    import webbrowser

    p = argparse.ArgumentParser(prog="maoweb", description="Crossview 网页版")
    p.add_argument("--remote", action="store_true",
                   help="同时在这台电脑的 Tailscale 地址上监听，让你自己的手机、平板在外面也能打开")
    p.add_argument("--lan", action="store_true", help="也允许同一个家庭网络（局域网）里的设备打开")
    p.add_argument("--host", action="append", help="额外的监听地址（一般不用填）")
    p.add_argument("--port", type=int, default=8848)
    p.add_argument("--no-browser", action="store_true")
    args = p.parse_args(argv)
    hosts = ["127.0.0.1"]
    if args.remote:
        ts = tailscale_ips()
        if ts:
            hosts += ts
        else:
            print("没有找到 Tailscale 地址：请先安装 Tailscale 并登录（https://tailscale.com/download），"
                  "然后重新打开。现在只有本机能打开。")
    if args.lan:
        hosts = ["0.0.0.0"]  # every address of this PC (the Tailscale one included); the client filter still applies
    for h in args.host or []:
        if h not in hosts:
            hosts.append(h)
    instance = hold_instance_lock(data_dir())   # before anything touches the jobs of a Crossview that may be running
    if instance is None:
        print(f"已经有一个 Crossview 在运行（数据在 {data_dir()}）。不用再打开一次：直接在浏览器里打开 "
              f"http://127.0.0.1:{args.port}/ ；要重新启动，先关掉原来那个窗口。")
        return 1
    handler = type("BoundHandler", (Handler,), {})
    servers = []
    for h in hosts:   # the ports first: a failed start must not have touched anything
        try:
            servers.append(Server((h, args.port), handler))
        except OSError as e:
            print(f"无法在 {h}:{args.port} 监听（{e}）")
    if not servers:
        instance.close()
        return 1
    app = handler.app = App()
    app.allow_lan = args.lan
    watcher = start_watcher(app)
    app.addresses = [s.server_address[0] for s in servers]
    for srv in servers[1:]:
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.5}, daemon=True).start()
    local = f"http://127.0.0.1:{args.port}/"
    print(f"Crossview 网页版已启动：{local}")
    for h in app.addresses:
        if h not in ("127.0.0.1", "0.0.0.0"):
            print(f"外出时在你自己的手机 / 平板上打开（需要登录同一个 Tailscale 账号）：http://{h}:{args.port}/")
    print(f"数据保存在 {app.data}（不会上传到 GitHub）。关掉这个窗口就停止。")
    if not args.no_browser:
        webbrowser.open(local)
    try:
        servers[0].serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        if watcher:
            watcher[1].set()
        for srv in servers:
            srv.server_close()
        instance.close()
    return 0
