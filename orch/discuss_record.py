"""Decision record of a discussion: typed entries, stage feedback, RECORD.md, approval, the carry-over from
earlier discussions of the same project, and the brief Cowork pulls over MCP.

Trust rule:
- `owner_decision` is written only by owner actions (the admin, on the web) and by approve_state, also when the
  engine auto-approves because of the owner's own control setting (then `by: "auto"`).
- `fact` is written only by MAO about what MAO itself did or checked (`by: "MAO"`).
- A model's text and Cowork's text are never a fact or an owner decision: they are `model_suggestion` (a claim) or
  `assumption`. A claim that calls itself a fact carries `claim: "fact"` and, where MAO checked it, `source_exists`.

Nothing here writes into a project folder: RECORD.md and BRIEF.md live in the discussion folder (Store.update
renders them). The MCP-facing functions at the end raise English ValueErrors only.
"""
from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

from . import discuss as d

KINDS = ("fact", "assumption", "model_suggestion", "owner_decision")
SECTIONS = ("requirement_changes", "approved_decisions", "rejected_plans", "open_questions", "notes",
            "review_records", "cowork_feedback", "earlier")
BRIEF_STATES = ("approved", "taken", "done")
REPORT_KINDS = ("progress", "blocker", "question", "done")
MAX_EVIDENCE = 4000
MAX_TAKER = 64
COMMIT_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
CARRIED_STEP = 150

KIND_LABELS = {"fact": "事实（MAO 记录）", "assumption": "假设", "model_suggestion": "模型建议",
               "owner_decision": "你的决定"}
SECTION_TITLES = {"requirement_changes": "需求变更与约束", "approved_decisions": "已批准决策",
                  "rejected_plans": "被否方案（及原因）", "open_questions": "待解决问题", "notes": "事实与假设",
                  "review_records": "模型评审记录", "cowork_feedback": "Cowork 反馈",
                  "earlier": "同一项目以前的讨论"}
STEP_LABELS = {"analysis": "独立分析", "review": "评审", "exchange": "交换意见", "revision": "修订方案",
               "ideate": "继续构思"}
REPORT_LABELS = {"progress": "进展", "blocker": "受阻", "question": "问题", "done": "完成"}
CHANGE_LABELS = {"change": "需求变更", "constraint": "新约束", "answer": "回答"}
COMMIT_CHECK = {True: "在项目仓库里存在", False: "项目仓库里没有，来源找不到", None: "没法核对"}
REPORT_WITH = "mao_report_progress(discussion_id, kind, text, evidence, commit)"
NO_QUEUE = ("Develop it yourself in Cowork. Do not queue it with orch_add_task or orch_run: MAO would then run the "
            "same work a second time.")


def _when(t: float | None, day_only: bool = False) -> str:
    if not t:
        return "—"
    return time.strftime("%Y-%m-%d" if day_only else "%Y-%m-%d %H:%M", time.localtime(float(t)))


def _clip(v, n: int) -> str:
    return str(v if v is not None else "").strip()[:n]


# -- entries -------------------------------------------------------------------------------------------------------
def add_entry(state: dict, section: str, kind: str, text: str, by: str, ref: str = "", source: str = "",
              **extra) -> dict:
    if section not in SECTIONS or kind not in KINDS:
        raise ValueError(f"bad entry {section}/{kind}")
    e = {"id": f"E{len(state['entries']) + 1}", "section": section, "kind": kind, "text": str(text), "by": str(by),
         "at": time.time(), "round": state.get("round", 1), "stage": len(state.get("stages") or []) + 1,
         "ref": ref, "source": source, "source_exists": None, "claim": "", "answered_by": ""}
    e.update(extra)
    state["entries"].append(e)
    return e


def open_questions(state: dict, stage_n: int | None = None) -> list[dict]:
    """The owner questions of the latest stage (or of `stage_n`) that the owner has not answered."""
    n = len(state.get("stages") or []) if stage_n is None else stage_n
    return [e for e in state.get("entries") or []
            if e["section"] == "open_questions" and e.get("stage") == n and not e.get("answered_by")]


_LINE_SUFFIX = re.compile(r"(?::\d+(?:-\d+)?|#L\d+(?:-L?\d+)?)$")


def source_inside(source: str, project_path: str | None) -> bool:
    """A relative path that resolves to an existing file or folder inside the project folder."""
    source = _LINE_SUFFIX.sub("", str(source or "").strip())
    if not project_path or not source:
        return False
    if source.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", source) or Path(source).is_absolute():
        return False
    try:
        root = Path(project_path).resolve()
        full = (root / source).resolve()
    except (OSError, ValueError):
        return False
    return d.contains(root, full) and full.exists()


