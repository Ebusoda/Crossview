"""V2 step 1: GPT and Claude discuss a requirement, the owner decides.

The owner (the admin of the web app) writes a requirement. GPT and Claude each analyse it on their own,
read-only; then they exchange structured conclusions, the first speaker revises the plan, and Crossview lays out a
stage card (orch/discuss_record.py) from which the owner decides what happens next. Once the owner approves,
Cowork pulls the brief over MCP (orch/mcp.py) and reports back.

    <root>/DS-0001/discussion.json   the whole content state (holds no raw model text)
    <root>/DS-0001/RECORD.md         decision record, rendered after every update
    <root>/DS-0001/BRIEF.md          once approved
    <root>/DS-0001/turns/            prompt, raw answer and event stream of every step
    <root>/DS-0001/.lock             OS lock file

Nothing is ever written into a project folder. Models never get write access: see orch/discuss_call.py.
Messages for the web are Chinese DiscussErrors (maoweb/static/i18n.js gives them English); the MCP functions
turn them into English ValueErrors. Log lines are English. Warnings and reasons are codes.
"""
from __future__ import annotations

import copy
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from .context import estimate_tokens
from .retrieval import words as _words

REPO = Path(__file__).resolve().parents[1]

SIDES = ("gpt", "claude")
NAMES = {"gpt": "GPT", "claude": "Claude"}
EFFORTS = ("", "low", "medium", "high")
# "" = CLI default. Only these three, the same on both sides, so one control means the same thing on both.
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\[\]-]{0,63}$")   # or "" (= CLI default)
WORDS_MIN, WORDS_MAX, WORDS_DEFAULT = 100, 2000, 600
MAX_REQUIREMENT, MAX_TEXT = 8000, 4000
MAX_CHANGES, MAX_CHANGES_CHARS = 20, 12000        # requirement changes, constraints and answers, all together
MAX_REJECTED = 10
MAX_CARRIED_CHARS = 3000
ROUNDS_MIN, ROUNDS_MAX = 1, 4                     # automatic rounds
HARD_ROUNDS, HARD_CALLS = 6, 40                   # absolute caps incl. owner-requested steps and repairs
DEFAULT_TIMEOUT_S, REPAIR_TIMEOUT_S = 900, 300
STABLE, REJECT_SIMILAR, SAME_POINT = 0.9, 0.6, 0.6
PROMPT_TOKENS = 24000                             # estimate_tokens budget for one prompt (a cost guide, not a hard limit)
REPAIR_KEEP = 6000                                # chars of the previous answer a repair prompt quotes (the tail)
DEFAULT_CONTROLS = {"max_rounds": 2, "pause_after_each_stage": True, "independent_review": True,
                    "converge": True, "wait_for_approval": True, "arbiter": "off"}
BLOCKING = ("rejected_reintroduced", "parse_failed", "cap_reached", "tool_call_seen", "stale_plan")
INFO = ("over_limit", "unverified_fact", "repaired", "rejected_touched", "agree_not_proof", "no_reason",
        "prompt_large", "stale_change", "auto_approved")
STEPS = ("analysis", "review", "exchange", "revision", "ideate")
OWNER_ACTIONS = ("back_to_gpt", "claude_review", "one_more_round", "edit_requirement", "reject_plan", "approve")
CHANGE_KINDS = ("change", "constraint", "answer")
ID_RE = re.compile(r"^DS-\d{4,}$")
TURN_FILE_RE = re.compile(r"^S\d+-(gpt|claude)-\w+(\.repair)?\.raw\.txt$")
LIST_ITEMS, ITEM_CHARS = 10, 400

# Chinese messages for the web (maoweb/static/i18n.js has their English); MCP gets English (discuss_record).
NO_SUCH = "没有这场讨论"
BUSY = "讨论记录正忙，请稍后再试"
NOT_NOW = "当前状态不能执行这个操作"
CAPPED = "这场讨论已经到上限了：请批准、修改需求或停止"
DATA_INSIDE = "Crossview 自己的数据文件夹在这个项目里面，模型会读到别的讨论和家人的聊天，不能关联这个项目"
NO_TURN_FILE = "没有这一步的原始记录"


class DiscussError(ValueError):
    """Chinese message for the web; the MCP functions turn it into an English ValueError."""


class Interrupted(Exception):
    """The engine's stop event was set (the worker passes orch.proc.STOP)."""


class StepFailed(Exception):
    def __init__(self, side: str, step: str, status: str, error: str, resets_at: float | None = None):
        super().__init__(f"{step} {side}: {status}: {error}"[:600])
        self.side, self.step, self.status, self.error, self.resets_at = side, step, status, error, resets_at


# -- roots and validation ----------------------------------------------------------------------------------
def default_root() -> Path:
    """$MAO_DISCUSS_DIR, else ($MAO_WEB_DATA or <repo>/.maoweb)/discussions (the web app's own data folder)."""
    env = os.environ.get("MAO_DISCUSS_DIR")
    if env:
        return Path(env)
    web = os.environ.get("MAO_WEB_DATA")
    return (Path(web) if web else REPO / ".maoweb") / "discussions"


def data_folders(store: "Store") -> list[Path]:
    """Crossview's own folders a model must never see: the store root, the default root and the web data folder."""
    out: list[Path] = []
    for p in (store.root, default_root(), Path(os.environ.get("MAO_WEB_DATA") or REPO / ".maoweb")):
        r = Path(p).resolve()
        if r not in out:
            out.append(r)
    return out


def contains(folder: Path, inner: Path) -> bool:
    """`inner` is `folder` or somewhere below it (both resolved)."""
    return inner == folder or folder in inner.parents


def other(side: str) -> str:
    return "claude" if side == "gpt" else "gpt"


def _int(v: Any) -> int | None:
    if isinstance(v, bool):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def validate(requirement: str, first: str, sides: dict | None, controls: dict | None) -> tuple[str, str, dict, dict]:
    requirement = str(requirement or "").strip()
    if not requirement:
        raise DiscussError("请写清楚要讨论什么")
    if len(requirement) > MAX_REQUIREMENT:
        raise DiscussError("需求写得太长了，最多 8000 字")
    if first not in SIDES:
        raise DiscussError("第一个发言的只能是 GPT 或 Claude")
    clean_sides: dict[str, dict] = {}
    for side in SIDES:
        s = (sides or {}).get(side) or {}
        if not isinstance(s, dict):
            s = {}
        model = str(s.get("model") or "").strip()
        if model and not MODEL_RE.match(model):
            raise DiscussError("模型名称不对")
        effort = str(s.get("effort") or "").strip()
        if effort not in EFFORTS:
            raise DiscussError("推理强度只能是 low、medium 或 high")
        raw = s.get("output_limit_words", WORDS_DEFAULT)
        words = WORDS_DEFAULT if raw in (None, "") else _int(raw)
        if words is None or not WORDS_MIN <= words <= WORDS_MAX:
            raise DiscussError("每轮字数上限必须是 100 到 2000")
        clean_sides[side] = {"model": model, "effort": effort, "output_limit_words": words}
    c = dict(DEFAULT_CONTROLS)
    given = controls or {}
    for k in DEFAULT_CONTROLS:
        if k in given:
            c[k] = given[k]
    rounds = _int(c["max_rounds"])
    if rounds is None or not ROUNDS_MIN <= rounds <= ROUNDS_MAX:
        raise DiscussError("自动轮数必须是 1 到 4")
    if str(c["arbiter"] or "off") != "off":
        raise DiscussError("第三方裁判这一版还没有")
    clean = {"max_rounds": rounds, "arbiter": "off"}
    for k in ("pause_after_each_stage", "independent_review", "converge", "wait_for_approval"):
        clean[k] = bool(c[k])
    return requirement, first, clean_sides, {k: clean[k] for k in DEFAULT_CONTROLS}


