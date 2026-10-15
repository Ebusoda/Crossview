"""Images and video through Volcano Engine Ark (豆包): Seedream for pictures, Seedance for video.

Pictures: POST {base}/images/generations, answered in one call (base64, saved under data/media/).
Photo editing is the same call with the source picture attached (`image`).
Video: POST {base}/contents/generations/tasks starts a job; a background thread polls
GET {base}/contents/generations/tasks/{id} until it succeeds, then downloads the video.
Jobs still running when the server stops are picked up again on the next start.

Model ids are editable on the settings page (the Ark console lists the current ones).
"""
from __future__ import annotations

import base64
import json
import re
import threading
import time
import urllib.request
from pathlib import Path

from orch.llm import LLMError, _request

from .db import add_media, get_media, update_media

IMAGE_MODELS = ["doubao-seedream-4-0-250828"]
VIDEO_MODELS = ["doubao-seedance-1-0-pro-250528"]
IMAGE_SIZES = ["1024x1024", "864x1152", "1152x864", "720x1280", "1280x720", "2K"]
VIDEO_RATIOS = ["16:9", "9:16", "1:1", "4:3", "3:4"]
MAX_UPLOAD = 10 * 1024 * 1024
POLL_SECONDS = 5.0
_MIME_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "video/mp4": ".mp4"}


