"""Office documents from the web page: ask a table, write, edit or fill in a Word document, follow money through
bank statements (资金追踪) and find information in a pile of files (找信息).

The work itself is orch's (orch/sheet.py, orch/doc.py, orch/redact.py); this module keeps the files,
runs each request in the background and remembers the results per family member.

- Files live under <data>/docs/u<user id>/: uploads in in/, results in out/<job id>/. Nobody sees another
  person's files.
- Models: the providers the admin gave a key on the settings page, in the order DeepSeek, Qwen, GPT,
  Claude, Gemini (Doubao too, when a chat model is listed for it). "Sensitive" keeps to DeepSeek, Qwen, Doubao.
- Personal details are always masked before anything is sent (orch/redact.py).
- 资金追踪 and 找信息 use no model at all: orch/trace.py and orch/find.py run here, on the uploaded copies.
"""
from __future__ import annotations

import base64
import json
import re
import secrets
import shutil
import threading
import time
from pathlib import Path

from orch import doc, find, i18n, office, redact, sheet, trace
from orch.providers import BY_ID as PRESETS

KINDS = ("ask", "draft", "polish", "fill", "trace", "find")
LOCAL = ("trace", "find")                 # no model: computed on this computer
TABLES = (".xlsx", ".xlsm", ".csv", ".tsv", ".xls", ".et")
TEXTS = (".docx", ".txt", ".md", ".doc", ".wps", ".rtf")
PDFS = (".pdf",)
MAX_INPUTS = 30
FIND_TYPES = ("身份证", "手机", "银行卡", "邮箱")
MAX_FILE = 10_000_000
PREVIEW_ROWS = 30
ORDER = ("deepseek", "qwen", "doubao", "openai", "anthropic", "gemini")

SCHEMA = """
CREATE TABLE IF NOT EXISTS doc_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, kind TEXT NOT NULL, request TEXT,
    input TEXT, status TEXT NOT NULL, result TEXT, error TEXT, created_at REAL, updated_at REAL
);
CREATE INDEX IF NOT EXISTS doc_jobs_user ON doc_jobs(user_id);
"""