def title_of(requirement: str) -> str:
    """First non-empty line without Markdown marks, up to the first 。, at most 60 characters (as maoweb's
    make_title; orch never imports maoweb)."""
    line = next((ln for ln in (requirement or "").splitlines() if ln.strip()), "")
    line = re.sub(r"^\s*(?:#+|>|[-*+]|\d+[.)])\s+", "", line)
    line = re.sub(r"\s+", " ", re.sub(r"[*`~]+", "", line)).strip()
    return (line.split("。")[0].strip() or line)[:60]


_WORD = re.compile(r"[A-Za-z0-9_]+")
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿]")


def count_words(text: str) -> int:
    """ASCII word tokens plus one per CJK character."""
    text = text or ""
    return len(_WORD.findall(text)) + len(_CJK.findall(text))


def output_words(output: Any) -> int:
    """Words over every parsed string value of a TurnOutput (no keys, no JSON syntax)."""
    if isinstance(output, str):
        return count_words(output)
    if isinstance(output, dict):
        return sum(output_words(v) for v in output.values())
    if isinstance(output, list):
        return sum(output_words(v) for v in output)
    return 0


def similarity(a: str, b: str) -> float:
    """Jaccard over orch.retrieval.words(); both empty -> 1.0."""
    wa, wb = _words(a or ""), _words(b or "")
    if not wa and not wb:
        return 1.0
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def plan_text(plan: dict | None) -> str:
    plan = plan or {}
    return str(plan.get("summary") or "") + "\n" + "\n".join(str(s) for s in plan.get("steps") or [])


def step_num(sid: str) -> int:
    try:
        return int(str(sid).lstrip("S"))
    except ValueError:
        return 0


def turn_base(step: dict) -> str:
    return f"{step['id']}-{step['side']}-{step['step']}"


def _clip(v: Any, n: int) -> str:
    return str(v if v is not None else "").strip()[:n]


# -- state helpers (pure) ------------------------------------------------------------------------------------
def new_state(did: str, requirement: str, first: str, sides: dict, controls: dict, project: dict | None,
              by: str, carried: dict | None) -> dict:
    now = time.time()
    return {"version": 1, "id": did, "title": title_of(requirement), "created_at": now, "updated_at": now,
            "created_by": by, "project": project, "requirement": requirement, "first": first, "sides": sides,
            "controls": controls, "round": 1, "calls": 0, "next_step": 1, "next_group": 1, "finalized_group": 0,
            "queue": [], "running": [], "turns": [], "plans": [], "stages": [], "changes": [], "rejected": [],
            "carried": carried or None, "entries": [], "events": [], "last_error": None, "brief": None}


def add_event(state: dict, action: str, by: str = "", text: str = "", kind: str = "", ref: str = "") -> dict:
    ev = {"at": time.time(), "action": action, "text": text, "kind": kind, "by": by}
    if ref:
        ev["ref"] = ref
    state["events"].append(ev)
    return ev


def current_plan(state: dict) -> dict | None:
    for p in reversed(state.get("plans") or []):
        if p.get("status") == "current":
            return p
    return None


def latest_plan(state: dict) -> dict | None:
    """The plan the owner should look at: the current or the approved one."""
    for p in reversed(state.get("plans") or []):
        if p.get("status") in ("current", "approved"):
            return p
    return None


def max_change(state: dict) -> int:
    """The number of the highest C-id (0 when there are no changes)."""
    out = 0
    for c in state.get("changes") or []:
        try:
            out = max(out, int(str(c["id"]).lstrip("C")))
        except ValueError:
            continue
    return out


def round_shape(state: dict, round_n: int) -> list[list[tuple[str, str, bool]]]:
    """Groups of (step, side, writes_plan) for one round (section 3.3 of the contract)."""
    a = state["first"]
    b = other(a)
    c = state["controls"]
    if round_n <= 1:
        if c.get("independent_review", True):
            groups = [[("analysis", a, True), ("analysis", b, False)], [("exchange", a, False), ("exchange", b, False)]]
        else:
            groups = [[("analysis", a, True)], [("review", b, False)]]
    else:
        groups = [[("exchange", a, False), ("exchange", b, False)]]
    if c.get("converge", True):
        groups.append([("revision", a, True)])
    else:
        groups[-1] = [(s, side, wp or side == a) for s, side, wp in groups[-1]]
    return groups


def planned_calls(state: dict, round_n: int) -> int:
    return sum(len(g) for g in round_shape(state, round_n))


def round_allowed(state: dict) -> bool:
    """One more round fits the hard caps."""
    return state["round"] + 1 <= HARD_ROUNDS and \
        state["calls"] + len(state["queue"]) + planned_calls(state, state["round"] + 1) <= HARD_CALLS


def call_allowed(state: dict, n: int = 1) -> bool:
    return state["calls"] + len(state["queue"]) + n <= HARD_CALLS


def limits_left(state: dict) -> dict:
    return {"changes_left": max(0, MAX_CHANGES - len(state.get("changes") or [])),
            "rejects_left": max(0, MAX_REJECTED - len(state.get("rejected") or []))}


def _new_step(state: dict, step: str, side: str, group: int, round_n: int, writes_plan: bool, stage_end: bool,
              stage_kind: str, note: str) -> dict:
    sid = f"S{state['next_step']:02d}"
    state["next_step"] += 1
    return {"id": sid, "group": group, "round": round_n, "step": step, "side": side, "writes_plan": writes_plan,
            "stage_end": stage_end, "stage_kind": stage_kind, "note": note}


def enqueue_round(state: dict, round_n: int, note: str = "") -> None:
    groups = round_shape(state, round_n)
    for i, g in enumerate(groups):
        gid = state["next_group"]
        state["next_group"] += 1
        for step, side, wp in g:
            state["queue"].append(_new_step(state, step, side, gid, round_n, wp, i == len(groups) - 1, "round", note))


def enqueue_single(state: dict, step: str, side: str, stage_kind: str, note: str = "") -> None:
    gid = state["next_group"]
    state["next_group"] += 1
    state["queue"].append(_new_step(state, step, side, gid, state["round"], True, True, stage_kind, note))


def pending_group(state: dict) -> int | None:
    """A group whose turns are all saved but that was never finalized (a worker died in between)."""
    queued = {s["group"] for s in state.get("queue") or []}
    groups = sorted({t["group"] for t in state.get("turns") or [] if t["group"] > state.get("finalized_group", 0)})
    for g in groups:
        if g not in queued:
            return g
    return None