def turn_entries(state: dict, turn: dict, project_path: str | None) -> None:
    """Record entries for one saved turn: its review record, assumptions, facts (checked) and owner questions."""
    side = turn["side"]
    out = turn["output"]
    label = STEP_LABELS.get(turn["step"], turn["step"])
    summary = out["plan"]["summary"][:200] if turn["status"] == "ok" else "（读不出结构）"
    add_entry(state, "review_records", "model_suggestion", f"{turn['id']} {d.NAMES[side]} {label} r{turn['round']}: {summary}",
              by=side, ref=turn["id"], source=turn["files"]["raw"])
    said = {" ".join(e["text"].split()) for e in state["entries"]
            if e["section"] == "notes" and e["kind"] == "assumption" and e["by"] == side}
    for a in out.get("assumptions") or []:
        if " ".join(str(a).split()) in said:
            continue
        said.add(" ".join(str(a).split()))
        add_entry(state, "notes", "assumption", a, by=side, ref=turn["id"])
    for f in out.get("facts") or []:
        if source_inside(f.get("source", ""), project_path):
            add_entry(state, "notes", "model_suggestion", f["text"], by=side, ref=turn["id"], source=f.get("source", ""),
                      source_exists=True, claim="fact")
        else:
            e = add_entry(state, "notes", "assumption", f["text"], by=side, ref=turn["id"], source=f.get("source", ""),
                          source_exists=False, claim="fact")
            turn["warnings"].append({"code": "unverified_fact", "side": side, "ref": e["id"]})
    n = len(state.get("stages") or []) + 1
    for q in out.get("questions_for_owner") or []:
        if any(d.similarity(q, e["text"]) >= d.SAME_POINT for e in open_questions(state, n)):
            continue
        add_entry(state, "open_questions", "model_suggestion", q, by=side, ref=turn["id"])


# -- stage feedback ----------------------------------------------------------------------------------------------
def _best(text: str, items: list[dict], key: str) -> tuple[dict | None, float]:
    best, score = None, 0.0
    for it in items or []:
        s = d.similarity(text, it.get(key, ""))
        if s >= d.SAME_POINT and s > score:
            best, score = it, s
    return best, score


def stage_turns(state: dict) -> list[dict]:
    """The turns since the previous stage card, in order."""
    seen = {tid for s in state.get("stages") or [] for tid in s.get("turns") or []}
    turns = [t for t in state.get("turns") or [] if t["id"] not in seen]
    return sorted(turns, key=lambda t: (t["group"], d.step_num(t["id"])))


def previous_version(state: dict, cur: dict | None) -> dict | None:
    """The latest version before `cur` that the owner did not reject."""
    if not cur:
        return None
    for p in reversed(state.get("plans") or []):
        if p["v"] < cur["v"] and p.get("status") != "rejected":
            return p
    return None


def _disagreements(state: dict, latest_ok: dict, prev_stage: dict | None) -> list[dict]:
    out: list[dict] = []
    first = state.get("first") or "gpt"
    for side in (first, d.other(first)):
        t = latest_ok.get(side)
        if not t:
            continue
        o = latest_ok.get(d.other(side))
        for x in t["output"]["disagree"]:
            if any(y["by"] != side and d.similarity(x["point"], y["point"]) >= d.SAME_POINT for y in out):
                continue   # the other side raised the same point; its entry keeps both reasons
            pos, oreason = "silent", ""
            if o:
                agree, sa = _best(x["point"], o["output"]["agree"], "point")
                dis, sd = _best(x["point"], o["output"]["disagree"], "point")
                if dis is not None and sd >= sa:
                    pos, oreason = "disagrees", dis.get("reason", "")
                elif agree is not None:
                    pos, oreason = "agrees", agree.get("reason", "")
            persistent = bool(x["severity"] == "major" and prev_stage and any(
                p.get("severity") == "major" and d.similarity(p["point"], x["point"]) >= d.SAME_POINT
                for p in prev_stage.get("disagreements") or []))
            out.append({"point": x["point"], "severity": x["severity"], "by": side, "reason": x.get("reason", ""),
                        "other": pos, "other_reason": oreason, "persistent": persistent})
    return out[:12]


def _consensus(ok_turns: list[dict], latest_ok: dict) -> list[dict]:
    items: list[dict] = []
    for t in ok_turns:
        for a in t["output"]["agree"]:
            m = next((c for c in items if d.similarity(c["point"], a["point"]) >= d.SAME_POINT), None)
            if m is None:
                items.append({"point": a["point"], "by": [t["side"]], "reasons": {t["side"]: a.get("reason", "")}})
                continue
            if t["side"] not in m["by"]:
                m["by"].append(t["side"])
            if a.get("reason"):
                m["reasons"][t["side"]] = a["reason"]
    out = []
    for c in items:
        if set(c["by"]) >= set(d.SIDES):
            sides = "both"
        else:
            o = latest_ok.get(d.other(c["by"][0]))
            if o and _best(c["point"], o["output"]["disagree"], "point")[0] is not None:
                continue   # contested: it shows up under disagreements with other "agrees"
            sides = "one"
        out.append({"point": c["point"], "sides": sides, "by": c["by"],
                    "reasons": {side: c["reasons"].get(side, "") for side in d.SIDES}})
    return out[:10]