class Docs:
    def __init__(self, app):
        self.app = app
        self.app.db.db.executescript(SCHEMA)
        self._threads: dict[int, threading.Thread] = {}
        self.app.db.x("UPDATE doc_jobs SET status='failed', error=? WHERE status='running'",
                      ("网页版重启时这个任务还没做完，请再提交一次",))

    # -- files -----------------------------------------------------------------------
    def folder(self, user_id: int) -> Path:
        return self.app.data / "docs" / f"u{user_id}"

    def upload(self, user_id: int, name: str, data: str) -> dict:
        name = Path(str(name or "")).name.strip()
        ext = Path(name).suffix.lower()
        if ext not in TABLES + TEXTS + PDFS:
            raise ValueError("只能上传 Excel（.xlsx、.xls）、CSV、Word（.docx、.doc）、WPS、PDF 或文本文件（.txt、.md）")
        raw = data.split(",", 1)[1] if data.startswith("data:") else data
        try:
            blob = base64.b64decode(raw, validate=True)
        except ValueError:
            raise ValueError("文件内容不对，请重新选择") from None
        if not blob or len(blob) > MAX_FILE:
            raise ValueError("文件太大了（最多 10 MB）" if blob else "文件是空的")
        token = secrets.token_hex(8) + ext
        path = self.folder(user_id) / "in" / token
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob)
        (path.parent / (token + ".name")).write_text(name, encoding="utf-8")
        return {"file": token, "name": name, "size": len(blob)}

    def _input(self, user_id: int, token: str, kinds: tuple[str, ...]) -> tuple[Path, str]:
        if not re.fullmatch(r"[0-9a-f]{16}\.[a-z]{2,4}", str(token or "")):
            raise ValueError("请先上传文件")
        path = self.folder(user_id) / "in" / token
        if not path.is_file():
            raise ValueError("找不到上传的文件，请重新上传")
        if path.suffix not in kinds:
            raise ValueError("这一项要用" + ("Excel 或 CSV 表格" if kinds == TABLES else
                                            "Word 或文本文件" if PDFS[0] not in kinds else "表格、Word、PDF 或文本文件"))
        label = path.parent / (token + ".name")
        return path, (label.read_text(encoding="utf-8") if label.is_file() else token)

    def output(self, user_id: int, job_id: int, name: str) -> Path | None:
        rows = self.app.db.q("SELECT result FROM doc_jobs WHERE id=? AND user_id=?", (job_id, user_id))
        if not rows or not rows[0]["result"]:
            return None
        files = json.loads(rows[0]["result"]).get("files", {})
        if name not in files.values():
            return None
        p = self.folder(user_id) / "out" / str(job_id) / name
        return p if p.is_file() else None

    # -- models --------------------------------------------------------------------------
    def endpoints(self, sensitive: bool) -> list[office.Endpoint]:
        reg = self.app.registry
        out = []
        for pid in ORDER:
            if sensitive and pid not in office.DOMESTIC:
                continue
            key, _ = reg.key(pid)
            models = reg.models(pid)
            model = models[0] if models else PRESETS[pid].api_model
            if key and model:
                out.append(office.Endpoint(pid, reg.base_url(pid), key, model, reg.headers(pid, key)))
        if not out:
            raise ValueError("敏感模式要用国内模型：请管理员在设置里填 DeepSeek、千问或豆包的 API key" if sensitive
                             else "还没有可用的模型：请管理员在设置里填一个 API key")
        return out

    def ready(self) -> dict:
        reg = self.app.registry
        have = [pid for pid in ORDER if reg.key(pid)[0] and (reg.models(pid) or PRESETS[pid].api_model)]
        return {"ready": bool(have), "domestic": any(p in office.DOMESTIC for p in have)}

    # -- jobs ------------------------------------------------------------------------------
    def submit(self, user_id: int, body: dict) -> int:
        kind = str(body.get("kind", ""))
        if kind not in KINDS:
            raise ValueError("不认识的操作")
        if kind in LOCAL:
            return self._submit_local(user_id, kind, body)
        text = str(body.get("text", "")).strip()[:4000]
        style = "gongwen" if body.get("style") == "gongwen" else "plain"
        if kind == "draft" and body.get("template"):
            from .templates import compose

            text, default_style = compose(str(body["template"]), body.get("fields") or {}, text)
            if body.get("style") not in ("plain", "gongwen"):
                style = default_style
        if kind in ("ask", "draft", "fill") and not text:
            raise ValueError({"ask": "请写下想问什么", "draft": "请写下要写什么", "fill": "请写下要填的内容"}[kind])
        files = {}
        if kind == "ask":
            files["main"] = self._input(user_id, body.get("file"), TABLES)
        elif kind in ("polish", "fill"):
            files["main"] = self._input(user_id, body.get("file"), (".docx", ".doc", ".wps") if kind == "fill" else TEXTS)
        mats = [self._input(user_id, t, TEXTS) for t in (body.get("materials") or [])[:5]]
        sensitive = bool(body.get("sensitive"))
        eps = self.endpoints(sensitive)                     # fail now, not in the background
        words = [w for w in re.split(r"[,，、\s]+", str(body.get("mask", ""))) if w]
        now = time.time()
        meta = {"style": style, "sensitive": sensitive, "file": files["main"][1] if "main" in files else "",
                "materials": [m[1] for m in mats]}
        jid = self.app.db.x("INSERT INTO doc_jobs(user_id, kind, request, input, status, created_at, updated_at) "
                            "VALUES(?,?,?,?,?,?,?)", (user_id, kind, text, json.dumps(meta, ensure_ascii=False),
                                                     "running", now, now))
        args = (jid, user_id, kind, text, files.get("main"), mats, style, eps, redact.make(words))
        t = threading.Thread(target=self._work, args=args, daemon=True)
        self._threads[jid] = t
        t.start()
        return jid

    def _work(self, jid, user_id, kind, text, main, mats, style, eps, masker) -> None:
        out = self.folder(user_id) / "out" / str(jid)
        try:
            result = getattr(self, f"_{kind}")(out, text, main, [m[0] for m in mats], style, eps, masker)
            result["masked"] = masker.summary()
            self._update(jid, status="done", result=json.dumps(result, ensure_ascii=False))
        except (office.OfficeError, ValueError, OSError) as e:
            self._update(jid, status="failed", error=str(e)[:500])
        except Exception as e:  # never leave a job "running" forever
            self._update(jid, status="failed", error=f"出错了：{type(e).__name__}: {str(e)[:300]}")

    def _ask(self, out, text, main, mats, style, eps, masker) -> dict:
        t = sheet.load(main[0])
        ans = sheet.ask(t, text, eps, masker=masker)
        files = sheet.save(main[0], ans.result, ans.plan, out)
        res = ans.result
        return {"title": res.name, "headers": res.headers, "total": len(res.rows),
                "rows": [[sheet.show(v) for v in r] for r in res.rows[:PREVIEW_ROWS]],
                "note": str(ans.plan.get("note") or ""), "files": {k: p.name for k, p in files.items()},
                "model": f"{ans.endpoint} {ans.model}", "tokens": [ans.tokens_in, ans.tokens_out]}

    def _write(self, out, blocks, res, style, stem=None) -> dict:
        dest = doc.out_path(blocks, out, stem)
        doc.write_docx(blocks, dest, style)
        text = doc.to_markdown(blocks)
        return {"text": text, "count": doc.count(text), "files": {"docx": dest.name},
                "model": f"{res.endpoint} {res.model}", "tokens": [res.tokens_in, res.tokens_out]}

    def _draft(self, out, text, main, mats, style, eps, masker) -> dict:
        blocks, res = doc.draft(text, eps, mats, style, masker=masker)
        return self._write(out, blocks, res, style)

    def _polish(self, out, text, main, mats, style, eps, masker) -> dict:
        blocks, res = doc.polish(doc.read_any(main[0]), text, eps, style, masker=masker)
        return self._write(out, blocks, res, style, Path(main[1]).stem + "_改")

    def _fill(self, out, text, main, mats, style, eps, masker) -> dict:
        values, res = doc.fill(main[0], text, eps, mats, masker=masker)
        dest = doc.out_path([], out, Path(main[1]).stem + "_填好")
        empty = doc.fill_docx(main[0], values, dest)
        return {"fields": values, "empty": empty, "files": {"docx": dest.name},
                "model": f"{res.endpoint} {res.model}", "tokens": [res.tokens_in, res.tokens_out]}

    # -- 资金追踪 / 找信息: no model --------------------------------------------------------------
    def _submit_local(self, user_id: int, kind: str, body: dict) -> int:
        tokens = [t for t in (body.get("files") or []) if t][:MAX_INPUTS]
        if not tokens:
            raise ValueError("请先上传银行流水" if kind == "trace" else "请先上传要查的文件")
        inputs = [self._input(user_id, t, TABLES if kind == "trace" else TABLES + TEXTS + PDFS) for t in tokens]
        text = str(body.get("text", "")).strip()[:2000]
        opts: dict = {"lang": "en" if body.get("lang") == "en" else "zh"}
        if kind == "trace":
            try:
                opts["days"] = max(0, min(int(body.get("days", trace.DEFAULT_DAYS)), 60))
            except (TypeError, ValueError):
                opts["days"] = trace.DEFAULT_DAYS
        else:
            opts["types"] = [t for t in (body.get("types") or []) if t in FIND_TYPES]
            opts["all"] = bool(body.get("all"))
            if not text and not opts["types"]:
                raise ValueError("请写下要找什么，或者选一种号码")
        now = time.time()
        meta = {"files": [name for _, name in inputs], **{k: v for k, v in opts.items() if k != "lang"}}
        jid = self.app.db.x("INSERT INTO doc_jobs(user_id, kind, request, input, status, created_at, updated_at) "
                            "VALUES(?,?,?,?,?,?,?)", (user_id, kind, text, json.dumps(meta, ensure_ascii=False),
                                                     "running", now, now))
        t = threading.Thread(target=self._work_local, args=(jid, user_id, kind, text, inputs, opts), daemon=True)
        self._threads[jid] = t
        t.start()
        return jid

    def _work_local(self, jid, user_id, kind, text, inputs, opts) -> None:
        out = self.folder(user_id) / "out" / str(jid)
        try:
            src = out / "in"                     # the uploads under their own names, so results name them
            src.mkdir(parents=True, exist_ok=True)
            for path, name in inputs:
                dest = src / (Path(name).name or path.name)
                n = 2
                while dest.exists():
                    dest = src / f"{Path(name).stem}_{n}{path.suffix}"
                    n += 1
                shutil.copyfile(path, dest)
            with i18n.using(opts["lang"]):
                result = self._trace(out, src, text, opts) if kind == "trace" else self._find(out, src, text, opts)
            self._update(jid, status="done", result=json.dumps(result, ensure_ascii=False))
        except (office.OfficeError, ValueError, OSError) as e:
            self._update(jid, status="failed", error=str(e)[:500])
        except Exception as e:  # never leave a job "running" forever
            self._update(jid, status="failed", error=f"出错了：{type(e).__name__}: {str(e)[:300]}")

    @staticmethod
    def _preview(t: sheet.Table, rows: int = PREVIEW_ROWS) -> dict:
        return {"title": t.name, "headers": t.headers, "total": len(t.rows),
                "rows": [[sheet.show(v) for v in r] for r in t.rows[:rows]]}

    def _trace(self, out: Path, src: Path, text: str, opts: dict) -> dict:
        rep = trace.run([src], opts["days"], [text] if text else None)
        files = trace.save(rep, out)
        return {"summary": trace.summary_lines(rep), "notes": rep.notes, "counts": rep.counts,
                "tables": [self._preview(t) for t in (rep.transfers, rep.unknown, rep.counterparties) if t.rows],
                "files": {k: p.name for k, p in files.items()}}

    def _find(self, out: Path, src: Path, text: str, opts: dict) -> dict:
        words = [w for w in re.split(r"[,，、;；\s]+", text) if w]
        res = find.search([src], find.needles(words, opts["types"]), opts["all"])
        result = {"summary": find.summary_lines(res, 0)[:1], "table": self._preview(find.table(res, src), 50),
                  "files": {}}
        result["table"]["headers"] = result["table"]["headers"][:4]          # the full path is the server's copy
        result["table"]["rows"] = [r[:4] for r in result["table"]["rows"]]
        if res.hits or res.skipped:
            result["files"]["xlsx"] = find.save(res, out, src).name
        return result

    def _update(self, jid: int, **kw) -> None:
        kw["updated_at"] = time.time()
        self.app.db.x(f"UPDATE doc_jobs SET {', '.join(k + '=?' for k in kw)} WHERE id=?", (*kw.values(), jid))

    def wait(self, jid: int, timeout: float = 30) -> None:  # tests
        t = self._threads.get(jid)
        if t:
            t.join(timeout)

    def jobs(self, user_id: int) -> list[dict]:
        rows = self.app.db.q("SELECT * FROM doc_jobs WHERE user_id=? ORDER BY id DESC LIMIT 50", (user_id,))
        return [{"id": r["id"], "kind": r["kind"], "request": r["request"], "status": r["status"],
                 "input": json.loads(r["input"] or "{}"), "result": json.loads(r["result"]) if r["result"] else None,
                 "error": r["error"], "created_at": r["created_at"]} for r in rows]

    def delete(self, user_id: int, jid: int) -> bool:
        rows = self.app.db.q("SELECT status FROM doc_jobs WHERE id=? AND user_id=?", (jid, user_id))
        if not rows or rows[0]["status"] == "running":
            return False
        folder = self.folder(user_id) / "out" / str(jid)
        if folder.is_dir():
            shutil.rmtree(folder, ignore_errors=True)
        self.app.db.x("DELETE FROM doc_jobs WHERE id=?", (jid,))
        return True