def add_plan_version(state: dict, turn: dict) -> dict:
    for p in state["plans"]:
        if p["status"] == "current":
            p["status"] = "superseded"
    plan = turn["output"]["plan"]
    v = {"v": len(state["plans"]) + 1, "by": turn["side"], "turn": turn["id"], "group": turn["group"],
         "round": turn["round"], "summary": plan["summary"], "steps": list(plan["steps"]),
         "acceptance": list(plan["acceptance"]), "status": "current", "at": time.time()}
    state["plans"].append(v)
    return v


# -- prompts --------------------------------------------------------------------------------------------------
ROLE = {"analysis": "independent analysis", "review": "review of the current plan",
        "exchange": "exchange of conclusions", "revision": "one revised plan",
        "ideate": "keep developing the plan"}
TASK = {
    "analysis": "Analyse the requirement on your own. Another reviewer does the same separately; you will see its "
                "conclusion later. Propose the plan you think is best, with risks, assumptions and owner questions.",
    "review": "Review the current plan critically: accept points only with your own reason, give each problem a "
              "concrete reason and a severity, then give the plan as you would change it.",
    "exchange": "Compare the other reviewer's conclusion with yours. Keep your position where you have a reason; "
                "change it where the other one is right and say so in `changes`. Give your revised plan.",
    "revision": "Write ONE revised plan that resolves the open disagreements where there is a good reason. Keep any "
                "disagreement you do not resolve in `disagree` with your reason; the owner decides. Do not drop the "
                "other reviewer's points silently.",
    "ideate": "The owner asks you to keep developing the plan (see the owner's note). Improve or extend it, consider "
              "alternatives, keep what works.",
}
FOLDER_PROJECT = ("You are in READ-ONLY mode: read files in the project folder (your working directory) if useful, "
                  "but never create, edit or delete files and never run commands that change anything.")
FOLDER_NONE = ("There is no project folder for this discussion: answer from the requirement alone and do not read "
               "any files. You are in READ-ONLY mode.")
SCHEMA = """{
  "plan": {"summary": "the plan in a few sentences", "steps": ["one concrete step per item (at most 12)"],
           "acceptance": ["how the owner can check that it is done (at most 8)"]},
  "agree": [{"point": "what you agree with", "reason": "your own reason"}],
  "disagree": [{"point": "what you disagree with", "reason": "your concrete reason", "severity": "major or minor"}],
  "risks": [{"risk": "what can go wrong", "mitigation": "how to avoid or limit it"}],
  "assumptions": ["what you assume without having checked it"],
  "questions_for_owner": ["a product or direction decision only the owner can make"],
  "changes": ["what you changed since your previous conclusion, and why"],
  "facts": [{"text": "something you checked in the project files", "source": "relative/path/in/the/project"}],
  "rejected_conflicts": [{"id": "R1", "note": "how your plan differs from that rejected plan"}]
}"""
OUTPUT_HEAD = "## Output: exactly one JSON object, nothing else (a ```json block is fine)"


def conclusion(output: dict, n: int = 6) -> dict:
    """What the other side sees of a turn: never raw text, assumptions or questions."""
    plan = output.get("plan") or {}
    return {"plan": {"summary": _clip(plan.get("summary"), 300), "steps": [_clip(s, 300) for s in (plan.get("steps") or [])[:8]]},
            "agree": [{"point": _clip(a.get("point"), 300), "reason": _clip(a.get("reason"), 300)}
                      for a in (output.get("agree") or [])[:n]],
            "disagree": [{"point": _clip(d.get("point"), 300), "reason": _clip(d.get("reason"), 300),
                          "severity": d.get("severity", "minor")} for d in (output.get("disagree") or [])[:n]],
            "risks": [{"risk": _clip(r.get("risk"), 300), "mitigation": _clip(r.get("mitigation"), 300)}
                      for r in (output.get("risks") or [])[:n]]}


def latest_turn(state: dict, side: str, before_group: int) -> dict | None:
    """The latest readable turn of a side from a strictly earlier group (the selection rule)."""
    best = None
    for t in state.get("turns") or []:
        if t["side"] == side and t["group"] < before_group and t.get("status") == "ok":
            if best is None or (t["group"], step_num(t["id"])) > (best["group"], step_num(best["id"])):
                best = t
    return best


def _question_text(state: dict, ref: str) -> str:
    for e in state.get("entries") or []:
        if e["id"] == ref:
            return e["text"]
    return ""


def _concl_for(state: dict, turn: dict, n: int) -> dict:
    c = conclusion(turn["output"], n)
    if state.get("rejected"):
        last = state["rejected"][-1]
        if step_num(turn["id"]) < int(last.get("before_step") or 0):
            c["plan"] = {"summary": f"(rejected by the owner as {last['id']}; do not reuse)"}
    return c