def _risks(ok_turns: list[dict]) -> list[dict]:
    out: list[dict] = []
    for t in ok_turns:
        for r in t["output"]["risks"]:
            if any(d.similarity(r["risk"], x["risk"]) >= d.SAME_POINT for x in out):
                continue
            out.append({"risk": r["risk"], "mitigation": r.get("mitigation", ""), "by": t["side"]})
    return out[:8]


def stage_feedback(state: dict, kind: str) -> dict:
    """The stage card, assembled deterministically from the structured turns (no model involved)."""
    turns = stage_turns(state)
    n = len(state.get("stages") or []) + 1
    latest: dict[str, dict] = {}
    for t in turns:
        latest[t["side"]] = t
    latest_ok = {s: t for s, t in latest.items() if t["status"] == "ok"}
    ok = [t for t in turns if t["status"] == "ok"]
    prev_stage = state["stages"][-1] if state.get("stages") else None
    cur = d.current_plan(state)
    prev = previous_version(state, cur)
    sim = d.similarity(d.plan_text(cur), d.plan_text(prev)) if cur and prev else None
    disagreements = _disagreements(state, latest_ok, prev_stage)
    majors = any(x["severity"] == "major" for x in disagreements)
    converged = bool((sim is not None and sim >= d.STABLE and not majors) or
                     (latest and all(t["status"] == "ok" and not t["output"]["disagree"] for t in latest.values())))
    warnings: list[dict] = [{"code": "agree_not_proof"}]
    seen: set = {("agree_not_proof", "", "")}
    for t in turns:
        for w in t.get("warnings") or []:
            key = (w.get("code"), w.get("side", ""), w.get("ref", ""))
            if key not in seen:
                seen.add(key)
                warnings.append(dict(w))
    if cur:
        pt = next((t for t in state["turns"] if t["id"] == cur.get("turn")), None)
        if pt is not None and pt.get("changes_seen", 0) < d.max_change(state):
            warnings.append({"code": "stale_plan", "side": cur["by"], "ref": cur["turn"]})
    stage = {"n": n, "round": state.get("round", 1), "kind": kind, "at": time.time(), "turns": [t["id"] for t in turns],
             "current_plan": ({"v": cur["v"], "by": cur["by"], "summary": cur["summary"], "steps": list(cur["steps"]),
                               "acceptance": list(cur["acceptance"])} if cur else None),
             "consensus": _consensus(ok, latest_ok), "disagreements": disagreements, "risks": _risks(ok),
             "questions_for_owner": [{"id": e["id"], "text": e["text"], "by": e["by"]} for e in open_questions(state, n)],
             "next_step": {}, "converged": converged,
             "plan_change": round(1 - sim, 3) if sim is not None else None,
             "stale_turns": [t["id"] for t in turns if d.has_warning(t, "stale_change")],
             "warnings": warnings,
             "sides": {s: {"turn": t["id"]} for s, t in latest.items()}}
    stage["next_step"] = next_step(state, stage)
    return stage


def blocking_reasons(state: dict, stage: dict) -> set[str]:
    """What stops automatic continuation and auto-approve (an open major disagreement is not in here: it blocks
    auto-approve only)."""
    out = {w["code"] for w in stage.get("warnings") or [] if w.get("code") in d.BLOCKING}
    if any(x.get("persistent") for x in stage.get("disagreements") or []):
        out.add("persistent")
    if stage.get("questions_for_owner"):
        out.add("questions")
    return out


def next_step(state: dict, stage: dict) -> dict:
    """The recommended owner action (first rule that matches; never approve while a blocking warning is there)."""
    codes = {w.get("code") for w in stage.get("warnings") or []}
    rounds_ok = d.round_allowed(state)
    majors = any(x["severity"] == "major" for x in stage.get("disagreements") or [])

    def r(action: str, why: str) -> dict:
        return {"action": action, "why": why}

    if stage.get("questions_for_owner"):
        return r("edit_requirement", "answer_questions")
    if any(x.get("persistent") for x in stage.get("disagreements") or []):
        return r("edit_requirement", "owner_decides")
    if "cap_reached" in codes:
        return r("edit_requirement", "cap_reached")
    if "tool_call_seen" in codes:
        return r("edit_requirement", "tool_used")
    if "stale_plan" in codes and rounds_ok:
        return r("one_more_round", "stale_plan")
    if "rejected_reintroduced" in codes and rounds_ok:
        return r("one_more_round", "rejected_again")
    if "parse_failed" in codes and rounds_ok:
        return r("one_more_round", "unreadable")
    if codes & set(d.BLOCKING):
        return r("edit_requirement", "owner_decides")
    if majors and rounds_ok:
        return r("one_more_round", "more_round")
    if (stage.get("converged") or not majors) and d.current_plan(state):
        return r("approve", "stable")
    if d.call_allowed(state):
        return r("claude_review", "review")
    return r("edit_requirement", "owner_decides")


