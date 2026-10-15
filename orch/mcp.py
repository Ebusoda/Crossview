"""MCP server: use orch from a chat app (Claude Desktop, ChatGPT and other MCP clients).

    python -m orch mcp --project F:\\work\\repo-a --project F:\\work\\repo-b

Speaks the Model Context Protocol over stdio (JSON-RPC 2.0, one message per line). The chat model
can look at projects and tasks, add tasks and start runs; orch itself still decides routing,
acceptance and merging, so the chat model never becomes the control centre. Runs start as separate
`python -m orch run` processes and keep going when the chat ends. High-risk tasks still wait for a
human merge, exactly as on the command line.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

PROTOCOL = "2025-06-18"
SUPPORTED = ("2025-06-18", "2025-03-26", "2024-11-05")
ROOT = Path(__file__).resolve().parents[1]


class OrchTools:
    def __init__(self, projects: list[str], python: str | None = None):
        self.projects = {Path(p).resolve().name: Path(p).resolve() for p in projects}
        self.python = python or sys.executable
        self.running: dict[str, subprocess.Popen] = {}

    # -- helpers -------------------------------------------------------------------
    def _path(self, project: str) -> Path:
        if project not in self.projects:
            raise ValueError(f"unknown project {project!r}; known: {', '.join(self.projects) or '(none)'}")
        return self.projects[project]

    def _orch(self, project: str, *args: str, timeout: float = 120) -> str:
        p = subprocess.run([self.python, "-m", "orch", "-p", str(self._path(project)), *args],
                           cwd=str(ROOT), capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL,
                           env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        out = (p.stdout + p.stderr).decode("utf-8", errors="replace").strip()
        if p.returncode != 0:
            raise ValueError(out[-2000:] or f"orch exited with {p.returncode}")
        return out[-8000:]

    def _db(self, project: str) -> sqlite3.Connection:
        db = self._path(project) / ".agents" / "state.db"
        if not db.is_file():
            raise ValueError(f"{project} is not an orch project yet (run: python -m orch -p <path> init)")
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        con.row_factory = sqlite3.Row
        return con

    # -- tools ---------------------------------------------------------------------
    def list_projects(self) -> dict:
        out = []
        for name in self.projects:
            try:
                con = self._db(name)
                counts = {r["status"]: r["n"] for r in con.execute("SELECT status, COUNT(*) AS n FROM tasks GROUP BY status")}
                con.close()
            except (ValueError, sqlite3.Error) as e:
                counts = {"error": str(e)}
            proc = self.running.get(name)
            out.append({"project": name, "path": str(self.projects[name]), "tasks": counts,
                        "run_in_progress": bool(proc and proc.poll() is None)})
        return {"projects": out}

    def list_tasks(self, project: str, status: str = "") -> dict:
        con = self._db(project)
        sql = "SELECT id, title, status, difficulty, risk, note FROM tasks" + (" WHERE status=?" if status else "")
        rows = [dict(r) for r in con.execute(sql + " ORDER BY id DESC LIMIT 50", (status,) if status else ())]
        con.close()
        return {"tasks": rows}

    def task_detail(self, project: str, task_id: str) -> dict:
        con = self._db(project)
        t = con.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not t:
            con.close()
            raise ValueError(f"no task {task_id}")
        runs = [dict(r) for r in con.execute(
            "SELECT id, pool, kind, status, verified, tokens_in, tokens_cached, model_calls, est_cost_usd "
            "FROM runs WHERE task_id=? ORDER BY id", (task_id,))]
        con.close()
        task = {k: t[k] for k in ("id", "title", "spec", "type", "difficulty", "risk", "status", "note", "branch")}
        task["verify"] = json.loads(t["verify"] or "[]")
        task["scope"] = json.loads(t["scope"] or "[]")
        return {"task": task, "runs": runs}

    def add_task(self, project: str, title: str, spec: str = "", difficulty: str = "M", risk: str = "normal",
                 verify: list[str] | None = None, scope: list[str] | None = None, task_type: str = "code_change") -> dict:
        args = ["task", "add", "--title", title, "--spec", spec, "--difficulty", difficulty, "--risk", risk,
                "--type", task_type]
        for v in verify or []:
            args += ["--verify", v]
        for s in scope or []:
            args += ["--scope", s]
        return {"output": self._orch(project, *args)}

    def run(self, project: str, task_ids: list[str] | None = None, auto_merge: bool = True) -> dict:
        proc = self.running.get(project)
        if proc and proc.poll() is None:
            return {"started": False, "reason": "a run is already in progress for this project", "pid": proc.pid}
        args = [self.python, "-m", "orch", "-p", str(self._path(project)), "run", *(task_ids or [])]
        if auto_merge:
            args.append("--auto-merge")
        log = self._path(project) / ".agents" / "mcp_run.log"
        with open(log, "ab") as f:
            proc = subprocess.Popen(args, cwd=str(ROOT), stdout=f, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                                    **_detached())
        self.running[project] = proc
        return {"started": True, "pid": proc.pid, "log": str(log),
                "note": "runs in the background; check progress with orch_list_tasks / orch_task"}

    def report(self, project: str) -> dict:
        return {"report": self._orch(project, "report")}

    def merge(self, project: str, task_id: str) -> dict:
        return {"output": self._orch(project, "merge", task_id, timeout=900)}

    def context(self, project: str, task_id: str) -> dict:
        return {"output": self._orch(project, "context", task_id)}

    # -- agent control panel ---------------------------------------------------------
    def agents(self, project: str = "") -> dict:
        """Every agent of one project, or of all projects plus totals per executor (one subscription
        is shared by every project that uses it)."""
        names = [project] if project else list(self.projects)
        out, totals = [], {}
        for name in names:
            try:
                p = json.loads(self._orch(name, "agents", "--json"))
            except (ValueError, json.JSONDecodeError) as e:
                out.append({"project": name, "error": str(e)[:500]})
                continue
            p["project"] = name
            out.append(p)
            for a in p["agents"]:
                t = totals.setdefault(a["executor"], {"runs_today": 0, "cost_usd_today": 0.0, "tokens_today": 0,
                                                      "working": 0})
                t["runs_today"] += a["today"]["runs"]
                t["cost_usd_today"] = round(t["cost_usd_today"] + a["today"]["cost_usd"], 4)
                t["tokens_today"] += a["today"]["tokens_in"] + a["today"]["tokens_out"]
                t["working"] += len(a["current"])
        return {"projects": out, "by_executor": totals}

    def pause(self, project: str, pool: str, minutes: float | None = None) -> dict:
        args = ["agents", "pause", pool] + (["--minutes", str(minutes)] if minutes else [])
        return {"output": self._orch(project, *args)}

    def resume(self, project: str, pool: str) -> dict:
        return {"output": self._orch(project, "agents", "resume", pool)}

    def swap(self, project: str, task_id: str, pool: str) -> dict:
        return {"output": self._orch(project, "agents", "swap", task_id, pool)}

    def sessions(self, days: float = 7, include_orch: bool = False) -> dict:
        """Claude Code / Codex sessions on this computer, the hand-opened ones included (orch/sessions.py:
        times, folders and token counts only, never conversation content)."""
        from . import sessions

        data = sessions.scan(days=days)
        if not include_orch:
            data["sessions"] = [x for x in data["sessions"] if x["source"] == "manual"]
        for x in data["sessions"]:
            x.pop("log", None)
        data["sessions"] = data["sessions"][:50]
        return data

    def retry(self, project: str, task_id: str) -> dict:
        return {"output": self._orch(project, "task", "retry", task_id)}

    def cancel(self, project: str, task_id: str) -> dict:
        return {"output": self._orch(project, "task", "cancel", task_id)}

    # -- office work (orch sheet / orch doc): files on this computer, any folder ---------------------
    @staticmethod
    def _office(sensitive: bool, mask: str):
        from . import office, redact

        return office.endpoints(None, None, None, True if sensitive else None), redact.make([mask] if mask else [])

    def sheet_ask(self, file: str, question: str, sheet: str = "", sensitive: bool = False, mask: str = "") -> dict:
        from . import sheet as sh

        if not question.strip():
            raise ValueError("question is empty")
        t = sh.load(Path(file), sheet or None)
        eps, masker = self._office(sensitive, mask)
        ans = sh.ask(t, question, eps, masker=masker)
        files = sh.save(Path(file), ans.result, ans.plan)
        return {"answer": "\n".join(sh.render_text(ans.result)), "note": str(ans.plan.get("note") or ""),
                "rows": len(ans.result.rows), "files": {k: str(v) for k, v in files.items()},
                "masked": masker.summary(), "model": f"{ans.endpoint} {ans.model}",
                "tokens": {"in": ans.tokens_in, "out": ans.tokens_out}}

    def doc(self, action: str, request: str = "", file: str = "", materials: list[str] | None = None,
            style: str = "plain", sensitive: bool = False, mask: str = "", out: str = "") -> dict:
        from . import doc as d

        if action not in ("draft", "polish", "fill"):
            raise ValueError("action is draft, polish or fill")
        if action in ("polish", "fill") and not file:
            raise ValueError(f"{action} needs file")
        if action in ("draft", "fill") and not request.strip():
            raise ValueError(f"{action} needs request")
        style = "gongwen" if style == "gongwen" else "plain"
        mats = [Path(m) for m in materials or []]
        eps, masker = self._office(sensitive, mask)
        if action == "fill":
            values, res = d.fill(Path(file), request, eps, mats, masker=masker)
            dest = Path(out) if out else d.out_path([], Path(file).parent, Path(file).stem + "_filled")
            empty = d.fill_docx(Path(file), values, dest)
            body = {"fields": values, "empty_fields": empty}
        else:
            if action == "draft":
                blocks, res = d.draft(request, eps, mats, style, masker=masker)
                folder = mats[0].parent if mats else Path.home() / "Documents"
                dest = Path(out) if out else d.out_path(blocks, folder)
            else:
                blocks, res = d.polish(d.read_any(Path(file)), request, eps, style, masker=masker)
                dest = Path(out) if out else d.out_path(blocks, Path(file).parent, Path(file).stem + "_edited")
            d.write_docx(blocks, dest, style)
            text = d.to_markdown(blocks)
            body = {"text": text, "length": d.count(text)}
        return {**body, "saved": str(dest), "masked": masker.summary(), "model": f"{res.endpoint} {res.model}",
                "tokens": {"in": res.tokens_in, "out": res.tokens_out}}


    def trace(self, files: list[str], days: int = 3, known: str = "", columns: str = "", sender: str = "",
              receiver: str = "", details: bool = False, out: str = "") -> dict:
        from . import trace as tc

        if not files:
            raise ValueError("files is empty")
        rep = tc.run([Path(f) for f in files], int(days), [known] if known else None, tc.parse_overrides([columns]),
                     sender, receiver)
        first = Path(files[0])
        saved = tc.save(rep, Path(out) if out else (first if first.is_dir() else first.parent))
        body: dict[str, Any] = {"summary": tc.summary_lines(rep), "notes": rep.notes, "counts": rep.counts,
                                "files": {k: str(v) for k, v in saved.items()},
                                "columns": [dict(zip(rep.columns.headers, r)) for r in rep.columns.rows]}
        if details:
            for key, t in (("transfers", rep.transfers), ("unknown", rep.unknown),
                           ("counterparties", rep.counterparties), ("flows", rep.flows)):
                body[key] = [dict(zip(t.headers, r)) for r in t.rows[:200]]
        return body

    def find(self, folder: str, words: list[str] | None = None, types: list[str] | None = None,
             all_words: bool = False, details: bool = False, out: str = "") -> dict:
        from . import find as fd

        res = fd.search([Path(folder)], fd.needles(words or [], types or []), all_words)
        root = Path(folder) if Path(folder).is_dir() else Path(folder).parent
        saved = fd.save(res, Path(out) if out else root, root) if (res.hits or res.skipped) else None
        per_file: dict[str, list[str]] = {}
        for h in res.hits:
            per_file.setdefault(str(h.file), []).append(h.where)
        body: dict[str, Any] = {"searched": res.searched, "files_with_hits": res.files_with_hits,
                                "hits": len(res.hits), "where": per_file,
                                "not_read": {str(f): why for f, why in res.skipped},
                                "saved": str(saved) if saved else ""}
        if details:
            body["text"] = [{"file": str(h.file), "where": h.where, "found": h.found, "text": h.text}
                            for h in res.hits[:200]]
        return body


def _detached() -> dict:
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
    return {"start_new_session": True}


_P = {"project": {"type": "string", "description": "project name, as listed by orch_list_projects"}}
TOOLS: list[dict[str, Any]] = [
    {"name": "orch_list_projects", "description": "Projects this orch server manages, with task counts per status.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "orch_list_tasks", "description": "Tasks of a project, newest first (optionally only one status: "
     "queued, running, verified, merged, blocked, failed, cancelled).",
     "inputSchema": {"type": "object", "properties": {**_P, "status": {"type": "string"}}, "required": ["project"]}},
    {"name": "orch_task", "description": "One task: its specification, status, note and every run (pool, tokens, cost).",
     "inputSchema": {"type": "object", "properties": {**_P, "task_id": {"type": "string"}},
                     "required": ["project", "task_id"]}},
    {"name": "orch_add_task", "description": "Queue a coding task. Give acceptance commands (verify) whenever "
     "possible: a task is only done when they exit with code 0.",
     "inputSchema": {"type": "object", "properties": {
         **_P, "title": {"type": "string"}, "spec": {"type": "string"},
         "difficulty": {"type": "string", "enum": ["S", "M", "L"]},
         "risk": {"type": "string", "enum": ["low", "normal", "high", "critical"]},
         "verify": {"type": "array", "items": {"type": "string"}},
         "scope": {"type": "array", "items": {"type": "string"}, "description": "files the task may change"}},
         "required": ["project", "title"]}},
    {"name": "orch_run", "description": "Start running queued tasks in the background (orch picks models, verifies "
     "and merges; high-risk tasks still wait for a human merge).",
     "inputSchema": {"type": "object", "properties": {
         **_P, "task_ids": {"type": "array", "items": {"type": "string"}}, "auto_merge": {"type": "boolean"}},
         "required": ["project"]}},
    {"name": "orch_report", "description": "Cost and quality report: first-pass rate and cost per successful task "
     "by task type and pool.", "inputSchema": {"type": "object", "properties": {**_P}, "required": ["project"]}},
    {"name": "orch_context", "description": "What each model call of a task was given (context manifest).",
     "inputSchema": {"type": "object", "properties": {**_P, "task_id": {"type": "string"}},
                     "required": ["project", "task_id"]}},
    {"name": "orch_merge", "description": "Merge a verified task into the integration branch (the human approval "
     "step for high-risk tasks; only call it when the user asked for it).",
     "inputSchema": {"type": "object", "properties": {**_P, "task_id": {"type": "string"}},
                     "required": ["project", "task_id"]}},
    {"name": "orch_agents", "description": "Control panel: every agent (pool) with its current task, use today and "
     "over 7 days, subscription windows, recent success rate, last error and whether it is paused. Leave project "
     "empty for all projects.",
     "inputSchema": {"type": "object", "properties": {"project": {"type": "string", "description": "optional"}}}},
    {"name": "orch_pause", "description": "Pause an agent: it gets no new tasks (running work is not interrupted). "
     "Without minutes it stays paused until orch_resume.",
     "inputSchema": {"type": "object", "properties": {**_P, "pool": {"type": "string"},
                                                       "minutes": {"type": "number"}},
                     "required": ["project", "pool"]}},
    {"name": "orch_resume", "description": "Resume a paused (or cooling) agent.",
     "inputSchema": {"type": "object", "properties": {**_P, "pool": {"type": "string"}},
                     "required": ["project", "pool"]}},
    {"name": "orch_swap", "description": "Give a task to another agent: that pool goes first in the task's route "
     "and the task is queued again. Not for running, verified or merged tasks.",
     "inputSchema": {"type": "object", "properties": {**_P, "task_id": {"type": "string"}, "pool": {"type": "string"}},
                     "required": ["project", "task_id", "pool"]}},
    {"name": "orch_sessions", "description": "Claude Code and Codex sessions on this computer, including the ones "
     "the user opened by hand: project folder, model, when last used, whether in use now, tokens (today and in "
     "total), plus Codex's subscription windows. Conversation content is never read.",
     "inputSchema": {"type": "object", "properties": {
         "days": {"type": "number", "description": "how far back (default 7)"},
         "include_orch": {"type": "boolean", "description": "also list sessions orch started itself"}}}},
    {"name": "office_sheet_ask", "description": "Answer a question about an Excel (.xlsx/.xls), WPS or CSV table on this "
     "computer. The model only sees column names and five masked sample rows; the numbers are computed locally over "
     "the whole table. Saves the result as .xlsx (and a chart as .svg) next to the file.",
     "inputSchema": {"type": "object", "properties": {
         "file": {"type": "string", "description": "full path of the table"},
         "question": {"type": "string"}, "sheet": {"type": "string", "description": "sheet name or number (optional)"},
         "sensitive": {"type": "boolean", "description": "use only models based in China (DeepSeek, Qwen, Doubao)"},
         "mask": {"type": "string", "description": "more words to hide, comma-separated (names, organisations)"}},
         "required": ["file", "question"]}},
    {"name": "office_doc", "description": "Write (draft), edit (polish) or fill in a Word template (fill: {{field}} "
     "placeholders) and save a .docx on this computer. Personal details (ID, phone, card numbers, e-mail, labelled "
     "names) are masked before sending and restored in the result. style gongwen = Chinese official document layout.",
     "inputSchema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["draft", "polish", "fill"]},
         "request": {"type": "string", "description": "what to write, how to edit (polish: empty = tidy up), or what to fill in"},
         "file": {"type": "string", "description": "polish: the document; fill: the template (.docx/.doc/.wps)"},
         "materials": {"type": "array", "items": {"type": "string"}, "description": "reference files (draft, fill)"},
         "style": {"type": "string", "enum": ["plain", "gongwen"]},
         "sensitive": {"type": "boolean"}, "mask": {"type": "string"},
         "out": {"type": "string", "description": "where to save (optional)"}},
         "required": ["action"]}},
    {"name": "office_trace", "description": "Follow money through bank statements on this computer (Excel, WPS, CSV; "
     "files or folders): checks whether each transfer between the statement holders arrived on the other side, lists "
     "cash withdrawals and money sent to unknown accounts, and sums up every outside account with the bank written in "
     "the statements (plus the bank its card number suggests). Computed locally without any model; saves an .xlsx and a "
     "flow chart .svg. By default only counts and file paths come back; details=true also returns the rows (they then "
     "reach this chat). It cannot say who owns an account beyond what the statements say.",
     "inputSchema": {"type": "object", "properties": {
         "files": {"type": "array", "items": {"type": "string"}, "description": "statement files or folders (full paths)"},
         "days": {"type": "number", "description": "how many days a transfer may take to arrive (default 3)"},
         "known": {"type": "string", "description": "people or accounts that are not unknown: comma-separated, or a list file"},
         "columns": {"type": "string", "description": "fix columns that were missed: role=header, comma-separated, e.g. 对方户名=收款人名称"},
         "sender": {"type": "string", "description": "only transfers sent by this holder"},
         "receiver": {"type": "string", "description": "only transfers sent to this holder"},
         "details": {"type": "boolean", "description": "also return the result rows (default false)"},
         "out": {"type": "string", "description": "folder for the results (default: next to the statements)"}},
         "required": ["files"]}},
    {"name": "office_find", "description": "Find words, names or numbers in a folder of files on this computer (Excel, "
     "WPS, CSV, Word, text, PDF when readable); numbers are found even when written with spaces or dashes. types finds "
     "every ID card, mobile, bank card or e-mail. Searched locally; saves an .xlsx of all hits. By default only where the "
     "hits are comes back; details=true also returns the text around each hit.",
     "inputSchema": {"type": "object", "properties": {
         "folder": {"type": "string", "description": "folder or file (full path)"},
         "words": {"type": "array", "items": {"type": "string"}},
         "types": {"type": "array", "items": {"type": "string", "enum": ["身份证", "手机", "银行卡", "邮箱"]}},
         "all_words": {"type": "boolean", "description": "only files that contain every word"},
         "details": {"type": "boolean"}, "out": {"type": "string"}},
         "required": ["folder"]}},
    {"name": "orch_retry", "description": "Queue a blocked, failed or cancelled task again.",
     "inputSchema": {"type": "object", "properties": {**_P, "task_id": {"type": "string"}},
                     "required": ["project", "task_id"]}},
    {"name": "orch_cancel", "description": "Cancel a queued, blocked or failed task (orch_retry brings it back). "
     "Only call it when the user asked for it.",
     "inputSchema": {"type": "object", "properties": {**_P, "task_id": {"type": "string"}},
                     "required": ["project", "task_id"]}},
]


class Server:
    def __init__(self, tools: OrchTools):
        self.tools = tools
        self.calls: dict[str, Callable[..., dict]] = {
            "orch_list_projects": lambda a: tools.list_projects(),
            "orch_list_tasks": lambda a: tools.list_tasks(a["project"], a.get("status", "")),
            "orch_task": lambda a: tools.task_detail(a["project"], a["task_id"]),
            "orch_add_task": lambda a: tools.add_task(a["project"], a["title"], a.get("spec", ""),
                                                      a.get("difficulty", "M"), a.get("risk", "normal"),
                                                      a.get("verify"), a.get("scope")),
            "orch_run": lambda a: tools.run(a["project"], a.get("task_ids"), a.get("auto_merge", True)),
            "orch_report": lambda a: tools.report(a["project"]),
            "orch_context": lambda a: tools.context(a["project"], a["task_id"]),
            "orch_merge": lambda a: tools.merge(a["project"], a["task_id"]),
            "orch_agents": lambda a: tools.agents(a.get("project", "")),
            "orch_pause": lambda a: tools.pause(a["project"], a["pool"], a.get("minutes")),
            "orch_resume": lambda a: tools.resume(a["project"], a["pool"]),
            "orch_swap": lambda a: tools.swap(a["project"], a["task_id"], a["pool"]),
            "orch_sessions": lambda a: tools.sessions(a.get("days", 7), a.get("include_orch", False)),
            "orch_retry": lambda a: tools.retry(a["project"], a["task_id"]),
            "orch_cancel": lambda a: tools.cancel(a["project"], a["task_id"]),
            "office_sheet_ask": lambda a: tools.sheet_ask(a["file"], a["question"], a.get("sheet", ""),
                                                          a.get("sensitive", False), a.get("mask", "")),
            "office_trace": lambda a: tools.trace(a["files"], a.get("days", 3), a.get("known", ""), a.get("columns", ""),
                                                  a.get("sender", ""), a.get("receiver", ""), a.get("details", False),
                                                  a.get("out", "")),
            "office_find": lambda a: tools.find(a["folder"], a.get("words"), a.get("types"), a.get("all_words", False),
                                                a.get("details", False), a.get("out", "")),
            "office_doc": lambda a: tools.doc(a["action"], a.get("request", ""), a.get("file", ""), a.get("materials"),
                                              a.get("style", "plain"), a.get("sensitive", False), a.get("mask", ""),
                                              a.get("out", "")),
        }

    def handle(self, msg: dict) -> dict | None:
        method, mid = msg.get("method"), msg.get("id")
        if mid is None:  # a notification (e.g. notifications/initialized): no answer
            return None
        try:
            if method == "initialize":
                asked = (msg.get("params") or {}).get("protocolVersion")
                result = {"protocolVersion": asked if asked in SUPPORTED else PROTOCOL, "capabilities": {"tools": {}},
                          "serverInfo": {"name": "orch", "version": "0.4"}}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                params = msg.get("params") or {}
                fn = self.calls.get(params.get("name", ""))
                if fn is None:
                    return _error(mid, -32602, f"unknown tool {params.get('name')!r}")
                try:
                    data = fn(params.get("arguments") or {})
                    result = {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=1)}]}
                except (ValueError, KeyError, subprocess.TimeoutExpired) as e:
                    result = {"content": [{"type": "text", "text": f"error: {e}"}], "isError": True}
            else:
                return _error(mid, -32601, f"method not found: {method}")
        except Exception as e:  # never let one bad request kill the server
            return _error(mid, -32603, str(e))
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def serve(self, stdin=None, stdout=None) -> None:
        stdin = stdin or sys.stdin
        stdout = stdout or sys.stdout
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                out = _error(None, -32700, "parse error")
            else:
                out = self.handle(msg) if isinstance(msg, dict) else _error(None, -32600, "invalid request")
            if out is not None:
                stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
                stdout.flush()


def _error(mid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def main(projects: list[str]) -> int:
    # stdout carries the protocol; keep it UTF-8 and line-buffered
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)  # type: ignore[attr-defined]
    sys.stdin.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    Server(OrchTools(projects)).serve()
    return 0
