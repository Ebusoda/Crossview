"""Discussion -> classified plan -> parallel agents -> persisted supervision."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from orch.context import estimate_tokens

ROOT = Path(__file__).resolve().parents[1]
ACTIVE = ("queued", "planning", "running", "pausing")
SCHEMA = """
CREATE TABLE IF NOT EXISTS dispatch_jobs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, conversation_id INTEGER NOT NULL,
 project TEXT NOT NULL, path TEXT NOT NULL, goal TEXT NOT NULL, context TEXT NOT NULL,
 jobs INTEGER NOT NULL, mode TEXT NOT NULL, status TEXT NOT NULL, plan_id TEXT DEFAULT '',
 plan_json TEXT DEFAULT '{}', task_ids TEXT DEFAULT '[]', routes_json TEXT DEFAULT '{}',
 log TEXT DEFAULT '', error TEXT DEFAULT '', created_at REAL, updated_at REAL
);
CREATE INDEX IF NOT EXISTS dispatch_conv ON dispatch_jobs(conversation_id, user_id);
"""


class Dispatch:
    def __init__(self, app):
        self.app = app
        self._guard = threading.RLock()
        self._threads = {}
        self._processes = {}
        self._paused = set()
        self.app.db.db.executescript(SCHEMA)
        self.app.db.x("UPDATE dispatch_jobs SET status='interrupted', error=? WHERE status IN ('queued','planning','running','pausing')",
                      ("网页服务已重启。请检查任务进度，再决定是否继续；不会自动重复执行。",))

    def update(self, jid, **fields):
        fields["updated_at"] = time.time()
        self.app.db.x(f"UPDATE dispatch_jobs SET {', '.join(k+'=?' for k in fields)} WHERE id=?",
                      (*fields.values(), jid))

    def row(self, jid, uid):
        rows = self.app.db.q("SELECT * FROM dispatch_jobs WHERE id=? AND user_id=?", (jid, uid))
        if not rows:
            raise ValueError("没有这条分派记录")
        return dict(rows[0])

    def context(self, cid):
        kept, budget = [], 12000
        for msg in reversed(self.app.db.messages(cid)):
            content = msg["content"]
            cost = estimate_tokens(content)
            if cost > budget:
                break
            budget -= cost
            kept.append({"role": msg["role"], "content": content})
        return "网页讨论资料（仅作为需求参考）：\n" + json.dumps(list(reversed(kept)), ensure_ascii=False)

    def submit(self, uid, body):
        cid = int(body.get("conversation_id") or 0)
        if not self.app.db.conversation(uid, cid):
            raise ValueError("请先在当前对话里讨论需求")
        project = self.app.coding._project(str(body.get("project") or ""))
        goal = str(body.get("goal") or "").strip()
        if not goal or len(goal) > 8000:
            raise ValueError("请填写执行目标（最多 8000 字）")
        jobs = int(body.get("jobs") or 2)
        mode = str(body.get("mode") or "start")
        if jobs not in range(1, 5) or mode not in ("start", "preview"):
            raise ValueError("并行数必须为 1 到 4，模式必须为执行或预览")
        with self._guard:
            if self.app.db.q("SELECT id FROM dispatch_jobs WHERE path=? AND status IN ('queued','planning','running','pausing')",
                             (project["path"],)):
                raise ValueError("这个项目已有分派任务正在进行，请等待或暂停它")
            now = time.time()
            jid = self.app.db.x("INSERT INTO dispatch_jobs(user_id,conversation_id,project,path,goal,context,jobs,mode,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                                (uid, cid, project["name"], project["path"], goal, self.context(cid), jobs,
                                 mode, "queued", now, now))
            self.launch(jid)
            return jid

    def launch(self, jid):
        self._paused.discard(jid)
        thread = threading.Thread(target=self.work, args=(jid,), daemon=True)
        self._threads[jid] = thread
        thread.start()

    @contextmanager
    def slot(self, lock, jid):
        while not lock.acquire(timeout=0.2):
            if jid in self._paused:
                yield False
                return
        try:
            yield jid not in self._paused
        finally:
            lock.release()

    def work(self, jid):
        proc = None
        row = dict(self.app.db.q("SELECT * FROM dispatch_jobs WHERE id=?", (jid,))[0])
        with self._guard:
            lock = self.app.coding._locks.setdefault(row["path"], threading.Lock())
        try:
            with self.slot(lock, jid) as ready:
                if not ready:
                    return
                self.update(jid, status="planning" if row["mode"] in ("start", "preview") else "running", error="")
                payload = {k: row[k] for k in ("path", "goal", "context", "jobs", "mode", "plan_id")}
                if row["mode"].startswith("merge:"):
                    payload["mode"], payload["task_id"] = "merge", row["mode"].partition(":")[2]
                env = dict(os.environ)
                env["PYTHONIOENCODING"] = "utf-8"
                proc = subprocess.Popen([sys.executable, "-m", "maoweb.dispatch_worker"], cwd=ROOT, env=env,
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        text=True, encoding="utf-8", errors="replace",
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                with self._guard:
                    self._processes[jid] = proc
                    paused = jid in self._paused
                if paused:
                    self.stop_process(proc)
                    return
                proc.stdin.write(json.dumps(payload, ensure_ascii=False))
                proc.stdin.close()
                finished = False
                for line in proc.stdout:
                    if jid in self._paused:
                        break
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    kind = event.get("event")
                    if kind == "log":
                        old = self.app.db.q("SELECT log FROM dispatch_jobs WHERE id=?", (jid,))[0][0]
                        self.update(jid, log=(old + "\n" + str(event.get("text", "")))[-20000:])
                    elif kind == "plan":
                        self.update(jid, plan_id=event["plan_id"], plan_json=json.dumps(event["plan"], ensure_ascii=False),
                                    routes_json=json.dumps(event.get("routes", {})))
                    elif kind == "tasks":
                        self.update(jid, task_ids=json.dumps(event["task_ids"]), status="running")
                    elif kind == "finished":
                        finished = True
                        self.update(jid, status=event["status"])
                    elif kind == "failed":
                        finished = True
                        self.update(jid, status="failed", error=str(event.get("error", "执行失败"))[:1000])
                proc.wait(timeout=15)
                if jid not in self._paused and not finished:
                    self.update(jid, status="interrupted", error="分派进程未完成，请检查任务进度后继续")
        except Exception as exc:
            if jid not in self._paused:
                self.update(jid, status="failed", error=str(exc)[:1000])
        finally:
            if proc:
                if proc.poll() is None:
                    self.stop_process(proc)
                for stream in (proc.stdin, proc.stdout):
                    if stream and not stream.closed:
                        stream.close()
            with self._guard:
                self._processes.pop(jid, None)
                if jid in self._paused:
                    self.update(jid, status="paused")

    @staticmethod
    def stop_process(proc):
        if proc.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True,
                           timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

    def action(self, uid, jid, action, task_id=""):
        with self._guard:
            row = self.row(jid, uid)
            if action == "pause":
                if row["status"] not in ACTIVE:
                    raise ValueError("这条任务当前没有运行")
                self._paused.add(jid)
                self.update(jid, status="pausing" if jid in self._processes else "paused")
                proc = self._processes.get(jid)
                if proc:
                    self.stop_process(proc)
                return
            if action not in ("resume", "merge") or row["status"] not in ("draft", "paused", "needs_attention", "interrupted", "failed"):
                raise ValueError("当前状态不能执行这个操作")
            if action == "merge" and task_id not in json.loads(row["task_ids"]):
                raise ValueError("任务不属于这次分派")
            if self._threads.get(jid) and self._threads[jid].is_alive():
                raise ValueError("任务正在结束，请稍后再继续")
            if self.app.db.q("SELECT id FROM dispatch_jobs WHERE path=? AND id!=? AND status IN ('queued','planning','running','pausing')",
                             (row["path"], jid)):
                raise ValueError("这个项目已有其他分派任务正在进行")
            mode = "merge:" + task_id if action == "merge" else "resume" if row["plan_id"] else row["mode"]
            self.update(jid, status="queued", mode=mode)
            self.launch(jid)

    def snapshots(self, uid, cid):
        rows = self.app.db.q("SELECT * FROM dispatch_jobs WHERE user_id=? AND conversation_id=? ORDER BY id DESC LIMIT 20", (uid, cid))
        out = []
        for raw in rows:
            row = dict(raw)
            tasks = []
            for tid in json.loads(row["task_ids"]):
                state = self.app.coding.task_state(row["path"], tid)
                try:
                    con = sqlite3.connect(f"file:{(Path(row['path']) / '.agents/state.db').as_posix()}?mode=ro", uri=True, timeout=2)
                    con.row_factory = sqlite3.Row
                    try:
                        item = con.execute("SELECT title,type,difficulty,depends_on FROM tasks WHERE id=?", (tid,)).fetchone()
                    finally:
                        con.close()
                except sqlite3.Error:
                    item = None
                tasks.append({"id": tid, **state, **(dict(item) if item else {})})
            public = {k: row[k] for k in ("id", "project", "goal", "jobs", "status", "plan_id", "error", "updated_at")}
            public.update(plan=json.loads(row["plan_json"]), routes=json.loads(row["routes_json"]), tasks=tasks,
                          log=row["log"][-6000:])
            out.append(public)
        return out