# -- RECORD.md -------------------------------------------------------------------------------------------------
def _fence(text: str) -> str:
    longest = max((len(m) for m in re.findall(r"`+", text or "")), default=0)
    return "`" * max(3, longest + 1)


def _carried_entries(state: dict) -> list[dict]:
    """The `earlier` section, derived from the carried snapshot (never stored as entries)."""
    car = state.get("carried") or {}
    out = []
    for r in car.get("rejected") or []:
        out.append({"id": r["ref"], "section": "earlier", "kind": "owner_decision", "by": r["ref"].split("/")[0],
                    "text": f"被否：{r['summary']}；原因：{r['reason']}", "at": None, "ref": r["ref"], "source": "",
                    "source_exists": None, "claim": "", "answered_by": ""})
    for x in car.get("decisions") or []:
        out.append({"id": x["ref"], "section": "earlier", "kind": "owner_decision", "by": x["ref"].split("/")[0],
                    "text": x["text"], "at": None, "ref": x["ref"], "source": "", "source_exists": None, "claim": "",
                    "answered_by": ""})
    cw = car.get("cowork")
    if cw:
        out.append({"id": cw["ref"], "section": "earlier", "kind": "model_suggestion", "by": f"cowork ({cw['ref']})",
                    "text": f"{REPORT_LABELS.get(cw.get('kind'), cw.get('kind'))}：{cw['text']}", "at": cw.get("at"),
                    "ref": cw["ref"], "source": "", "source_exists": None, "claim": "", "answered_by": ""})
    return out


def record_sections(state: dict) -> dict[str, list[dict]]:
    """Entries per section in RECORD.md order (the web's 决策记录 tab); `earlier` comes from the carried snapshot."""
    out: dict[str, list[dict]] = {s: [] for s in SECTIONS}
    for e in state.get("entries") or []:
        out.setdefault(e["section"], []).append(e)
    out["earlier"] = _carried_entries(state) + out.get("earlier", [])
    return out


def _one_line(text: str, n: int = 200) -> str:
    """The first non-empty line, clipped to `n`; "…" when anything was left out."""
    lines = [x.strip() for x in str(text or "").splitlines() if x.strip()]
    if not lines:
        return ""
    return lines[0][:n] + ("…" if len(lines) > 1 or len(lines[0]) > n else "")


def _entry_line(e: dict) -> str:
    text = str(e.get("text") or "").replace("\n", " ")
    cowork_claim = e.get("section") == "cowork_feedback" and e.get("kind") != "fact"
    if e.get("section") == "cowork_feedback" and e.get("report_kind"):
        text = f"[{REPORT_LABELS.get(e['report_kind'], e['report_kind'])}] {text}"
    if cowork_claim and e.get("source"):
        text += f"（提交 {str(e['source'])[:12]}：{COMMIT_CHECK.get(e.get('source_exists'), COMMIT_CHECK[None])}）"
    elif e.get("claim") == "fact":
        if e.get("source_exists") is True:
            text += "（模型说是事实；来源文件存在，MAO 没有核对内容）"
        elif e.get("source_exists") is False:
            text += "（来源找不到）"
    if cowork_claim and e.get("evidence"):
        text += f"（证据：{_one_line(e['evidence'])}）"
    if e.get("section") == "open_questions" and e.get("answered_by"):
        text += f"（已回答：{e['answered_by']}）"
    tail = f" {{ref: {e['ref']}}}" if e.get("ref") else ""
    return f"- [{KIND_LABELS.get(e['kind'], e['kind'])} · {e.get('by') or '—'} · {_when(e.get('at'), True)}] {text}{tail}"


def _plan_lines(plan: dict) -> list[str]:
    lines = [plan.get("summary") or "", ""]
    lines += [f"{i}. {s}" for i, s in enumerate(plan.get("steps") or [], 1)]
    if plan.get("acceptance"):
        lines += ["", "验收标准："] + [f"- {a}" for a in plan["acceptance"]]
    return lines


