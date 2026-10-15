"""Coding tasks from the web page: hand a task to orch on one of the projects the admin listed.

The web app only queues work and reports progress; orch does everything else (routing, worktrees,
acceptance, review, merge) exactly as on the command line. Agents run commands on this PC, so only the
admin and people the admin allows (`can_code`) may start tasks, and only on listed projects.
One run per project at a time; later jobs wait their turn.
"""
from __future__ import annotations

import re
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]           # where `python -m orch` resolves
TASK_ID = re.compile(r"\b(T-\d{4,})\b")
LOG_LIMIT = 20_000


class Coding:
    def __init__(self, app, python: str | None = None):
        self.app = app
        self.python = python or sys.executable
        self._locks: dict[str, threading.Lock] = {}
        self._threads: dict[int, threading.Thread] = {}

    # -- projects (admin) ----------------------------------------------------------
    def projects(self) -> list[dict]:
        return list(self.app.db.setting("code.projects", []) or [])

    def set_projects(self, items: list[dict]) -> list[dict]:
        clean, seen = [], set()
        for it in items:
            name, path = str(it.get("name", "")).strip()[:40], str(it.get("path", "")).strip()
            if not name or not path or name in seen:
                continue
            p = Path(path)
            if not (p / ".agents" / "orch.toml").is_file():
                raise ValueError(f"{path} 还不是 orch 项目：先在命令行运行 python -m orch -p \"{path}\" init")
            seen.add(name)
            clean.append({"name": name, "path": str(p)})
        self.app.db.set_setting("code.projects", clean)
        return clean

    def _project(self, name: str) -> dict:
        for p in self.projects():
            if p["name"] == name:
                return p
        raise ValueError("没有这个项目")

    # -- jobs ------------------------------------------------------------------------
    def submit(self, user_id: int, project: str, title: str, spec: str, difficulty: str, verify: list[str]) -> int:
        proj = self._project(project)
        if not title.strip():
            raise ValueError("请写清楚要做什么")
        if difficulty not in ("S", "M", "L"):
            raise ValueError("难度只能是 S / M / L")
        now = time.time()
        jid = self.app.db.x("INSERT INTO code_jobs(user_id, project, title, status, log, created_at, updated_at) "
                            "VALUES(?,?,?,?,?,?,?)", (user_id, project, title.strip()[:200], "queued", "", now, now))
        cmd = [self.python, "-m", "orch", "-p", proj["path"], "task", "add", "--title", title.strip()[:200],
               "--spec", spec.strip(), "--difficulty", difficulty, "--allow-duplicate"]
        for v in verify:
            if v.strip():
                cmd += ["--verify", v.strip()]
        out = self._run(cmd)
        m = TASK_ID.search(out)
        if not m:
            self._update(jid, status="failed", log=out)
            return jid
        self._update(jid, task_id=m.group(1), log=out)
        t = threading.Thread(target=self._work, args=(jid, proj, m.group(1)), daemon=True)
        self._threads[jid] = t
        t.start()
        return jid

    def _work(self, jid: int, proj: dict, task_id: str) -> None:
        lock = self._locks.setdefault(proj["path"], threading.Lock())
        with lock:
            self._update(jid, status="running")
            out = self._run([self.python, "-m", "orch", "-p", proj["path"], "run", task_id, "--auto-merge"],
                            timeout=4 * 3600)
            state = self.task_state(proj["path"], task_id)
            status = {"merged": "done", "verified": "done"}.get(state.get("status", ""), "failed")
            self._update(jid, status=status, log=self._log(jid) + "\n" + out)

    def _run(self, cmd: list[str], timeout: float = 120) -> str:
        try:
            p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, timeout=timeout,
                               stdin=subprocess.DEVNULL, env=_env())
            text = (p.stdout + p.stderr).decode("utf-8", errors="replace")
        except (OSError, subprocess.TimeoutExpired) as e:
            text = f"运行失败：{e}"
        return text[-LOG_LIMIT:]

    def _log(self, jid: int) -> str:
        rows = self.app.db.q("SELECT log FROM code_jobs WHERE id=?", (jid,))
        return rows[0]["log"] if rows else ""

    def _update(self, jid: int, **kw) -> None:
        kw["updated_at"] = time.time()
        self.app.db.x(f"UPDATE code_jobs SET {', '.join(k + '=?' for k in kw)} WHERE id=?", (*kw.values(), jid))

    def wait(self, jid: int, timeout: float = 60) -> None:  # tests
        t = self._threads.get(jid)
        if t:
            t.join(timeout)

    # -- status ----------------------------------------------------------------------
    @staticmethod
    def task_state(path: str, task_id: str) -> dict:
        """Read orch's own state (read-only) for one task."""
        db = Path(path) / ".agents" / "state.db"
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
            try:
                row = con.execute("SELECT status, note FROM tasks WHERE id=?", (task_id,)).fetchone()
                runs = con.execute("SELECT pool, status, verified FROM runs WHERE task_id=? ORDER BY id",
                                   (task_id,)).fetchall()
            finally:
                con.close()
        except sqlite3.Error:
            return {}
        if not row:
            return {}
        return {"status": row[0], "note": row[1] or "",
                "runs": [{"pool": r[0], "status": r[1], "verified": r[2]} for r in runs]}

    def jobs(self, user_id: int | None) -> list[dict]:
        sql = "SELECT * FROM code_jobs" + (" WHERE user_id=?" if user_id is not None else "") + " ORDER BY id DESC LIMIT 50"
        rows = self.app.db.q(sql, (user_id,) if user_id is not None else ())
        paths = {p["name"]: p["path"] for p in self.projects()}
        out = []
        for r in rows:
            state = self.task_state(paths[r["project"]], r["task_id"]) if r["task_id"] and r["project"] in paths else {}
            out.append({"id": r["id"], "project": r["project"], "task_id": r["task_id"], "title": r["title"],
                        "status": r["status"], "task_status": state.get("status"), "note": state.get("note", ""),
                        "runs": state.get("runs", []), "log": (r["log"] or "")[-4000:],
                        "created_at": r["created_at"]})
        return out


def _env() -> dict:
    import os

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    return env
