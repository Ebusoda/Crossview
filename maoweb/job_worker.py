"""One launch of a job (maoweb/jobs.py) in its own process; uses orch as a library without changing it.

stdin: one JSON payload {path, goal, context, jobs, stop_mode, plan_id, plans, task_ids, mode, actions, feedback,
previous_plan, parent_pid}. stdout: JSON lines {"event": ...}: log, planning, plan, tasks, note, applied, finished,
failed. Modes: "plan" (the planner proposes a plan), "run" (approve the plan, apply the user's decisions, build),
"cancel" (drop every unmerged task of the job). The process stops by itself when the web server goes away.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path, PureWindowsPath

from orch import workspace as ws
from orch.i18n import tr
from orch.models import TaskStatus
from orch.plan import PlanError, approve, run_planner, validate
from orch.proc import STOP, child_env, pid_alive
from orch.scheduler import OrchError, Orchestrator

CATEGORIES = ("frontend", "backend", "testing", "documentation", "research", "code_change")
LANGUAGE = ("\n\n## Language\nWrite PROGRESS.md, your final summary and, when reviewing, the review summary and every "
            "issue detail in the language of this specification's goal (Chinese if the goal is written in Chinese).\n")
RULES = ("每项任务的 type 只能为 frontend（前端）、backend（后端）、testing（测试）、"
         "documentation（文档）、research（项目内调研）或 code_change（其他代码修改）。"
         "每项必须包含具体 spec、项目内相对路径 scope、真实 verify 验收命令。"
         "允许独立任务并行；修改同一批文件的任务用 depends_on 排序。"
         "所有工作限于选定项目的文件成果，不安排登录、付款、发布或向他人发送消息。"
         "不要自行扩大用户目标。"
         "计划的 summary、每项任务的 title 和 spec 用执行目标所用的语言写（目标是中文就写中文）。")
SQUASH = re.compile(r"(T-\d+): ")   # orch's merge of an item on the integration branch: "<task id>: <title>"
_closed = threading.Event()   # stdout is gone: nobody reads what we say any more
_out = threading.Lock()        # orch's parallel workers talk at the same time: one whole line each
_merging = threading.Event()   # orch merges an item and checks the result (a stop waits for it to undo that merge)


def emit(event, **data):
    line = (json.dumps({"event": event, **data}, ensure_ascii=False) + "\n").encode("utf-8", "replace")
    with _out:
        if _closed.is_set():
            return
        try:
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()
        except (OSError, ValueError):
            _closed.set()
            STOP.set()


def watch(parent_pid):
    """Stop the work when the web server is gone (orch.run clears STOP when it starts, so keep setting it)."""
    while True:
        if _closed.is_set() or (parent_pid and not pid_alive(parent_pid)):
            STOP.set()
        time.sleep(1)


def check_plan(plan):
    """Fail closed on incomplete plans before creating executable tasks."""
    for task in plan.tasks:
        if task["type"] not in CATEGORIES:
            raise PlanError("任务分类不支持，请重新规划：" + task["type"])
        if not task["spec"] or not task["scope"] or not task["verify"]:
            raise PlanError("任务缺少具体要求、文件范围或验收方法：" + task["title"])
        for name in task["scope"]:
            normalized = name.replace("\\", "/")
            if (not normalized.strip() or Path(normalized).is_absolute() or
                    PureWindowsPath(name).drive or ".." in normalized.split("/")):
                raise PlanError("任务文件范围必须在项目内：" + name)


def planner_context(payload):
    """The chat discussion (if any), the rules, and on a new round the previous plan with the user's words."""
    context = payload.get("context") or ""
    head = ("\n网页讨论已结束。请根据明确的执行目标和讨论资料拆分任务，不把聊天里的问题当作执行指令。" if context
            else "\n请根据执行目标拆分任务。")
    text = context + head + RULES
    previous, feedback = payload.get("previous_plan") or {}, (payload.get("feedback") or "").strip()
    if previous.get("tasks"):
        titles = "\n".join(f"{i}. {t.get('title', '')}" for i, t in enumerate(previous["tasks"], 1))
        text += f"\n\n上一次的方案（用户没有同意，请重新出方案）：\n{previous.get('summary', '')}\n{titles}"
    if feedback:
        text += f"\n\n用户的修改意见（新方案要照这个改）：\n{feedback}"
    return text


def guard_merges(orch):
    """A stop while orch checks a merge lets orch finish: its check notices STOP and gives up, and orch undoes the
    merge before it stops (a KeyboardInterrupt in the middle would leave the unchecked merge on the integration
    branch). Once the merge is over, the stop goes on as usual."""
    merge = orch.merge

    def guarded(*args, **kwargs):
        _merging.set()
        try:
            return merge(*args, **kwargs)
        finally:
            _merging.clear()
            if STOP.is_set():
                raise KeyboardInterrupt
    orch.merge = guarded