def render_record(state: dict) -> str:
    proj = state.get("project") or {}
    head = (f"- 项目：{proj.get('name') or '—'} · 第一个发言：{d.NAMES.get(state.get('first'), '—')} · "
            f"轮次：{state.get('round', 1)} · 调用：{state.get('calls', 0)} · 更新：{_when(state.get('updated_at'))}")
    req = state.get("requirement") or ""
    fence = _fence(req)
    sections = record_sections(state)
    out = [f"# 讨论记录 {state['id']}：{state.get('title', '')}", head, "", "## 原始需求（原文）", "",
           f"{fence}text", req, fence, ""]

    def block(title: str, entries: list[dict]) -> None:
        out.append(f"## {title}")
        out.append("")
        if entries:
            out.extend(_entry_line(e) for e in entries)
        else:
            out.append("（无）")
        out.append("")

    block(SECTION_TITLES["requirement_changes"], sections["requirement_changes"])
    plan = d.latest_plan(state)
    if plan:
        out += [f"## 当前方案（v{plan['v']}，{d.NAMES.get(plan['by'], plan['by'])}）", ""] + _plan_lines(plan) + [""]
    else:
        out += ["## 当前方案", "", "（还没有）", ""]
    for s in ("approved_decisions", "rejected_plans", "open_questions", "notes", "review_records", "cowork_feedback",
              "earlier"):
        block(SECTION_TITLES[s], sections[s])
    return "\n".join(out).rstrip("\n") + "\n"


# -- approval ----------------------------------------------------------------------------------------------------
def approve_state(state: dict, by: str, note: str = "", auto: bool = False) -> dict:
    """Pure: approve the current plan on this state (inside one Store.update)."""
    cur = d.current_plan(state)
    if cur is None:
        raise d.DiscussError("还没有可以批准的方案")
    cur["status"] = "approved"
    text = f"批准方案 v{cur['v']}：{cur['summary']}"
    if note:
        text += f"（备注：{note}）"
    add_entry(state, "approved_decisions", "owner_decision", text, by=by, ref=f"P{cur['v']}")
    now = time.time()
    stages = state.get("stages") or []
    plan_stage = next((s["n"] for s in reversed(stages) if cur.get("turn") in (s.get("turns") or [])), len(stages))
    state["brief"] = {"state": "approved", "approved_at": now, "approved_by": by, "auto": bool(auto), "note": note,
                      "plan_v": cur["v"], "stage_n": len(stages), "plan_stage_n": plan_stage, "takes": [],
                      "reports": [], "taken_by": "", "taken_at": None, "done_at": None}
    return state["brief"]


def approve(store: "d.Store", did: str, by: str, note: str = "", auto: bool = False) -> dict:
    return store.update(did, lambda st: approve_state(st, by, note, auto))


# -- brief -----------------------------------------------------------------------------------------------------------
def _brief_plan(state: dict) -> dict | None:
    b = state.get("brief") or {}
    for p in state.get("plans") or []:
        if p["v"] == b.get("plan_v"):
            return p
    return d.latest_plan(state)


def _brief_change(state: dict, c: dict) -> dict:
    out = {"id": c["id"], "kind": c["kind"], "text": c["text"]}
    if c["kind"] == "answer":
        out["question"] = d._question_text(state, c.get("ref", ""))[:200]
    return out


def build_brief(state: dict) -> dict:
    """The approved brief. Consensus, disagreements, risks and assumptions come from the stage that produced the
    approved plan; open questions from the latest stage at approval."""
    b = state.get("brief") or {}
    plan = _brief_plan(state) or {}
    stages = state.get("stages") or []
    stage_n = int(b.get("stage_n") or 0)
    plan_stage_n = int(b.get("plan_stage_n") or stage_n)
    stage = stages[plan_stage_n - 1] if 0 < plan_stage_n <= len(stages) else {}
    car = state.get("carried") or {}
    entries = state.get("entries") or []
    # this discussion's rejections in full (already bounded by MAX_TEXT and the plan summary cap): the owner's reason
    # is often where the constraint is; the carried ones were clipped by carry_over
    rejected = [{"id": r["id"], "summary": r["summary"], "reason": r["reason"]} for r in state.get("rejected") or []]
    rejected += [{"id": r["ref"], "summary": r["summary"], "reason": r["reason"]} for r in car.get("rejected") or []]
    return {
        "discussion_id": state["id"], "title": state.get("title", ""), "state": b.get("state", ""),
        "approved_at": b.get("approved_at"), "approved_by": b.get("approved_by", ""), "note": b.get("note", ""),
        "requirement": state.get("requirement", ""),
        "changes": [_brief_change(state, c) for c in state.get("changes") or []],
        "plan": {"v": plan.get("v"), "by": plan.get("by", ""), "summary": plan.get("summary", ""),
                 "steps": list(plan.get("steps") or []), "acceptance": list(plan.get("acceptance") or [])},
        "decisions": [e["text"] for e in entries if e["section"] == "approved_decisions" and e["kind"] == "owner_decision"],
        "consensus": [{"point": c["point"], "sides": c["sides"]} for c in stage.get("consensus") or []],
        "disagreements": [dict(x) for x in stage.get("disagreements") or []],
        "rejected": rejected,
        "earlier_decisions": [x["text"] for x in car.get("decisions") or []],
        "risks": [{"risk": r["risk"], "mitigation": r.get("mitigation", "")} for r in stage.get("risks") or []],
        "open_questions": [q["text"] for q in open_questions(state, stage_n)] if stage_n else [],
        "assumptions": [e["text"] for e in entries if e["section"] == "notes" and e["kind"] == "assumption"
                        and e.get("stage") == plan_stage_n][:15],
        "project": ({"name": state["project"]["name"], "path": state["project"]["path"]} if state.get("project") else None),
        "report_with": REPORT_WITH,
    }