def _sniff(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


class Media:
    def __init__(self, app):
        self.app = app
        self.dir = app.data / "media"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._threads: dict[int, threading.Thread] = {}
        self.poll_seconds = POLL_SECONDS

    # -- helpers ----------------------------------------------------------------
    def _ark(self) -> tuple[str, str]:
        key, _ = self.app.registry.key("doubao")
        if not key:
            raise ValueError("画图和视频要用豆包（火山方舟）的 API key，请管理员在设置里填写")
        return self.app.registry.base_url("doubao"), key

    def models(self, kind: str) -> list[str]:
        custom = self.app.db.setting(f"models.{kind}")
        return list(custom) if isinstance(custom, list) else list(IMAGE_MODELS if kind == "image" else VIDEO_MODELS)

    def _save(self, user_id: int, data: bytes, mime: str) -> str:
        folder = self.dir / str(user_id)
        folder.mkdir(parents=True, exist_ok=True)
        name = f"{int(time.time() * 1000)}{_MIME_EXT.get(mime, '.bin')}"
        (folder / name).write_bytes(data)
        return f"media/{user_id}/{name}"

    def path(self, row) -> Path:
        return self.app.data / row["file"]

    def _data_uri(self, row) -> str:
        return f"data:{row['mime']};base64,{base64.b64encode(self.path(row).read_bytes()).decode()}"

    # -- uploads ----------------------------------------------------------------
    def upload(self, user_id: int, data_b64: str) -> int:
        if "," in data_b64 and data_b64.startswith("data:"):
            data_b64 = data_b64.split(",", 1)[1]
        data = base64.b64decode(data_b64, validate=True)
        if len(data) > MAX_UPLOAD:
            raise ValueError("图片太大了（最多 10 MB）")
        mime = _sniff(data)
        if not mime:
            raise ValueError("只支持 PNG、JPG、WebP 图片")
        return add_media(self.app.db, user_id, "upload", "done", file=self._save(user_id, data, mime), mime=mime)

    # -- pictures -----------------------------------------------------------------
    def image(self, user_id: int, prompt: str, model: str, size: str, source_id: int | None = None) -> int:
        if model not in self.models("image"):
            raise ValueError(f"画图模型 {model} 没有在设置里启用")
        if size not in IMAGE_SIZES:
            raise ValueError("不支持的尺寸")
        base, key = self._ark()
        body = {"model": model, "prompt": prompt, "size": size, "response_format": "b64_json", "watermark": False}
        src = None
        if source_id:
            src = get_media(self.app.db, source_id, user_id)
            if not src or src["kind"] not in ("upload", "image") or not src["file"]:
                raise ValueError("找不到要修改的图片")
            body["image"] = self._data_uri(src)
        mid = add_media(self.app.db, user_id, "image", "running", prompt=prompt, model=model, source_id=source_id)
        try:
            out = _request(base.rstrip("/") + "/images/generations", key, body, timeout=180)
            item = (out.get("data") or [{}])[0]
            if item.get("b64_json"):
                data = base64.b64decode(item["b64_json"])
            elif item.get("url"):
                with urllib.request.urlopen(item["url"], timeout=120) as r:
                    data = r.read()
            else:
                raise LLMError(f"没有返回图片：{json.dumps(out)[:200]}")
            mime = _sniff(data) or "image/jpeg"
            update_media(self.app.db, mid, status="done", file=self._save(user_id, data, mime), mime=mime)
        except LLMError as e:
            update_media(self.app.db, mid, status="failed", error=_plain(e))
        return mid

    # -- video ------------------------------------------------------------------
    def video(self, user_id: int, prompt: str, model: str, ratio: str, duration: int,
              source_id: int | None = None) -> int:
        if model not in self.models("video"):
            raise ValueError(f"视频模型 {model} 没有在设置里启用")
        if ratio not in VIDEO_RATIOS or duration not in (5, 10):
            raise ValueError("不支持的画面比例或时长")
        base, key = self._ark()
        text = f"{prompt} --ratio {ratio} --duration {duration}"
        content: list[dict] = [{"type": "text", "text": text}]
        if source_id:
            src = get_media(self.app.db, source_id, user_id)
            if not src or src["kind"] not in ("upload", "image") or not src["file"]:
                raise ValueError("找不到作为首帧的图片")
            content.append({"type": "image_url", "image_url": {"url": self._data_uri(src)}})
        mid = add_media(self.app.db, user_id, "video", "running", prompt=prompt, model=model, source_id=source_id)
        try:
            out = _request(base.rstrip("/") + "/contents/generations/tasks", key,
                           {"model": model, "content": content}, timeout=60)
        except LLMError as e:
            update_media(self.app.db, mid, status="failed", error=_plain(e))
            return mid
        update_media(self.app.db, mid, task_id=str(out.get("id") or ""))
        self._watch(mid, user_id)
        return mid

    def resume(self) -> None:
        """Poll again for videos that were still being made when the server stopped."""
        for r in self.app.db.q("SELECT id, user_id FROM media WHERE kind='video' AND status='running' AND task_id != ''"):
            self._watch(r["id"], r["user_id"])

    def _watch(self, mid: int, user_id: int) -> None:
        t = threading.Thread(target=self._poll, args=(mid, user_id), daemon=True)
        self._threads[mid] = t
        t.start()

    def _poll(self, mid: int, user_id: int, max_minutes: float = 30) -> None:
        deadline = time.time() + max_minutes * 60
        while time.time() < deadline:
            row = get_media(self.app.db, mid)
            if not row or row["status"] != "running":
                return
            try:
                base, key = self._ark()
                out = _request(f"{base.rstrip('/')}/contents/generations/tasks/{row['task_id']}", key, None, timeout=30)
            except (LLMError, ValueError) as e:
                if isinstance(e, LLMError) and e.kind == "auth":
                    update_media(self.app.db, mid, status="failed", error=_plain(e))
                    return
                time.sleep(self.poll_seconds)
                continue
            status = out.get("status")
            if status == "succeeded":
                url = (out.get("content") or {}).get("video_url")
                try:
                    with urllib.request.urlopen(url, timeout=300) as r:
                        data = r.read()
                    update_media(self.app.db, mid, status="done", file=self._save(user_id, data, "video/mp4"),
                                 mime="video/mp4")
                except (OSError, ValueError) as e:
                    update_media(self.app.db, mid, status="failed", error=f"视频做好了，但下载失败：{e}")
                return
            if status in ("failed", "cancelled", "expired"):
                err = out.get("error") or {}
                update_media(self.app.db, mid, status="failed",
                             error=str(err.get("message") if isinstance(err, dict) else err) or "生成失败")
                return
            time.sleep(self.poll_seconds)
        update_media(self.app.db, mid, status="failed", error="等了 30 分钟还没做好，请再试一次")

    def wait(self, mid: int, timeout: float = 10) -> None:  # tests
        t = self._threads.get(mid)
        if t:
            t.join(timeout)


def _plain(e: LLMError) -> str:
    """A message family members can act on, instead of the raw API answer."""
    known = {"auth": "豆包的 API key 不对或没有开通这个模型",
             "rate_limit": "调用太频繁或余额不足，请稍后再试",
             "timeout": "等太久没有回应，请再试一次"}
    if e.kind in known:
        return known[e.kind]
    m = re.search(r'"message"\s*:\s*"([^"]+)"', str(e))
    detail = m.group(1) if m else str(e)
    if re.search(r"sensitive|risk|敏感|违规", detail, re.I):
        return "描述或图片里可能有不允许生成的内容，换个说法再试试"
    return f"生成失败：{detail[:200]}"


def public(row) -> dict:
    """What the browser may see about a media row (the file itself is served by /media/<id>)."""
    return {"id": row["id"], "kind": row["kind"], "prompt": row["prompt"], "model": row["model"],
            "status": row["status"], "error": row["error"], "source_id": row["source_id"],
            "url": f"/media/{row['id']}" if row["file"] else None, "created_at": row["created_at"]}