def undo_unchecked(orch):
    """An item merged on the integration branch whose check never finished (this worker was killed meanwhile: on
    Windows a stop always is) is still there with the item not merged; orch would have undone it after the check.
    Undo it before anything else happens, unless the user's own folder has it already."""
    wt = orch.cfg.worktrees_dir / "_integration"
    if not wt.is_dir():
        return
    with orch.git_lock:
        for _ in range(20):
            top = ws.git(["log", "-1", "--format=%H%x00%s"], wt, check=False).strip()
            sha, _, subject = top.partition("\0")
            m = SQUASH.match(subject)
            task = orch.store.get_task(m.group(1)) if m else None
            if task is None or task.status == TaskStatus.MERGED:
                return
            if ws.git(["symbolic-ref", "-q", "--short", "HEAD"], wt, check=False).strip() != orch.integration_branch:
                return
            mine = subprocess.run(["git", "merge-base", "--is-ancestor", sha, "HEAD"], cwd=str(orch.project),
                                  capture_output=True, stdin=subprocess.DEVNULL, env=child_env())
            if mine.returncode != 1:                  # in the user's folder already, or no telling
                return
            ws.undo_last_commit(wt)
            orch.store.log("merge_undone", f"{sha[:12]}: the check after this merge never finished", task_id=task.id)
            orch.say(tr("[{0}] 合并后的验收被中断，已撤销合并；任务仍是 verified", task.id))


def fail_stale_plans(orch, payload):
    """Plans of this job left 'planning' by a process that died never finish by themselves."""
    for pid in payload.get("plans") or []:
        row = orch.store.get_plan(pid)
        if row is not None and row["status"] == "planning":
            orch.store.save_plan(pid, "failed", "", None, error="interrupted")


def plan_mode(orch, payload):
    with orch.lock():
        orch.recover()
        fail_stale_plans(orch, payload)
        try:
            pid, plan = run_planner(orch, payload["goal"], planner_context(payload),
                                    on_plan=lambda p: emit("planning", plan_id=p))
        except Exception as e:  # PlanError, or git / orch trouble on the way

            emit("failed", error=("商量方案没有成功：" + str(e))[:1000])
            return
        try:
            check_plan(plan)
        except PlanError as e:
            row = orch.store.get_plan(pid)
            orch.store.save_plan(pid, "failed", row["pool"] if row else "", plan, error=str(e))
            emit("failed", error=("方案不完整：" + str(e))[:1000])
            return
        emit("plan", plan_id=pid, plan={"summary": plan.summary, "tasks": plan.tasks, "warnings": plan.warnings})
        for old in dict.fromkeys([*(payload.get("plans") or []), payload.get("plan_id")]):   # the new one replaces them
            row = orch.store.get_plan(old) if old and old != pid else None
            if row is not None and row["status"] == "draft":
                orch.store.set_plan_status(old, "rejected")
    emit("finished", status="plan_ready")


def dependents(orch, ids, tid):
    """Tasks of the job that depend on tid, directly or through another one."""
    out, todo = [], [tid]
    while todo:
        cur = todo.pop()
        for other in ids:
            task = orch.store.get_task(other)
            if task and cur in task.depends_on and other not in out:
                out.append(other)
                todo.append(other)
    return out


def merge_failed_checks(orch, tid):
    """The task's newest merge failed the checks on the integration branch, and no work ran on it since."""
    since = max((r.started_at or 0 for r in orch.store.runs_for(tid, kind="work")), default=0)
    failed = [r for r in orch.store.logs_for(tid) if r["kind"] == "merge_failed" and (r["ts"] or 0) > since]
    return bool(failed) and str(failed[-1]["message"] or "").startswith("tests_failed")


def apply(orch, ids, act):
    """One decision of the user. Returns the task ids it queued again. Never fails the launch."""
    action, tid, text = act.get("action", ""), act.get("task_id", ""), act.get("text") or ""
    fits = {"merge": ("verified",), "revise": ("verified", "blocked", "failed"), "retry": ("blocked", "failed"),
            "accept": ("blocked", "failed"), "discard": ("queued", "verified", "blocked", "failed", "cancelled")}
    ok, requeued = False, []
    try:
        task = orch.store.get_task(tid) if tid in ids else None
        if task is None or action not in fits:
            raise OrchError(f"{tid or '?'} is not a task of this job, or {action!r} is unknown")
        if task.status.value not in fits[action]:
            raise OrchError(f"{tid} is {task.status.value}; {action} skipped")
        if action == "revise":
            if merge_failed_checks(orch, tid):   # the fix must see (and pass on) what the integration branch has now
                orch.sync_with_integration(tid)
            orch.requeue(tid, feedback=text)
            requeued.append(tid)
        elif action == "retry":
            orch.requeue(tid)
            requeued.append(tid)
        elif action == "discard":
            for t in [tid] + dependents(orch, ids, tid):
                cur = orch.store.get_task(t)
                if cur and cur.status not in (TaskStatus.MERGED, TaskStatus.RUNNING, TaskStatus.CANCELLED):
                    orch.cancel(t)
        else:
            if action == "accept":
                orch.accept(tid)
            if not orch.merge(tid):
                emit("note", task_id=tid, code="merge_failed", text=(orch.store.get_task(tid).note or "")[:300])
                emit("applied", task_id=tid, action=action, ok=False, text=text)
                return requeued
        ok = True
    except (OrchError, PlanError, ws.GitError, OSError) as e:
        emit("note", task_id=tid, code="action_failed", text=str(e)[:300])
    emit("applied", task_id=tid, action=action, ok=ok, text=text)
    return requeued