def _status_line(b: dict) -> str:
    st = b.get("state", "")
    if st == "taken":
        return f"taken by {b.get('taken_by') or '?'} at {_when(b.get('taken_at'))}"
    return st or "—"


def render_brief(state: dict) -> str:
    b = state.get("brief") or {}
    x = build_brief(state)
    req = x["requirement"]
    fence = _fence(req)
    who = (b.get("approved_by") or "—") + ("（自动批准 / auto-approved）" if b.get("auto") else "")
    out = [f"# 开发简报 {x['discussion_id']}：{x['title']}",
           f"- Status: {_status_line(b)}", f"- Discussion: {x['discussion_id']}",
           f"- Approved by: {who} · {_when(b.get('approved_at'))}"]
    if x["project"]:
        out.append(f"- Project: {x['project']['name']} ({x['project']['path']})")
    out += ["", "## 原始需求（Original requirement, verbatim）", "", f"{fence}text", req, fence, ""]
    out += ["## 需求变更与约束（Changes and constraints）", ""]
    out += [f"- {c['id']} [answer to 「{c.get('question') or '?'}」] {c['text']}" if c["kind"] == "answer"
            else f"- {c['id']} [{c['kind']}] {c['text']}" for c in x["changes"]] or ["（无 / none）"]
    plan = x["plan"]
    out += ["", f"## 批准的方案 / 步骤 / 验收标准（Approved plan, steps, acceptance criteria）· v{plan['v']}", ""]
    out += [plan["summary"], ""] + [f"{i}. {s}" for i, s in enumerate(plan["steps"], 1)]
    out += ["", "验收标准（Acceptance criteria）："] + ([f"- {a}" for a in plan["acceptance"]] or ["（无 / none）"])
    out += ["", "## 架构决定与共识（Architecture decisions and consensus）", ""]
    lines = [f"- [你的决定 / owner] {t}" for t in x["decisions"]]
    lines += [f"- [以前的决定 / earlier] {t}" for t in x["earlier_decisions"]]
    lines += [f"- [{'双方' if c['sides'] == 'both' else '一方'}] {c['point']}" for c in x["consensus"]]
    out += lines or ["（无 / none）"]
    out += ["", "## 未解决的分歧（Open disagreements: both positions; 持续分歧 marked）", ""]
    dl = []
    for dis in x["disagreements"]:
        line = (f"- [{dis['severity']}] {dis['point']} — {d.NAMES.get(dis['by'], dis['by'])}: {dis.get('reason') or '—'}; "
                f"{d.NAMES.get(d.other(dis['by']), '')} ({dis['other']}): {dis.get('other_reason') or '—'}")
        if dis.get("persistent"):
            line += " [持续分歧 / persistent]"
        dl.append(line)
    out += dl or ["（无 / none）"]
    out += ["", "## 被否方案（Rejected plans: do not use again; this discussion and earlier ones）", ""]
    out += [f"- {r['id']}: {r['summary']} — 原因 / reason: {r['reason']}" for r in x["rejected"]] or ["（无 / none）"]
    out += ["", "## 风险（Risks）", ""]
    out += [f"- {r['risk']}" + (f" → {r['mitigation']}" if r["mitigation"] else "") for r in x["risks"]] or ["（无 / none）"]
    out += ["", "## 未决问题（Open questions: ask the owner）", ""]
    out += [f"- {q}" for q in x["open_questions"]] or ["（无 / none）"]
    out += ["", "## 未核实的假设（Unverified assumptions）", ""]
    out += [f"- {a}" for a in x["assumptions"]] or ["（无 / none）"]
    out += ["", "## 如何回报（How to report back）", "", NO_QUEUE, "",
            f'Report with mao_report_progress(discussion_id="{x["discussion_id"]}", kind="progress|blocker|question|done", '
            'text="...", evidence="...", commit="...").',
            "done needs evidence: test commands with their results, and the commit."]
    return "\n".join(out).rstrip("\n") + "\n"


# -- carry-over ----------------------------------------------------------------------------------------------------
def _carried_size(c: dict) -> int:
    """The size MAX_CARRIED_CHARS bounds. A rejected plan's steps are bounded on their own (at most 6 of
    CARRIED_STEP characters): they back the rejected-plan check and are the first thing the prompt budget drops."""
    n = sum(len(r["summary"]) + len(r["reason"]) for r in c["rejected"])
    n += sum(len(x["text"]) for x in c["decisions"])
    return n + (len(c["cowork"]["text"]) if c.get("cowork") else 0)