def _render_prompt(state: dict, step: dict, lists_n: int, plan_steps: int, rejected_full: bool) -> str:
    side, kind = step["side"], step["step"]
    g = step["group"]
    project = state.get("project")
    out = [f"<!-- crossview:discuss step={kind} side={side} round={step['round']} id={step['id']} -->",
           f"# Crossview discussion: {ROLE.get(kind, kind)}",
           "You are one of two independent reviewers of a software plan. "
           + (FOLDER_PROJECT if project else FOLDER_NONE)
           + " This is planning only: do not claim that anything is implemented or tested, and do not call any "
             "tool that changes anything.",
           "",
           "## Original requirement (verbatim from the owner; it is the authority)",
           "<<<", state["requirement"], ">>>"]
    if state.get("changes"):
        out.append("## Requirement changes, constraints and answers from the owner "
                   "(in order; later ones win where they conflict)")
        for c in state["changes"]:
            if c["kind"] == "answer":
                q = _question_text(state, c.get("ref", ""))[:200].replace('"', "'")
                out.append(f'- {c["id"]} [answer to: "{q}"] {c["text"]}')
            else:
                out.append(f"- {c['id']} [{c['kind']}] {c['text']}")
    car = state.get("carried")
    if car and (car.get("rejected") or car.get("decisions") or car.get("cowork")):
        out.append("## Earlier for this project (from the owner's earlier discussions; its decisions still apply "
                   "unless a change above says otherwise)")
        for r in car.get("rejected") or []:
            line = f"- Rejected there, do NOT propose again: {r['ref']}: {r['summary']}"
            if rejected_full and r.get("steps"):
                line += " | steps: " + "; ".join(_clip(s, 150) for s in r["steps"][:6])
            out.append(line + f" | reason: {r['reason']}")
        for d in car.get("decisions") or []:
            out.append(f"- Owner decision there: {d['text']}")
        if car.get("cowork"):
            cw = car["cowork"]
            out.append(f"- Cowork's last report there ({cw.get('kind', '')}): {cw.get('text', '')}")
    if state.get("rejected"):
        out.append("## Rejected plans: the owner rejected these; do NOT propose them again")
        for r in state["rejected"]:
            line = f"- {r['id']}: {_clip(r['summary'], 300)}"
            if rejected_full and r.get("steps"):
                line += " | steps: " + "; ".join(_clip(s, 150) for s in r["steps"][:6])
            out.append(line + f" | reason: {_clip(r['reason'], 300)}")
    if kind != "analysis":
        cur = current_plan(state)
        if cur:
            who = "you" if cur["by"] == side else "the other reviewer"
            out.append(f"## Current plan (v{cur['v']}, written by {who})")
            out.append(cur["summary"])
            for i, s in enumerate(cur["steps"][:plan_steps], 1):
                out.append(f"{i}. {s}")
            if cur.get("acceptance"):
                out.append("Acceptance:")
                out += [f"- {a}" for a in cur["acceptance"]]
        elif state.get("plans") and state["plans"][-1]["status"] == "rejected":
            v = state["plans"][-1]["v"]
            rid = next((r["id"] for r in reversed(state.get("rejected") or []) if r.get("plan_v") == v),
                       state["rejected"][-1]["id"] if state.get("rejected") else "R?")
            out.append("## Current plan")
            out.append(f"The owner rejected the last plan ({rid}). Propose a different plan; do not rework the "
                       "rejected one.")
    own = latest_turn(state, side, g) if kind != "analysis" else None
    oth = latest_turn(state, other(side), g) if kind in ("exchange", "revision", "review") else None
    if own:
        out.append("## Your previous conclusion")
        out.append(json.dumps(_concl_for(state, own, lists_n), ensure_ascii=False))
    if oth:
        out.append("## The other reviewer's conclusion (structured; you do not see its full text)")
        out.append(json.dumps(_concl_for(state, oth, lists_n), ensure_ascii=False))
    if kind == "revision" or (kind == "exchange" and step["round"] >= 2):
        lines = []
        for t, who in ((own, "you"), (oth, "the other reviewer")):
            for d in ((t or {}).get("output") or {}).get("disagree", [])[:lists_n]:
                lines.append(f"- [{d['severity']}] {_clip(d['point'], 300)}: {_clip(d['reason'], 300)} (raised by {who})")
        if lines:
            out.append("## Open disagreements")
            out += lines
    if step.get("note"):
        out.append("## Owner's note for this step")
        out.append(step["note"])
    out.append("## Your task")
    out.append(TASK.get(kind, TASK["review"]))
    rules = ["Judge independently. Do not agree by default: every agree item needs your own reason. Two reviewers "
             "agreeing is not proof.",
             "Never reinterpret or shorten the original requirement.",
             "If your plan overlaps a rejected plan (here or from earlier discussions), list its id in "
             "rejected_conflicts and explain the difference."]
    if project:
        rules.append("Put in `facts` only what you checked in the project files, with the path relative to the "
                     "project folder. Everything else unverified goes in `assumptions`.")
    rules.append("`questions_for_owner`: only product or direction decisions that the owner must make.")
    words = int((state["sides"].get(side) or {}).get("output_limit_words") or WORDS_DEFAULT)
    rules.append(f"At most {words} words in total (Chinese: characters). Write all text values in the language of "
                 "the original requirement.")
    out.append("## Rules")
    out += [f"{i}. {r}" for i, r in enumerate(rules, 1)]
    out.append(OUTPUT_HEAD)
    out.append(SCHEMA)
    return "\n".join(out) + "\n"


def build_prompt(state: dict, step: dict) -> tuple[str, dict]:
    """The prompt for one step and {"changes_seen", "prompt_tokens", "warnings"}. Shrinks to PROMPT_TOKENS in this
    order: conclusion lists to 3, plan steps to 6, rejected plans to summary and reason. The requirement and the
    changes are never shortened."""
    tries = [(6, 12, True), (3, 12, True), (3, 6, True), (3, 6, False)]
    prompt, tokens = "", 0
    for lists_n, plan_steps, rejected_full in tries:
        prompt = _render_prompt(state, step, lists_n, plan_steps, rejected_full)
        tokens = estimate_tokens(prompt)
        if tokens <= PROMPT_TOKENS:
            break
    warnings = [] if tokens <= PROMPT_TOKENS else [{"code": "prompt_large", "side": step["side"]}]
    return prompt, {"changes_seen": max_change(state), "prompt_tokens": tokens, "warnings": warnings}


def repair_prompt(state: dict, step: dict, raw: str) -> str:
    raw = raw or ""
    quoted = raw if len(raw) <= REPAIR_KEEP else "[the start of your answer is cut here]\n" + raw[-REPAIR_KEEP:]
    return "\n".join([
        f"<!-- crossview:discuss step=repair side={step['side']} round={step['round']} id={step['id']} -->",
        "# Crossview discussion: repair your answer",
        "You are in READ-ONLY mode: do not read any files and do not call any tool.",
        "Your previous answer could not be read as the required JSON object. Rewrite it as exactly that object. "
        "Do not add or drop content; keep every point and reason.",
        "## Your previous answer",
        "<<<", quoted, ">>>",
        OUTPUT_HEAD, SCHEMA]) + "\n"


# -- parsing --------------------------------------------------------------------------------------------------
_FENCE = re.compile(r"```[ \t]*json[ \t]*\r?\n(.*?)```", re.S | re.I)
EMPTY_OUTPUT = {"plan": {"summary": "", "steps": [], "acceptance": []}, "agree": [], "disagree": [], "risks": [],
                "assumptions": [], "questions_for_owner": [], "changes": [], "facts": [], "rejected_conflicts": []}


def parse_turn(text: str) -> dict | None:
    """The answer's JSON object: fenced ```json blocks last first, then raw_decode from the last `{` backwards.
    The first dict with a `plan` key wins, so the final answer wins over an example quoted earlier."""
    text = text or ""
    for m in reversed(list(_FENCE.finditer(text))):
        try:
            obj = json.loads(m.group(1))
        except ValueError:
            continue
        if isinstance(obj, dict) and "plan" in obj:
            return obj
    dec = json.JSONDecoder()
    i = text.rfind("{")
    while i >= 0:
        try:
            obj, _ = dec.raw_decode(text, i)
        except ValueError:
            obj = None
        if isinstance(obj, dict) and "plan" in obj:
            return obj
        i = text.rfind("{", 0, i)
    return None


def _s(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, (bool, int, float)):
        return str(v)
    if isinstance(v, dict):
        for k in ("text", "point", "summary", "risk", "question", "value", "description"):
            if isinstance(v.get(k), str):
                return v[k].strip()
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list):
        return "; ".join(x for x in (_s(i) for i in v) if x)
    return str(v)


def _l(v: Any) -> list:
    if v is None or v == "":
        return []
    if isinstance(v, list):
        return v
    return [v]


def _strings(v: Any, n: int = LIST_ITEMS, chars: int = ITEM_CHARS) -> list[str]:
    return [x for x in (_s(i)[:chars] for i in _l(v)) if x][:n]


def _pairs(v: Any, key: str, second: str, alt: tuple[str, ...] = ()) -> list[dict]:
    out = []
    for item in _l(v):
        if isinstance(item, dict):
            first = _s(item.get(key) if item.get(key) is not None else next(
                (item[a] for a in alt if item.get(a) is not None), ""))
            d = {key: first[:ITEM_CHARS], second: _s(item.get(second))[:ITEM_CHARS]}
            if key == "point" and "severity" in item:
                d["severity"] = item.get("severity")
        else:
            d = {key: _s(item)[:ITEM_CHARS], second: ""}
        if d[key]:
            out.append(d)
    return out[:LIST_ITEMS]