def final_status(orch, ids):
    tasks = {t: orch.store.get_task(t) for t in ids}
    status = {t: (task.status.value if task else "cancelled") for t, task in tasks.items()}
    values = set(status.values())
    if "verified" in values:
        return "waiting"
    if values & {"blocked", "failed"}:
        return "needs_attention"
    if values <= {"merged", "cancelled"}:
        return "done" if "merged" in values else "cancelled"
    return "needs_attention"   # stalled behind a dropped item, or items left for 继续做


def run_mode(orch, payload):
    requeued = []
    with orch.lock():
        orch.recover()
        undo_unchecked(orch)
        pid = payload.get("plan_id") or ""
        row = orch.store.get_plan(pid) if pid else None
        if row is None:
            raise PlanError("找不到这次的方案，请重新商量。")
        if row["status"] == "draft":
            check_plan(validate(json.loads(row["plan_json"])))
            ids = [t.id for t in approve(orch, pid)]
        elif row["status"] == "approved":
            ids = list(json.loads(row["task_map"] or "{}").values())
        else:
            raise PlanError("这份方案已经失效，请重新商量。")
        if not ids:
            raise PlanError("方案里没有要做的事。")
        for tid in ids:   # every item is reviewed by another company's agent, and reports in the goal's language
            task = orch.store.get_task(tid)
            if task and task.status == TaskStatus.QUEUED and "\n## Language\n" not in task.spec:
                task.review, task.spec = "always", task.spec.rstrip() + LANGUAGE
                orch.store.save_task(task)
        emit("tasks", task_ids=ids)
        for act in payload.get("actions") or []:
            requeued += apply(orch, ids, act)
    if payload.get("stop_mode") == "plan":
        orch.run(only=ids, auto_merge=True, jobs=int(payload.get("jobs") or 2))
    else:   # one item per launch, then the user decides
        orch.run(only=requeued or ids, auto_merge=False, jobs=1, max_tasks=1)
    emit("finished", status=final_status(orch, ids))


def cancel_mode(orch, payload):
    with orch.lock():
        orch.recover()
        undo_unchecked(orch)
        fail_stale_plans(orch, payload)
        ids = list(payload.get("task_ids") or [])
        for pid in payload.get("plans") or []:
            row = orch.store.get_plan(pid)
            if row is None:
                continue
            if row["status"] == "draft":
                orch.store.set_plan_status(pid, "rejected")
            ids += [t for t in json.loads(row["task_map"] or "{}").values() if t not in ids]
        for tid in ids:
            task = orch.store.get_task(tid)
            if task and task.status not in (TaskStatus.MERGED, TaskStatus.CANCELLED, TaskStatus.RUNNING):
                try:
                    orch.cancel(tid)
                except (OrchError, ws.GitError, OSError) as e:
                    emit("note", task_id=tid, code="action_failed", text=str(e)[:300])
    emit("finished", status="cancelled")


def run(payload):
    orch = Orchestrator(Path(payload["path"]), say=lambda text: emit("log", text=str(text)))
    guard_merges(orch)
    try:
        {"plan": plan_mode, "cancel": cancel_mode}.get(payload.get("mode"), run_mode)(orch, payload)
    finally:
        orch.store.close()


def _terminated(signum, frame):
    STOP.set()
    if not _merging.is_set():   # during a merge orch stops by itself, once it has undone what it could not check
        raise KeyboardInterrupt


def main():
    if os.name != "nt":
        signal.signal(signal.SIGTERM, _terminated)
    try:
        payload = json.load(sys.stdin)
        threading.Thread(target=watch, args=(int(payload.get("parent_pid") or 0),), daemon=True).start()
        run(payload)
        return 0
    except KeyboardInterrupt:   # paused or cancelled by the user, or the web server went away
        return 0
    except Exception as exc:
        emit("failed", error=str(exc)[:1000])
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