def carry_over(store: "d.Store", project_path: str, exclude: str = "") -> dict | None:
    """A bounded snapshot of what earlier discussions of the same project decided (newest first): rejected plans,
    owner decisions and Cowork's last blocker or question. None when there is nothing."""
    try:
        target = Path(project_path).resolve()
    except (OSError, ValueError, TypeError):
        return None
    rejected: list[tuple[float, dict]] = []
    decisions: list[tuple[float, dict]] = []
    cowork: dict | None = None
    sources: list[str] = []
    for s in store.list(limit=10 ** 6):
        if s["id"] == exclude or not (s.get("project") or {}).get("path"):
            continue
        try:
            if Path(s["project"]["path"]).resolve() != target:
                continue
            st = store.load(s["id"])
        except (d.DiscussError, OSError, ValueError):
            continue
        used = False
        for r in reversed(st.get("rejected") or []):
            if len(rejected) < 5:
                rejected.append((r.get("at") or 0, {"ref": f"{st['id']}/{r['id']}", "summary": _clip(r["summary"], 300),
                                                    "steps": [_clip(x, CARRIED_STEP) for x in (r.get("steps") or [])[:6]],
                                                    "reason": _clip(r["reason"], 300)}))
                used = True
        for e in reversed(st.get("entries") or []):
            if e["kind"] == "owner_decision" and e["section"] in ("approved_decisions", "requirement_changes") \
                    and len(decisions) < 8:
                decisions.append((e.get("at") or 0, {"ref": f"{st['id']}/{e['id']}", "text": _clip(e["text"], 300)}))
                used = True
        if cowork is None:
            for rep in reversed((st.get("brief") or {}).get("reports") or []):
                if rep.get("kind") in ("blocker", "question"):
                    cowork = {"ref": st["id"], "kind": rep["kind"], "text": _clip(rep.get("text"), 500),
                              "at": rep.get("at") or 0.0}
                    used = True
                    break
        if used:
            sources.append(st["id"])
    if not rejected and not decisions and cowork is None:
        return None
    snap = {"from": sources, "rejected": [x for _, x in rejected], "decisions": [x for _, x in decisions],
            "cowork": cowork}
    # drop the oldest items until the snapshot fits
    while _carried_size(snap) > d.MAX_CARRIED_CHARS:
        pool = [(at, "rejected", x) for at, x in rejected if x in snap["rejected"]]
        pool += [(at, "decisions", x) for at, x in decisions if x in snap["decisions"]]
        if snap["cowork"]:
            pool.append((snap["cowork"].get("at") or 0.0, "cowork", snap["cowork"]))
        if not pool:
            break
        at, where, item = min(pool, key=lambda p: p[0])
        if where == "cowork":
            snap["cowork"] = None
        else:
            snap[where].remove(item)
    if not snap["rejected"] and not snap["decisions"] and not snap["cowork"]:
        return None
    return snap


# -- MCP-facing (English errors only) -----------------------------------------------------------------------------
def _english(e: Exception, did: str = "") -> ValueError:
    msg = str(e)
    if msg == d.NO_SUCH:
        return ValueError(f"no such discussion: {did}")
    if msg == d.BUSY:
        return ValueError("the discussion record is busy; try again in a moment")
    return ValueError("MAO refused this request")


def _light(st: dict) -> dict:
    b = st.get("brief") or {}
    return {"discussion_id": st["id"], "title": st.get("title", ""), "state": b.get("state", ""),
            "approved_at": b.get("approved_at"), "taken_by": b.get("taken_by", ""), "taken_at": b.get("taken_at"),
            "done_at": b.get("done_at"), "project": st.get("project")}


def list_briefs(store: "d.Store", project_path: str | None = None, project_name: str = "", state: str | None = None,
                limit: int = 50) -> list[dict]:
    """Discussions with a brief, newest approval first. The project filter is the resolved path when the name is a
    registered orch project, else the discussion's project name."""
    if state and state not in BRIEF_STATES:
        raise ValueError("state is approved, taken or done")
    try:
        target = Path(project_path).resolve() if project_path else None
        out = []
        for s in store.list(limit=10 ** 6):
            if not s.get("brief_state") or (state and s["brief_state"] != state):
                continue
            proj = s.get("project") or {}
            if target is not None:
                if not proj.get("path") or Path(proj["path"]).resolve() != target:
                    continue
            elif project_name and proj.get("name") != project_name:
                continue
            out.append(_light(store.load(s["id"])))
        out.sort(key=lambda x: x.get("approved_at") or 0, reverse=True)
        return out[:max(1, int(limit or 50))]
    except d.DiscussError as e:
        raise _english(e) from None