def normalize(obj: dict) -> tuple[dict, list[dict]]:
    out = copy.deepcopy(EMPTY_OUTPUT)
    warnings: list[dict] = []
    plan = obj.get("plan")
    if isinstance(plan, dict):
        out["plan"] = {"summary": _s(plan.get("summary"))[:1200], "steps": _strings(plan.get("steps"), 12, 300),
                       "acceptance": _strings(plan.get("acceptance"), 8, 300)}
    else:
        out["plan"] = {"summary": _s(plan)[:1200], "steps": [], "acceptance": []}
    out["agree"] = _pairs(obj.get("agree"), "point", "reason", ("text",))
    dis = _pairs(obj.get("disagree"), "point", "reason", ("text",))
    for d in dis:
        sev = str(d.get("severity") or "").strip().lower()
        d["severity"] = "major" if sev in ("blocker", "critical", "high", "major") else "minor"
    out["disagree"] = dis
    out["risks"] = _pairs(obj.get("risks"), "risk", "mitigation", ("text",))
    for k in ("assumptions", "questions_for_owner", "changes"):
        out[k] = _strings(obj.get(k))
    out["facts"] = _pairs(obj.get("facts"), "text", "source")
    out["rejected_conflicts"] = [{"id": d["id"][:40], "note": d["note"]}
                                 for d in _pairs(obj.get("rejected_conflicts"), "id", "note")]
    if any(not d["reason"] for d in dis):
        warnings.append({"code": "no_reason"})
    return out, warnings


def turn_checks(state: dict, turn: dict) -> None:
    """Deterministic per-turn checks; their warnings go on the turn (section 3.6)."""
    side = turn["side"]
    w = turn["warnings"]
    out = turn["output"]
    limit = int((state["sides"].get(side) or {}).get("output_limit_words") or WORDS_DEFAULT)
    turn["over_limit"] = turn["words"] > 1.3 * limit
    if turn["over_limit"]:
        w.append({"code": "over_limit", "side": side})
    if turn["status"] == "ok":
        text = plan_text(out["plan"])
        # like with like: a whole plan against a whole rejected plan; a carried rejection saved before carried
        # snapshots kept steps is compared summary against summary
        candidates = [(r["id"], text, plan_text(r)) for r in state.get("rejected") or []]
        for r in (state.get("carried") or {}).get("rejected") or []:
            if r.get("steps"):
                candidates.append((r["ref"], text, plan_text(r)))
            else:
                candidates.append((r["ref"], str(out["plan"].get("summary") or ""), str(r.get("summary") or "")))
        for ref, mine, rtext in candidates:
            score = similarity(mine, rtext)
            if score >= REJECT_SIMILAR:
                w.append({"code": "rejected_reintroduced", "side": side, "ref": ref, "score": round(score, 2)})
        for rc in out.get("rejected_conflicts") or []:
            w.append({"code": "rejected_touched", "side": side, "ref": rc["id"]})
    if turn.get("tool_calls"):
        w.append({"code": "tool_call_seen", "side": side})
    if turn.get("changes_seen", 0) < max_change(state):
        w.append({"code": "stale_change", "side": side})


def has_warning(item: dict, code: str) -> bool:
    return any(w.get("code") == code for w in item.get("warnings") or [])


# -- group end -----------------------------------------------------------------------------------------------
def finalize_group(state: dict, g: int) -> dict | None:
    """Plan versions, group checks, finalized_group and (at a stage end) the stage card and the decision, all on
    one state (the caller saves it in one update). Returns the new stage, if any."""
    from . import discuss_record as rec

    turns = sorted((t for t in state["turns"] if t["group"] == g), key=lambda t: step_num(t["id"]))
    top = max_change(state)
    for t in turns:
        if t.get("changes_seen", 0) < top and not has_warning(t, "stale_change"):
            t["warnings"].append({"code": "stale_change", "side": t["side"]})
    for t in turns:
        if t.get("writes_plan") and t.get("status") == "ok":
            add_plan_version(state, t)
    state["finalized_group"] = max(int(state.get("finalized_group") or 0), g)
    if not any(t.get("stage_end") for t in turns):
        return None
    kind = turns[0].get("stage_kind") or "round"
    stage = rec.stage_feedback(state, kind)
    state["stages"].append(stage)
    decision = decide(state, stage)
    stage["decision"] = decision
    if decision == "continue":
        state["round"] += 1
        enqueue_round(state, state["round"])
    elif decision == "approved":
        rec.approve_state(state, by="auto", auto=True)
        stage["warnings"].append({"code": "auto_approved"})
        add_event(state, "approve", by="auto")
    return stage


def decide(state: dict, stage: dict) -> str:
    """continue | approved | waiting after a stage (section 3.7)."""
    from . import discuss_record as rec

    c = state["controls"]
    blocking = rec.blocking_reasons(state, stage)
    open_major = any(d["severity"] == "major" for d in stage["disagreements"])
    if stage["kind"] == "round" and not c["pause_after_each_stage"] and state["round"] < c["max_rounds"] \
            and not stage["converged"] and not blocking:
        if round_allowed(state):
            return "continue"
        stage["warnings"].append({"code": "cap_reached"})
        stage["next_step"] = rec.next_step(state, stage)
        return "waiting"
    if not c["wait_for_approval"] and current_plan(state) and (stage["converged"] or state["round"] >= c["max_rounds"]) \
            and not blocking and not open_major:
        return "approved"
    return "waiting"


# -- store ------------------------------------------------------------------------------------------------------
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _thread_lock(folder: Path) -> threading.Lock:
    key = os.path.normcase(os.path.abspath(str(folder)))
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


def os_lock(f) -> None:
    """Non-blocking exclusive OS lock (raises OSError when another process holds it); as hold_instance_lock."""
    if os.name == "nt":
        import msvcrt

        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def os_unlock(f) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