def get_brief(store: "d.Store", did: str, taker: str = "cowork", take: bool = True) -> dict:
    """The approved brief. Taking it (once) marks it taken so nobody starts the same work twice."""
    did = str(did or "").strip()
    if not did:
        raise ValueError("give discussion_id")
    taker = str(taker or "").strip()[:MAX_TAKER] or "cowork"
    try:
        st = store.load(did)
        if not st.get("brief"):
            raise ValueError("this discussion has no approved brief")
        if st["brief"]["state"] == "approved" and take:
            def fn(s: dict) -> dict:
                b = s.get("brief")
                if b and b["state"] == "approved":
                    now = time.time()
                    b.update(state="taken", taken_by=taker, taken_at=now)
                    b.setdefault("takes", []).append({"by": taker, "at": now})
                    add_entry(s, "cowork_feedback", "fact", f"已被 {taker} 领取", by="MAO", ref=f"P{b.get('plan_v')}")
                    return {"took": True}
                return {"took": False}

            took = store.update(did, fn)["took"]
            st = store.load(did)
        else:
            took = False
        b = st["brief"]
        out = {"discussion_id": st["id"], "state": b["state"], "taken_by": b.get("taken_by", ""),
               "taken_at": b.get("taken_at")}
        if not took:
            if b["state"] == "taken":
                out["warning"] = (f"already taken by {b.get('taken_by') or '?'} at {_when(b.get('taken_at'))}; "
                                  "do not start it again unless the owner says so")
            elif b["state"] == "done":
                out["warning"] = "already done"
        out["brief"] = build_brief(st)
        out["brief_markdown"] = render_brief(st)
        return out
    except d.DiscussError as e:
        raise _english(e, did) from None


def commit_exists(project_path: str | None, sha: str) -> bool | None:
    """Whether `sha` is a commit in the project's git repository; None without a project or a repository."""
    if not project_path or not Path(project_path).is_dir():
        return None
    try:
        p = subprocess.run(["git", "-C", str(project_path), "rev-parse", "--is-inside-work-tree"], capture_output=True,
                           timeout=10, stdin=subprocess.DEVNULL)
        if p.returncode != 0 or p.stdout.decode(errors="replace").strip() != "true":
            return None
        p = subprocess.run(["git", "-C", str(project_path), "cat-file", "-e", f"{sha}^{{commit}}"], capture_output=True,
                           timeout=10, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.returncode == 0


def report_progress(store: "d.Store", did: str, text: str, kind: str = "progress", evidence: str = "",
                    commit: str = "", by: str = "cowork") -> dict:
    """Cowork reports back on a brief it took. Recorded as Cowork's claim; MAO checks only that a commit exists."""
    did = str(did or "").strip()
    if not did:
        raise ValueError("give discussion_id")
    kind = str(kind or "progress").strip()
    if kind not in REPORT_KINDS:
        raise ValueError("kind is progress, blocker, question or done")
    text = str(text or "").strip()
    if not text:
        raise ValueError("text is empty")
    if len(text) > d.MAX_TEXT:
        raise ValueError("text is too long (at most 4000 characters)")
    evidence = str(evidence or "").strip()
    if len(evidence) > MAX_EVIDENCE:
        raise ValueError("evidence is too long (at most 4000 characters)")
    if kind == "done" and len(evidence) < 10:
        raise ValueError("done needs evidence: test commands and results, commit")
    commit = str(commit or "").strip()
    if commit and not COMMIT_RE.match(commit):
        raise ValueError("commit must be 7 to 40 hex characters")
    try:
        st = store.load(did)
        _check_reportable(st)
        exists = commit_exists((st.get("project") or {}).get("path"), commit) if commit else None

        def fn(s: dict) -> dict:
            _check_reportable(s)
            b = s["brief"]
            taker = b.get("taken_by") or "?"
            who = by if by == taker else f"{by}:{taker}"
            extra = {"report_kind": kind, "evidence": evidence}
            if kind == "done":
                extra["claim"] = "fact"
            if commit and exists is not None:
                extra["source_exists"] = exists
            e = add_entry(s, "cowork_feedback", "model_suggestion", text, by=who, source=commit, **extra)
            if exists:
                add_entry(s, "cowork_feedback", "fact", f"提交 {commit[:12]} 在项目仓库里存在（MAO 只核对了这一点）",
                          by="MAO", ref=e["id"], source=commit, source_exists=True)
            now = time.time()
            b.setdefault("reports", []).append({"id": e["id"], "kind": kind, "text": text, "evidence": evidence,
                                                "commit": commit, "commit_exists": exists, "at": now, "by": who})
            if kind == "done":
                b["state"] = "done"
                b["done_at"] = now
            return {"ok": True, "entry_id": e["id"], "state": b["state"], "commit_exists": exists}

        return store.update(did, fn)
    except d.DiscussError as e:
        raise _english(e, did) from None


def _check_reportable(st: dict) -> None:
    b = st.get("brief")
    if not b:
        raise ValueError("this discussion has no approved brief")
    if b["state"] == "done":
        raise ValueError("this brief is done")
    if b["state"] != "taken":
        raise ValueError("take the brief with mao_get_brief first")