class Store:
    """Discussions under one root folder. No disk access until create()."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.say: Callable[[str], None] | None = None
        self._cache: dict[str, tuple[tuple[int, int], dict]] = {}
        self._cache_lock = threading.Lock()

    def _log(self, text: str) -> None:
        try:
            if self.say:
                self.say(text)
            else:
                print(text, file=sys.stderr)
        except Exception:  # noqa: BLE001  (logging must never fail an update)
            pass

    # -- paths and reading -----------------------------------------------------------------------
    def path(self, did: str) -> Path:
        if not isinstance(did, str) or not ID_RE.match(did):
            raise DiscussError(NO_SUCH)
        d = self.root / did
        if not (d / "discussion.json").is_file():
            raise DiscussError(NO_SUCH)
        return d

    def _ids(self) -> list[str]:
        if not self.root.is_dir():
            return []
        ids = [p.name for p in self.root.iterdir() if ID_RE.match(p.name) and (p / "discussion.json").is_file()]
        return sorted(ids, key=lambda d: int(d[3:]), reverse=True)

    @staticmethod
    def _read(d: Path) -> dict:
        last: Exception | None = None
        for i in range(5):
            try:
                return json.loads((d / "discussion.json").read_text(encoding="utf-8"))
            except (PermissionError, json.JSONDecodeError, FileNotFoundError) as e:
                last = e
                time.sleep(0.05)
        raise DiscussError(BUSY) from last

    def load(self, did: str) -> dict:
        return self._read(self.path(did))

    # -- writing -----------------------------------------------------------------------------------
    def _replace(self, tmp: Path, dest: Path, wait: float = 3.0) -> None:
        deadline = time.monotonic() + wait
        while True:
            try:
                os.replace(tmp, dest)
                return
            except PermissionError:
                if time.monotonic() >= deadline:
                    try:
                        tmp.unlink()
                    except OSError:
                        pass
                    raise DiscussError(BUSY) from None
                time.sleep(0.05)

    def _write(self, d: Path, state: dict) -> None:
        tmp = d / "discussion.json.tmp"
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        self._replace(tmp, d / "discussion.json")

    def _render(self, d: Path, state: dict) -> None:
        """RECORD.md (and BRIEF.md once approved). A failure here is logged; the update stands."""
        from . import discuss_record as rec

        for name in ("RECORD.md", "BRIEF.md"):
            if name == "BRIEF.md" and not state.get("brief"):
                continue
            try:
                text = rec.render_record(state) if name == "RECORD.md" else rec.render_brief(state)
                tmp = d / (name + ".tmp")
                tmp.write_text(text, encoding="utf-8")
                self._replace(tmp, d / name, wait=1.0)
            except Exception as e:  # noqa: BLE001
                self._log(f"{d.name}: could not write {name}: {type(e).__name__}: {e}")

    def create(self, requirement: str, first: str, sides: dict | None, controls: dict | None,
               project: dict | None = None, by: str = "", carried: dict | None = None) -> str:
        requirement, first, sides, controls = validate(requirement, first, sides, controls)
        proj = None
        if project and project.get("path"):
            ppath = Path(project["path"]).resolve()
            if any(contains(ppath, df) for df in data_folders(self)):
                raise DiscussError(DATA_INSIDE)
            proj = {"name": str(project.get("name") or ppath.name), "path": str(ppath)}
        self.root.mkdir(parents=True, exist_ok=True)
        while True:
            n = max((int(p.name[3:]) for p in self.root.iterdir() if ID_RE.match(p.name)), default=0) + 1
            did = f"DS-{n:04d}"
            try:
                (self.root / did).mkdir()
                break
            except FileExistsError:
                continue
        d = self.root / did
        state = new_state(did, requirement, first, sides, controls, proj, by, carried)
        enqueue_round(state, 1)
        add_event(state, "create", by=by)
        self._write(d, state)
        self._render(d, state)
        return did

    def update(self, did: str, fn: Callable[[dict], Any], lock_timeout: float = 10.0) -> Any:
        """Load, fn(state) (which mutates), save, all under an in-process lock plus an OS lock."""
        d = self.path(did)
        deadline = time.monotonic() + lock_timeout
        tl = _thread_lock(d)
        if not tl.acquire(timeout=max(0.0, lock_timeout)):
            raise DiscussError(BUSY)
        try:
            with open(d / ".lock", "a+b") as f:
                while True:
                    try:
                        os_lock(f)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise DiscussError(BUSY) from None
                        time.sleep(0.05)
                try:
                    state = self._read(d)
                    result = fn(state)
                    state["updated_at"] = time.time()
                    self._write(d, state)
                    self._render(d, state)
                    return result
                finally:
                    os_unlock(f)
        finally:
            tl.release()

    # -- summaries ---------------------------------------------------------------------------------
    def summary(self, did: str) -> dict:
        d = self.path(did)
        st = (d / "discussion.json").stat()
        key = (st.st_mtime_ns, st.st_size)
        with self._cache_lock:
            hit = self._cache.get(did)
        if hit and hit[0] == key:
            return copy.deepcopy(hit[1])
        s = self.load(did)
        out = {"id": s["id"], "title": s.get("title", ""), "created_at": s.get("created_at"),
               "updated_at": s.get("updated_at"), "project": s.get("project"), "first": s.get("first"),
               "round": s.get("round"), "brief_state": (s.get("brief") or {}).get("state", ""),
               "queue_len": len(s.get("queue") or []), "last_stage_n": len(s.get("stages") or []),
               "usage": [{"side": t["side"], "ended_at": t.get("ended_at"), **{k: (t.get("usage") or {}).get(k)
                          for k in ("calls", "tokens_in", "tokens_out", "tokens_cached", "est_cost_usd")}}
                         for t in s.get("turns") or []]}
        with self._cache_lock:
            self._cache[did] = (key, out)
        return copy.deepcopy(out)

    def list(self, limit: int = 200) -> list[dict]:
        out = []
        for did in self._ids():
            if len(out) >= limit:
                break
            try:
                out.append(self.summary(did))
            except (DiscussError, OSError, KeyError, TypeError):
                continue
        return out

    def read_turn_file(self, did: str, name: str) -> str:
        d = self.path(did)
        if not isinstance(name, str) or not TURN_FILE_RE.match(name):
            raise DiscussError(NO_TURN_FILE)
        try:
            return (d / "turns" / name).read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return ""

    def usage_since(self, since: float) -> dict:
        out = {side: {"calls": 0, "tokens_in": 0, "tokens_out": 0, "tokens_cached": 0, "est_cost_usd": None}
               for side in SIDES}
        for s in self.list(limit=10 ** 6):
            for u in s["usage"]:
                if (u.get("ended_at") or 0) < since or u.get("side") not in out:
                    continue
                o = out[u["side"]]
                for k in ("calls", "tokens_in", "tokens_out", "tokens_cached"):
                    o[k] += int(u.get(k) or 0)
                if u["side"] == "claude" and u.get("est_cost_usd"):
                    o["est_cost_usd"] = round((o["est_cost_usd"] or 0.0) + float(u["est_cost_usd"]), 6)
        return out


# -- owner actions (content side) -------------------------------------------------------------------------------
def owner_action(store: Store, did: str, action: str, text: str = "", kind: str = "", ref: str = "",
                 by: str = "") -> dict:
    """-> {"launch": bool}. Content-level checks only; maoweb checks the process status first."""
    from . import discuss_record as rec

    if action not in OWNER_ACTIONS:
        raise DiscussError(NOT_NOW)
    text = str(text or "").strip()
    if len(text) > MAX_TEXT:
        raise DiscussError("写得太长了，最多 4000 字")

    def fn(st: dict) -> dict:
        if action == "back_to_gpt":
            if not call_allowed(st):
                raise DiscussError(CAPPED)
            enqueue_single(st, "ideate", "gpt", "ideate", text)
            add_event(st, action, by=by, text=text)
            return {"launch": True}
        if action == "claude_review":
            if not call_allowed(st):
                raise DiscussError(CAPPED)
            enqueue_single(st, "review", "claude", "review", text)
            add_event(st, action, by=by, text=text)
            return {"launch": True}
        if action == "one_more_round":
            if not round_allowed(st):
                raise DiscussError(CAPPED)
            st["round"] += 1
            enqueue_round(st, st["round"], note=text)
            add_event(st, action, by=by, text=text)
            return {"launch": True}
        if action == "edit_requirement":
            k = kind or "change"
            if k not in CHANGE_KINDS:
                raise DiscussError(NOT_NOW)
            if not text:
                raise DiscussError("请写下要改的需求或约束")
            question = None
            if k == "answer":
                question = next((e for e in rec.open_questions(st) if e["id"] == ref), None)
                if question is None:
                    raise DiscussError("这个问题已经不在了")
            if len(st["changes"]) >= MAX_CHANGES or \
                    sum(len(c["text"]) for c in st["changes"]) + len(text) > MAX_CHANGES_CHARS:
                raise DiscussError("需求变更已经太多了（最多 20 条、共 12000 字）：请批准、停止或开一场新讨论")
            cid = f"C{max_change(st) + 1}"
            change = {"id": cid, "kind": k, "text": text, "by": by, "at": time.time()}
            if question is not None:
                change["ref"] = question["id"]
            st["changes"].append(change)
            if question is not None:
                entry_text = f"回答「{question['text'][:200]}」：{text}"
            else:
                entry_text = text
            rec.add_entry(st, "requirement_changes", "owner_decision", entry_text, by=by, ref=cid, change_kind=k)
            if question is not None:
                question["answered_by"] = cid
            add_event(st, action, by=by, text=text, kind=k, ref=cid)
            return {"launch": False}
        if action == "reject_plan":
            if st["queue"] or st["running"]:
                raise DiscussError(NOT_NOW)
            cur = current_plan(st)
            if cur is None:
                raise DiscussError("还没有方案")
            if not text:
                raise DiscussError("请写下否掉的原因")
            if len(st["rejected"]) >= MAX_REJECTED:
                raise DiscussError("已经否掉 10 个方案了：请批准、停止或开一场新讨论")
            cur["status"] = "rejected"
            rid = f"R{len(st['rejected']) + 1}"
            st["rejected"].append({"id": rid, "plan_v": cur["v"], "summary": cur["summary"], "steps": list(cur["steps"]),
                                   "reason": text, "before_step": st["next_step"], "by": by, "at": time.time()})
            rec.add_entry(st, "rejected_plans", "owner_decision", f"否掉方案 v{cur['v']}：{cur['summary'][:300]}；原因：{text}",
                          by=by, ref=rid)
            add_event(st, action, by=by, text=text, ref=rid)
            return {"launch": False}
        # approve
        if current_plan(st) is None:
            raise DiscussError("还没有可以批准的方案")
        rec.approve_state(st, by=by, note=text)
        add_event(st, action, by=by, text=text)
        return {"launch": False}

    return store.update(did, fn)


def record_event(store: Store, did: str, action: str, by: str = "", text: str = "") -> None:
    """A plain event (pause / resume / stop) from the web."""
    store.update(did, lambda st: add_event(st, action, by=by, text=text))


# -- engine -------------------------------------------------------------------------------------------------------
class Engine:
    def __init__(self, store: Store, did: str, call: Callable, say=None, stop: threading.Event | None = None,
                 timeout_s: float = DEFAULT_TIMEOUT_S, on_event=None):
        self.store, self.did, self.call = store, did, call
        self._say_fn = say
        self.stop = stop if stop is not None else threading.Event()
        self.timeout_s = float(timeout_s)
        self._on_event = on_event
        self._pids: dict[str, int] = {}

    def say(self, text: str) -> None:
        try:
            if self._say_fn:
                self._say_fn(text)
        except Exception:  # noqa: BLE001
            pass

    def emit(self, kind: str, **data) -> None:
        try:
            if self._on_event:
                self._on_event(kind, **data)
        except Exception:  # noqa: BLE001
            pass

    def run(self) -> str:
        """Runs queued groups until the queue is empty: "waiting" | "approved". Raises Interrupted or StepFailed."""
        def clear(st: dict) -> None:
            st["running"] = []
            st["last_error"] = None

        self.store.update(self.did, clear)
        while True:
            state = self.store.load(self.did)
            g = pending_group(state)
            if g is not None:
                self._finalize(g)
                continue
            if not state["queue"]:
                return "approved" if state.get("brief") else "waiting"
            g = state["queue"][0]["group"]
            steps = [s for s in state["queue"] if s["group"] == g]
            if self.stop.is_set():
                raise Interrupted()
            self._run_group(state, g, steps)
            self._finalize(g)

    # -- one group ------------------------------------------------------------------------------
    def _run_group(self, state: dict, g: int, steps: list[dict]) -> None:
        tdir = self.store.path(self.did) / "turns"
        tdir.mkdir(exist_ok=True)
        prepared = []
        for step in steps:
            prompt, meta = build_prompt(state, step)
            base = turn_base(step)
            for suffix in (".prompt.md", ".raw.txt", ".jsonl", ".times", ".stderr.log"):
                for name in (base + suffix, base + ".repair" + suffix):
                    (tdir / name).unlink(missing_ok=True)
            (tdir / f"{base}.prompt.md").write_text(prompt, encoding="utf-8")
            prepared.append((step, prompt, meta))

        def mark(st: dict) -> None:
            now = time.time()
            ids = {s["id"] for s in steps}
            st["running"] = [r for r in st["running"] if r["step"] not in ids]
            st["running"] += [{"step": s["id"], "side": s["side"], "kind": s["step"], "started_at": now, "pid": None}
                              for s in steps]

        self.store.update(self.did, mark)
        results: list[Any] = []
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self._guarded_step, state, step, prompt, meta) for step, prompt, meta in prepared]
            results = [f.result() for f in futures]
        if self.stop.is_set() or any(r == "interrupted" for r in results):
            self.say(f"group {g}: interrupted")
            raise Interrupted()
        failures = [r for r in results if isinstance(r, StepFailed)]
        if failures:
            f = failures[0]
            err = {"step": f.step, "side": f.side, "status": f.status, "error": str(f.error)[:500],
                   "resets_at": f.resets_at, "at": time.time()}

            def fail(st: dict) -> None:
                st["last_error"] = err

            self.store.update(self.did, fail)
            raise f

    def _finalize(self, g: int) -> None:
        box: dict[str, Any] = {}

        def fn(st: dict) -> None:
            box["stage"] = finalize_group(st, g)

        self.store.update(self.did, fn)
        stage = box.get("stage")
        if stage:
            self.say(f"stage {stage['n']}: {stage.get('decision')} (next: {stage['next_step']['action']})")
            self.emit("stage", n=stage["n"])

    def _guarded_step(self, state: dict, step: dict, prompt: str, meta: dict) -> Any:
        try:
            result = self._step(state, step, prompt, meta)
            if result == "interrupted":
                self._clear_running(step["id"])
                self.say(f"{step['id']} {step['side']} {step['step']}: interrupted")
            return result
        except StepFailed as e:
            self._clear_running(step["id"])
            self.say(f"{step['id']} {step['side']} {step['step']}: {e.status}")
            return e
        except Exception as e:  # noqa: BLE001  (a broken step must not take the group down without a record)
            self._clear_running(step["id"])
            self.say(f"{step['id']} {step['side']} {step['step']}: crashed: {type(e).__name__}: {e}")
            return StepFailed(step["side"], step["id"], "crashed", f"{type(e).__name__}: {e}"[:500])

    def _clear_running(self, sid: str) -> None:
        try:
            self.store.update(self.did, lambda st: st.__setitem__(
                "running", [r for r in st["running"] if r["step"] != sid]))
        except Exception as e:  # noqa: BLE001
            self.say(f"{sid}: could not clear the running entry: {e}")

    def _on_start(self, step: dict) -> Callable[[int], None]:
        sid = step["id"]

        def on_start(pid: int) -> None:
            try:
                self._pids[sid] = pid
                self.emit("step_started", step=sid, side=step["side"], pid=pid)

                def write() -> None:
                    def fn(st: dict) -> None:
                        for r in st["running"]:
                            if r["step"] == sid:
                                r["pid"] = pid
                    try:
                        self.store.update(self.did, fn, lock_timeout=2.0)
                    except Exception as e:  # noqa: BLE001
                        self.say(f"{sid}: pid {pid} not recorded: {e}")

                threading.Thread(target=write, daemon=True).start()
            except Exception:  # noqa: BLE001  (never raise: run_streaming would orphan the CLI)
                pass

        return on_start

    def _invoke(self, state: dict, step: dict, prompt: str, events_path: Path, timeout_s: float):
        """One CLI call; None when it was interrupted. Raises StepFailed for a failed call."""
        from . import discuss_call

        side, sid = step["side"], step["id"]
        try:
            folder, is_temp = discuss_call.call_folder(state, self.store)
        except StepFailed as e:
            raise StepFailed(side, sid, e.status, e.error) from None
        try:
            cfg = state["sides"].get(side) or {}
            proj = state.get("project")
            spec = discuss_call.CallSpec(did=self.did, step_id=sid, side=side, prompt=prompt, cwd=folder,
                                         events_path=events_path, timeout_s=timeout_s, model=cfg.get("model", ""),
                                         effort=cfg.get("effort", ""),
                                         project=Path(proj["path"]) if proj else None)
            try:
                res = self.call(spec, self._on_start(step))
            except StepFailed:
                raise
            except Exception as e:  # noqa: BLE001
                raise StepFailed(side, sid, "crashed", str(e)[:500]) from e
        finally:
            if is_temp:
                import shutil

                shutil.rmtree(folder, ignore_errors=True)
        if res.status == "interrupted":
            return None
        if res.status != "completed":
            raise StepFailed(side, sid, res.status, (res.error or "")[:500], res.resets_at)
        return res

    def _step(self, state: dict, step: dict, prompt: str, meta: dict) -> str:
        side, sid = step["side"], step["id"]
        base = turn_base(step)
        tdir = self.store.path(self.did) / "turns"
        started = time.time()
        self.say(f"{sid} {side} {step['step']}: started")
        first = self._invoke(state, step, prompt, tdir / f"{base}.jsonl", self.timeout_s)
        if first is None:
            return "interrupted"
        (tdir / f"{base}.raw.txt").write_text(first.text or "", encoding="utf-8")
        results = [first]
        obj = parse_turn(first.text)
        output, warns = normalize(obj) if obj is not None else (None, [])
        repaired, repair_raw = False, ""
        if not (output and output["plan"]["summary"]):
            rp = repair_prompt(state, step, first.text)
            (tdir / f"{base}.repair.prompt.md").write_text(rp, encoding="utf-8")
            second = self._invoke(state, step, rp, tdir / f"{base}.repair.jsonl",
                                  min(float(REPAIR_TIMEOUT_S), self.timeout_s))
            if second is None:
                return "interrupted"
            (tdir / f"{base}.repair.raw.txt").write_text(second.text or "", encoding="utf-8")
            repair_raw = f"turns/{base}.repair.raw.txt"
            results.append(second)
            obj = parse_turn(second.text)
            output, warns = normalize(obj) if obj is not None else (None, [])
            if output and output["plan"]["summary"]:
                repaired = True
            else:
                output, warns = None, []
        ok = output is not None
        warnings = [dict(w, side=side) for w in meta.get("warnings") or []] + [dict(w, side=side) for w in warns]
        if repaired:
            warnings.append({"code": "repaired", "side": side})
        if not ok:
            warnings.append({"code": "parse_failed", "side": side})
        tool_calls: list[str] = []
        for r in results:
            tool_calls += [t for t in (r.tool_calls or []) if t not in tool_calls]
        costs = [r.est_cost_usd for r in results if r.est_cost_usd and r.est_cost_usd > 0]
        quota = next((r.quota for r in reversed(results) if r.quota), None)
        out = output if ok else copy.deepcopy(EMPTY_OUTPUT)
        ended = time.time()
        turn = {"id": sid, "group": step["group"], "round": step["round"], "step": step["step"], "side": side,
                "status": "ok" if ok else "parse_failed", "started_at": started, "ended_at": ended,
                "seconds": round(ended - started, 2), "output": out, "raw_chars": len(first.text or ""),
                "repaired": repaired, "words": output_words(out) if ok else 0, "over_limit": False,
                "changes_seen": meta.get("changes_seen", 0), "prompt_tokens": meta.get("prompt_tokens", 0),
                "tool_calls": tool_calls, "warnings": warnings,
                "usage": {"calls": len(results), "tokens_in": sum(int(r.tokens_in or 0) for r in results),
                          "tokens_out": sum(int(r.tokens_out or 0) for r in results),
                          "tokens_cached": sum(int(r.tokens_cached or 0) for r in results),
                          "est_cost_usd": round(sum(costs), 6) if costs and side == "claude" else None,
                          "quota": quota},
                "files": {"prompt": f"turns/{base}.prompt.md", "raw": f"turns/{base}.raw.txt",
                          "events": f"turns/{base}.jsonl", "repair_raw": repair_raw},
                "writes_plan": bool(step.get("writes_plan")), "stage_end": bool(step.get("stage_end")),
                "stage_kind": step.get("stage_kind") or "round", "note": step.get("note") or ""}
        project_path = (state.get("project") or {}).get("path")

        def save(st: dict) -> None:
            from . import discuss_record as rec

            st["turns"] = [t for t in st["turns"] if t["id"] != sid]
            st["turns"].append(turn)
            st["turns"].sort(key=lambda t: (t["group"], step_num(t["id"])))
            st["queue"] = [q for q in st["queue"] if q["id"] != sid]
            st["running"] = [r for r in st["running"] if r["step"] != sid]
            st["calls"] += len(results)
            turn_checks(st, turn)
            rec.turn_entries(st, turn, project_path)

        self.store.update(self.did, save)
        self.say(f"{sid} {side} {step['step']}: {turn['status']} ({turn['seconds']} s, {len(results)} call(s))")
        self.emit("turn", turn_id=sid, side=side, step=step["step"], status=turn["status"])
        return "ok"


from . import discuss_call, discuss_record  # noqa: E402,F401  (they import this module)
