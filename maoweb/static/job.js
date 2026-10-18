"use strict";
// One job (#projects/<id>/<jobId>): where it is (the stage bar), what happens right now, the conversation between
// the agents and Crossview, and the cards where the user decides. projects.js hands over the element and calls
// window.MAOJob.open / close; app.js's poll timer calls MAOJob.poll.
// Uses app.js helpers: api, esc, inline, markdown, toast, money, tokens, schedulePoll, refreshBadge, state.

window.MAOJob = (() => {
  const ACTIVE = ["queued", "planning", "running", "pausing"], FINAL = ["done", "cancelled"];
  const COLORS = ["anthropic", "openai", "deepseek", "google", "alibaba", "other", "mao", "user"];
  const MAO = { pool: "", name: "Crossview", vendor: "mao", initials: "C", color: "mao" };
  const svg = (d, extra = "") => `<svg viewBox="0 0 20 20" aria-hidden="true"${extra}>${d}</svg>`;
  const ICON = {
    back: svg('<path d="M12.5 4.5L7 10l5.5 5.5"/>'), next: svg('<path d="M7.5 4.5L13 10l-5.5 5.5"/>'),
    down: svg('<path d="M6 8l4 4 4-4"/>'), pause: svg('<path d="M7.5 5v10M12.5 5v10"/>'),
    play: svg('<path d="M7 5l8 5-8 5z"/>'), ok: svg('<path d="M5 10.5l3.2 3.2L15 7"/>'),
    no: svg('<path d="M6 6l8 8M14 6l-8 8"/>'), bang: svg('<path d="M10 5v6M10 14.5h.01"/>'),
    read: svg('<path d="M2.5 10s2.8-5 7.5-5 7.5 5 7.5 5-2.8 5-7.5 5-7.5-5-7.5-5z"/><circle cx="10" cy="10" r="2.2"/>'),
    list: svg('<path d="M3 6.5A1.5 1.5 0 0 1 4.5 5H8l1.5 2h6A1.5 1.5 0 0 1 17 8.5v6a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 3 14.5z"/>'),
    search: svg('<circle cx="9" cy="9" r="5"/><path d="M13 13l4 4"/>'),
    edit: svg('<path d="M4 16l1-4 8-8 3 3-8 8z"/><path d="M11.5 5.5l3 3"/>'),
    write: svg('<path d="M6 3h5.5L15 6.5V16a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/><path d="M10 9v5M7.5 11.5h5"/>'),
    delete: svg('<path d="M4.5 6h11M8 6V4.5h4V6M6 6l.7 10h6.6L14 6"/>'),
    run: svg('<rect x="3" y="4" width="14" height="12" rx="2"/><path d="M6.5 8.5l2 1.5-2 1.5M10.5 12h3"/>'),
    web: svg('<circle cx="10" cy="10" r="7"/><path d="M3 10h14M10 3c2 2.2 2 11.8 0 14M10 3c-2 2.2-2 11.8 0 14"/>'),
    todo: svg('<path d="M8 6h8M8 10h8M8 14h8M4 6h.01M4 10h.01M4 14h.01"/>'),
    agent: svg('<circle cx="10" cy="7" r="2.5"/><path d="M5 16c.5-3 2.5-4.5 5-4.5s4.5 1.5 5 4.5"/>'),
    tool: svg('<circle cx="10" cy="10" r="2.6"/><path d="M10 3v3M10 14v3M3 10h3M14 10h3"/>'),
    error: svg('<circle cx="10" cy="10" r="7"/><path d="M10 6.5v4M10 13.5h.01"/>'),
    limit: svg('<circle cx="10" cy="10" r="7"/><path d="M10 6v4l2.5 2"/>'),
    info: svg('<circle cx="10" cy="10" r="7"/><path d="M10 9v5M10 6.5h.01"/>'),
  };
  // activity rows: what an agent did (VERB) or is doing (NOW_VERB)
  const VERB = { read: "看", list: "列出", search: "找", edit: "改", write: "写", delete: "删", run: "运行", web: "查资料",
    todo: "列计划", agent: "派帮手", tool: "用工具", error: "出错", limit: "额度用完" };
  const NOW_VERB = { read: "正在看", list: "正在看目录", search: "正在找", edit: "正在改", write: "正在写", delete: "正在删",
    run: "正在运行", web: "正在查资料", todo: "正在列计划", agent: "正在派帮手", tool: "正在用工具" };
  const NOW_ROLE = { plan: "在看项目、出方案", build: "正在写代码", fix: "正在修改", check: "正在检查改动", conflict: "正在解决合并冲突" };
  const ROLE_TAG = { plan: "出方案", build: "写代码", fix: "修改中", check: "检查", conflict: "解决冲突" };
  const ITEM_ROLE = { build: "写代码中", fix: "修改中", check: "检查中", conflict: "解决冲突中" };
  const TEAM_ROLE = { plan: "出方案", build: "写代码", check: "检查" };
  const TEAM_STATE = { done: "做完了", working: "工作中", waiting: "等着上场", idle: "空闲", stopped: "停下来了" };
  const DECISION = { approve: "同意了，照这个做", replan: "我要改方案", merge: "合并", revise: "再改改", discard: "这一项不要了",
    retry: "再试一次", accept: "照样合并", cancel: "不做了", pause: "暂停了", resume: "继续做" };
  const ACT_LABEL = { merge: "合并", revise: "再改改", discard: "不要了", retry: "再试一次", accept: "照样合并" };
  const QUEUED = { merge: "等这一步做完就合并", revise: "等这一步做完就去改", discard: "等这一步做完就不要它",
    retry: "等这一步做完就再试一次", accept: "等这一步做完就照样合并" };
  const DONE_TOAST = { approve: "好的，开始动手", replan: "已经发给它们，会重新出方案", merge: "好的，正在合并", revise: "已经发给它们",
    discard: "这一项不要了", retry: "好的，再试一次", accept: "好的，照样合并", cancel: "这件事不做了", pause: "已暂停",
    resume: "好的，接着做", stop_mode: "改好了，下一步开始时生效" };
  const NOTE = { review_skipped: "这次没有别家的 AI 检查，只跑了验收命令。", blocked: "这一项卡住了，停了下来。",
    failed: "这一项没做成。", interrupted: "Crossview 重启过，这一步停在了一半。", merge_failed: "合并时出了问题，已经撤销。",
    escalated: "换了另一个 AI 接着做。", cancelled: "这一项不做了。", plan_failed: "商量方案没有成功。",
    orch_error: "Crossview 内部出了点问题。", action_failed: "你的操作没能执行。", dropped: "Crossview 重启过，之前排着的操作没有执行。" };
  const RUN_END = { task_failed: "它没能做完这一轮。", rate_limited: "它的额度用完了，这一轮停了下来。",
    auth_error: "它没有登录（或者 key 不对），这一轮没能开始。", timeout: "做得太久了，Crossview 让它停了下来。",
    crashed: "它意外退出了。", unavailable: "这台电脑上找不到它。", interrupted: "这一轮被打断了。" };
  const RESULT = { merged: "已合并", discarded: "放弃了", blocked: "卡住了", failed: "没做成", waiting: "等你决定",
    running: "正在做", queued: "排队中" };
  const REASON_TITLE = { review_rejected: "检查一直没通过，停下来了", agent_blocked: "它有个问题要问你",
    no_pool: "现在没有能用的 AI", max_runs: "试了好几次还没做成", too_many_runs: "试了好几次还没做成",
    budget: "花费到上限了", no_route: "没有安排谁来做这类事", orch_error: "Crossview 出了点问题", other: "停下来了，要你看一下" };
  const REASON_TEXT = {
    review_rejected: "检查的 AI 改了几轮还是不同意。可以写一句意见让它再改，也可以照样合并或者不要这一项。",
    agent_blocked: "它停下来等你回答。点「回答它」写下你的回答，它会接着做。",
    no_pool: "能做这件事的 AI 现在都用不了（可能额度用完了，或者没登录）。过一会儿再试一次。",
    max_runs: "做了好几轮都没有通过验收。可以写一句意见再改，或者不要这一项。",
    too_many_runs: "做了好几轮都没有通过验收。可以写一句意见再改，或者不要这一项。",
    budget: "这一项花的钱到了设定的上限。", no_route: "项目设置里没有安排谁来做这类事。",
    orch_error: "Crossview 在安排这一项时出了错。可以再试一次。", other: "这一项停了下来。" };
  const STOP_TITLE = { plan_failed: "商量没成功", paused: "已经暂停", interrupted: "Crossview 重启过，这件事停在了一半",
    failed: "出了问题，停下来了", stalled: "剩下的几项做不了了", missing: "找不到项目文件夹了" };
  // a stopped card: what happened, then what the buttons on it can do (built from its actions, never more)
  const STOP_TEXT = { plan_failed: "出方案的 AI 没能给出能用的方案。", paused: "它们停在了这里。", interrupted: "",
    failed: "Crossview 在做这件事时出了错。", stalled: "剩下的项要用到前面没做成（或者不要了）的那一项，做不了了。",
    missing: "项目文件夹被移走或者删掉了，这件事没法接着做。" };
  const STOP_NEXT = { both: "可以接着做，或者重新商量。", resume: "点「继续做」接着来，或者不做了。", replan: "可以重新商量，或者不做了。",
    none: "没有可以接着做的了。点「不做了」结束这件事。" };
  const MERGE_PROBLEM = { conflict: "它的改动和项目里别的改动冲突了，合并不进去。点「让它修一下」，它会把冲突解决掉。",
    tests_failed: "合并到一起以后，验收没通过。点「让它修一下」，它会接着修好。",
    error: "合并的时候出错了。可以再合并一次，或者让它修一下。" };
  const COMPOSE = {
    replan: ["比如：测试也要包括负数", "它会带着你的意见重新出方案"],
    revise: ["比如：出错时的提示写清楚一点", "它会照你的意见改，改完再检查一遍"],
    answer: ["写下你的回答", "它会带着你的回答接着做"],
    restart: ["可以补充一句意见，也可以不写", "它会重新出方案"] };
  const CONFIRM = { discard: "不要这一项了？它做的改动不会合并进项目。",
    cancel: "这件事不做了？正在做的会停下来，还没合并的改动都不要了。已经合并的会留着。",
    pause: "暂停？正在做的这一步会停下来，点「继续」后接着做。" };

  let S = null;   // the open job: { root, pid, jid, el, data, sig, open, drafts, compose, runs, ... }

  // ---- small pieces ------------------------------------------------------------------------------
  const sep = '<span class="sep" aria-hidden="true">&nbsp;· </span>';   // a line never starts with the dot
  const txt = (s) => esc(String(s == null ? "" : s));
  const me = () => (state.user && (state.user.display || state.user.name)) || "";
  const who = (a) => `<span class="who" data-no-tr>${txt(a ? a.name : "AI")}</span>`;
  const isLive = (d) => !!d && (ACTIVE.includes(d.job.status) || !!d.job.active || !!(d.now && d.now.live));
  function avatar(a, cls = "") {
    a = a || { name: "AI", color: "other" };
    const c = COLORS.includes(a.color) ? a.color : "other";
    const inner = c === "mao" ? '<span class="mark"></span>' : txt(a.initials || (a.name || "?").slice(0, 2));
    return `<span class="av av-${c}${cls ? " " + cls : ""}" aria-hidden="true">${inner}</span>`;
  }
  // the person who decides: 你, or the owner's first letter when it is someone else's job
  const ownerMark = (j) => (j.mine ? "<span>你</span>" : `<span data-no-tr>${txt((j.owner || "?").slice(0, 1))}</span>`);
  const userAv = (j, cls = "") => `<span class="av av-user${cls ? " " + cls : ""}" aria-hidden="true">${ownerMark(j)}</span>`;
  const pad = (n) => String(n).padStart(2, "0");
  function hm(ts) { const d = new Date(ts * 1000); return `${d.getHours()}:${pad(d.getMinutes())}`; }
  function when(ts) {
    if (!ts) return "";
    const d = new Date(ts * 1000);
    return d.toDateString() === new Date().toDateString() ? hm(ts) : `${d.getMonth() + 1} 月 ${d.getDate()} 日 ${hm(ts)}`;
  }
  function clock(sec) {
    sec = Math.max(0, Math.floor(sec));
    const h = Math.floor(sec / 3600), m = Math.floor(sec / 60) % 60, s = sec % 60;
    return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
  }
  function dur(sec) {
    const m = Math.round(Math.max(0, sec) / 60);
    return m < 1 ? "不到 1 分钟" : m < 60 ? `${m} 分钟` : `${Math.floor(m / 60)} 小时 ${m % 60} 分钟`;
  }
  const pm = (f) => (f.binary ? '<span class="jv-pm">二进制</span>'
    : `<span class="jv-pm"><span class="add">+${f.added || 0}</span> <span class="del">−${f.removed || 0}</span></span>`);
  // raw orch / agent text: never the main line, only folded away under 详细记录
  const raw = (text, k) => (text ? `<details class="raw jv-rawd" data-k="${txt(k)}"${S.open.has(k) ? " open" : ""}><summary><span>详细记录</span>${ICON.down}</summary><pre data-no-tr>${txt(text)}</pre></details>` : "");
  const btn = (cls, attrs, label) => `<button type="button" class="jv-btn ${cls}" ${attrs}>${label}</button>`;
  const taskOf = (d, id) => (d.tasks || []).find((t) => t.id === id) || null;
  const billingOf = (d, a) => { const u = ((d.usage || {}).by_agent || []).find((x) => a && x.agent && x.agent.pool === a.pool); return u ? u.billing : ""; };

  // ---- the head: back, pause / resume, drop ------------------------------------------------------
  function stopCard(d) { return (d.checkpoints || []).find((c) => c.kind === "stopped") || null; }
  function headHtml(d) {
    const j = d.job, st = j.status;
    const back = `<a class="jv-back" href="#projects/${S.pid}">${ICON.back}${j.project ? `<span data-no-tr>${txt(j.project)}</span>` : "<span>项目</span>"}</a>`;
    let b = "";
    if (j.can_act && !FINAL.includes(st)) {
      const sc = stopCard(d);
      if (j.cancelling) b += `<button type="button" class="jv-hbtn" disabled>正在停下来…</button>`;   // 不做了 is on its way
      else if (st === "pausing") b += `<button type="button" class="jv-hbtn" disabled>正在暂停…</button>`;
      else if (ACTIVE.includes(st)) b += `<button type="button" class="jv-hbtn" data-act="pause">${ICON.pause}<span>暂停</span></button>`;
      else if (sc && sc.actions.includes("resume")) b += `<button type="button" class="jv-hbtn" data-act="resume">${ICON.play}<span>继续</span></button>`;
      if (!j.cancelling) b += `<button type="button" class="jv-hbtn danger" data-act="cancel">不做了</button>`;
    }
    return `${back}<span class="spacer"></span>${b}`;
  }

  // ---- the stage bar: 商量 -(你)- 动手 - 检查 -(你)- 合并 -------------------------------------------
  function stagesHtml(d) {
    const st = d.stage || {}, j = d.job, pg = st.plan_gate || {}, mg = st.merge_gate || {};
    const stopped = st.key === "stopped";
    let cur = stopped ? st.step || (pg.state === "done" ? "build" : "plan") : st.key;
    const POS = { plan: 0, plan_gate: 1, build: 2, check: 3, merge_gate: 4, done: 6 };
    let pos = POS[cur] !== undefined ? POS[cur] : 0;
    if (cur === "merge_gate" && mg.state === "done") pos = 5;   // the user said merge: Crossview merges now
    if (cur === "plan" && pg.state === "waiting") pos = 1;
    const live = isLive(d) && !stopped;
    const item = st.item, plan = (d.sections || []).find((s) => s.key === "plan");
    const planner = plan && plan.summary && plan.summary.planner;
    const checker = (d.team || []).find((m) => m.role === "check");
    const builder = (d.team || []).find((m) => m.role === "build");
    const step = (p, n, label, sub) => {
      const s = pos > p ? "done" : pos === p ? (stopped || st.key === "done" ? "stop" : live ? "cur" : "here") : "todo";
      const dot = s === "done" ? ICON.ok : s === "cur" ? '<span class="jv-dot"></span>' : s === "stop" ? (j.status === "cancelled" ? ICON.no : ICON.pause) : String(n);
      return `<div class="jv-step ${s}"${s === "cur" || s === "here" || s === "stop" ? ' aria-current="step"' : ""}><span class="jv-node">${dot}</span><span class="jv-sl">${label}</span>${sub ? `<span class="jv-ss">${sub}</span>` : ""}</div>`;
    };
    const gate = (g, p, doneLabel, todoLabel) => {
      const s = g.state === "waiting" ? "wait" : g.state === "done" || pos > p ? "done" : "todo";
      const label = s === "wait" ? (j.mine ? "等你" : "等着") : s === "done" && g.at ? doneLabel(when(g.at)) : s === "todo" && !FINAL.includes(j.status) ? todoLabel : "";
      return `<div class="jv-gate ${s}"><div class="jv-gl"><span class="jv-line${pos >= p ? " on" : ""}"></span><span class="jv-you">${ownerMark(j)}</span>
        <span class="jv-line${pos > p ? " on" : ""}"></span></div>${label ? `<span class="jv-gs">${label}</span>` : ""}</div>`;
    };
    const line = (p) => `<div class="jv-link"><span class="jv-line${pos > p ? " on" : ""}"></span></div>`;
    const name = (m) => (m && m.agent ? `<span data-no-tr>${txt(m.agent.name)}</span>` : "");
    return step(0, 1, "商量", planner ? `<span data-no-tr>${txt(planner.name)}</span>` : "") +
      gate(pg, 1, (t) => `${t} 同意`, "") +
      step(2, 2, "动手", item && item.total ? `第 ${item.index} / ${item.total} 项` : name(builder)) +
      line(2) +
      step(3, 3, "检查", name(checker)) +
      gate(mg, 4, (t) => `${t} 合并`, j.stop_mode === "plan" ? "自动合并" : "合并前给你看") +
      step(5, 4, "合并", "");
  }

  // ---- title and the line under it ---------------------------------------------------------------
  const STEP_TEXT = { plan: "第 1 步：商量方案", plan_gate: "第 1 步：商量方案", build: "第 2 步：动手", check: "第 3 步：检查",
    merge_gate: "第 4 步：合并" };
  function titleHtml(d) {
    const j = d.job, st = d.stage || {};
    const parts = [];
    if (j.created_at) parts.push(`${when(j.created_at)} 开始`);
    if (j.status === "done") parts.push("全部做完了");
    else if (j.status === "cancelled") parts.push("已经不做了");
    else if (j.cancelling) parts.push("正在停下来");
    else {
      const k = st.key === "stopped" ? st.step : st.key;
      if (STEP_TEXT[k]) parts.push(STEP_TEXT[k]);
      if (st.item && st.item.total) parts.push(`第 ${st.item.index} / ${st.item.total} 项`);
      if (j.status === "paused" || j.status === "pausing") parts.push("已暂停");
      else if (st.key === "stopped") parts.push("停下来了");
    }
    const cur = st.item && (d.tasks || []).find((t) => t.index === st.item.index);
    return `<h1 class="jv-h1" data-no-tr>${txt(j.title || j.goal)}</h1>
      <p class="jv-sub"><span>${parts.join(" · ")}</span>${cur && st.item.total > 1 && !FINAL.includes(j.status) ? `${sep}<span class="jv-subt" data-no-tr>${txt(cur.title)}</span>` : ""}
      ${j.mine ? "" : `${sep}<span>交代的人：${txt(j.owner)}</span>`}</p>`;
  }

  // ---- 现在: what happens right now ----------------------------------------------------------------
  function nowLine(n, d) {
    const a = n.agent || MAO;
    if (a.vendor === "mao") {
      if (n.kind === "cancel") return "<span>正在停下来…</span>";
      if (n.kind === "verify") return "<span>Crossview 正在验收：运行测试</span>";
      if (n.kind === "merge") return "<span>Crossview 正在合并</span>";
      if (n.role === "plan" || !(d.tasks || []).length) return "<span>正在准备商量方案</span>";
      const t = taskOf(d, n.task_id);   // the item is done and the work stops in a moment: no AI is awaited
      return t && t.status === "verified" ? "<span>Crossview 在收尾</span>" : "<span>等下一个 AI 空出来</span>";
    }
    if (NOW_VERB[n.kind] && n.target && n.role !== "plan")
      return `${who(a)}<span> ${NOW_VERB[n.kind]} </span><code class="jv-tgt">${txt(n.target)}</code>`;
    return `${who(a)}<span> ${NOW_ROLE[n.role] || "正在工作"}</span>`;
  }
  function quietHead(d) {   // nobody works: what the job waits for
    const j = d.job;
    return j.status === "done" ? "全部做完了" : j.status === "cancelled" ? "已经不做了"
      : (d.checkpoints || []).some((c) => c.kind !== "stopped") ? (j.mine ? "等你决定" : `等 ${txt(j.owner)} 决定`)
      : j.status === "paused" ? "已经暂停" : "停下来了";
  }
  // the line a screen reader says when it changes: who works now (not each file), or what the job waits for
  function liveHtml(d) {
    const n = d.now, a = n && (n.agent || MAO);
    if (!n) return `<span>现在：</span><span>${quietHead(d)}</span>`;
    return `<span>现在：</span>${a.vendor === "mao" ? nowLine(n, d) : `${who(a)}<span> ${NOW_ROLE[n.role] || "正在工作"}</span>`}`;
  }
  function nowHtml(d, side) {
    const n = d.now, j = d.job;
    if (!n) {
      if (!side) return (d.checkpoints || []).length && !FINAL.includes(j.status)
        ? `<button type="button" class="jv-jump" data-a="jump">${userAv(j, "sm")}<span>${j.mine ? "等你决定" : `等 ${txt(j.owner)} 决定`}</span>${ICON.down}</button>` : "";
      return `<section class="jv-now quiet" aria-label="现在"><span class="jv-now-k">现在</span><strong>${quietHead(d)}</strong></section>`;
    }
    const a = n.agent || MAO, mao = a.vendor === "mao";
    const t = taskOf(d, n.task_id);
    const meta = [];
    if (mao && n.kind === "verify" && n.target) meta.push(`<span>命令：</span><code>${txt(n.target)}</code>`);
    if (n.tokens) meta.push(`<span>${n.role === "plan" ? "已经用了" : "这一项已经用了"} ${tokens(n.tokens)} token</span>`);
    const bill = mao ? "" : billingOf(d, a);
    if (bill === "subscription") meta.push("<span>订阅，不另外花钱</span>");
    else if (bill === "api") meta.push("<span>按量计费</span>");
    return `<section class="jv-now${n.live ? " live" : ""}" aria-label="现在">
      <div class="jv-now-row">${avatar(a, "md" + (n.live ? " pulse" : ""))}
        <div class="jv-now-text"><span class="jv-now-k">现在${t && (d.tasks || []).length > 1 ? ` · 第 ${t.index} 项` : ""}</span><strong>${nowLine(n, d)}</strong></div>
        ${n.since && n.live ? `<span class="jv-elapsed" data-since="${n.since}" aria-hidden="true"></span>` : ""}</div>
      ${meta.length ? `<div class="jv-now-meta">${meta.join(sep)}</div>` : ""}</section>`;
  }

  // ---- the items of the plan, with their state -----------------------------------------------------
  function itemState(t, d) {
    const n = t.live, cps = d.checkpoints || [];
    if (n) {
      const a = n.agent || MAO;
      const label = a.vendor === "mao" ? { verify: "验收中", merge: "合并中" }[n.kind] || "等 AI 空出来" : ITEM_ROLE[n.role] || "写代码中";
      return ["spin", `<span>${label}</span>${a.vendor === "mao" ? "" : sep + who(a)}`];
    }
    if (t.status === "merged") return ["ok", "<span>已合并</span>"];
    if (t.status === "cancelled") return ["no", "<span>放弃了</span>"];
    if (t.status === "blocked" || t.status === "failed") return ["bad", `<span>${d.job.mine ? "要你看一下" : "停下来了"}</span>`];
    if (t.status === "verified") {
      const c = cps.find((x) => x.kind === "merge" && x.task_id === t.id);
      if (c && c.queued) return ["wait", `<span>${ACT_LABEL[c.queued] ? "已记下：" + ACT_LABEL[c.queued] : "等你决定"}</span>`];
      return ["wait", `<span>${c ? (d.job.mine ? "等你决定" : `等 ${txt(d.job.owner)} 决定`) : "做好了，等合并"}</span>`];
    }
    const dep = (t.depends_on || []).map((id) => taskOf(d, id)).find((x) => x && x.status !== "merged");
    if (t.status === "running") return isLive(d) ? ["spin", "<span>正在做</span>"] : ["pause", "<span>停在了一半</span>"];
    return ["num", `<span>${dep ? `等第 ${dep.index} 项合并` : "排队中"}</span>`];
  }
  function itemsHtml(d, side) {
    let rows;
    const tasks = d.tasks || [];
    if (tasks.length) {
      rows = tasks.map((t) => {
        const [ic, label] = itemState(t, d);
        const icon = ic === "spin" ? '<span class="jv-spin"></span>' : ic === "ok" ? ICON.ok : ic === "no" ? ICON.no : ic === "bad" ? ICON.bang : ic === "pause" ? ICON.pause : `<span>${t.index}</span>`;
        const n = t.live, a = n && n.agent;
        const liveLine = d.job.stop_mode === "plan" && n && a && a.vendor !== "mao" && NOW_VERB[n.kind] && n.target
          ? `<span class="jv-it-live">${who(a)}<span> ${NOW_VERB[n.kind]} </span><code>${txt(n.target)}</code></span>` : "";
        return `<div class="jv-it ${ic}"><span class="jv-it-ic">${icon}</span><span class="jv-it-main">
          <span class="jv-it-t"><span>${t.index}. </span><span data-no-tr>${txt(t.title)}</span></span>${liveLine}</span>
          <span class="jv-it-s">${label}</span></div>`;
      }).join("");
    } else if (side && d.plan && (d.plan.tasks || []).length) {
      rows = d.plan.tasks.map((t, i) => `<div class="jv-it num"><span class="jv-it-ic"><span>${i + 1}</span></span>
        <span class="jv-it-main"><span class="jv-it-t" data-no-tr>${txt(t.title)}</span></span></div>`).join("");
    } else if (side) rows = `<p class="jv-muted">${FINAL.includes(d.job.status) ? "没有做出计划。" : "还在商量，计划出来后会列在这里。"}</p>`;
    else return "";
    return `<section class="jv-items${side ? " side" : ""}" aria-label="这次要做的">${side ? "<h2>计划</h2>" : ""}${rows}</section>`;
  }

  // ---- the conversation ---------------------------------------------------------------------------
  function actRow(it, live) {
    const k = it.kind, going = live && it.ok === null && k !== "error" && k !== "limit";
    const brief = it.task_file && ["read", "edit", "write"].includes(k);   // Crossview's own notes under .task/: no path
    const verb = !brief ? VERB[k] || "用工具" : k === "read" ? "看任务说明"
      : /PROGRESS\.md$/i.test(it.target || "") ? "记下进度" : /REVIEW\.md$/i.test(it.target || "") ? "写检查意见" : "写任务笔记";
    let meta = "";
    if (going) meta = '<span class="jv-am going">进行中…</span>';
    else if ((k === "edit" || k === "write") && !brief && (it.added != null || it.removed != null)) meta = `<span class="jv-am">${pm(it)}</span>`;
    else if (k === "run" && it.exit_code != null && it.exit_code !== 0) meta = `<span class="jv-am">退出码 ${it.exit_code}</span>`;
    const body = k === "error" || k === "limit" ? (it.detail ? `<span class="jv-tgt plain" data-no-tr>${txt(it.detail)}</span>` : "")
      : !brief && it.target ? `<code class="jv-tgt">${txt(it.target)}</code>` : "";
    return `<div class="jv-act${going ? " going" : ""}${it.sub ? " sub" : ""}${k === "error" ? " err" : ""}">
      ${going ? '<span class="jv-spin sm"></span>' : ICON[k] || ICON.tool}<span class="jv-verb">${verb}</span>${body}${meta}</div>`;
  }
  // the agent's rows: its short messages as lines, everything else grouped in light boxes
  function actsHtml(items, e) {
    const out = [];
    let box = [];
    const flush = () => { if (box.length) { out.push(`<div class="jv-acts">${box.join("")}</div>`); box = []; } };
    for (const it of items) {
      if (it.kind === "say") {
        if ((it.detail || "").trim() === (e.text || "").trim()) continue;
        flush(); out.push(`<div class="jv-say" data-no-tr>${inline(esc(it.detail || ""))}</div>`);
      } else box.push(actRow(it, e.live));
    }
    flush();
    return out.join("");
  }
  // the rows of a run: the view sends the newest ones (e.hidden more before them); a loaded record fills in the rest
  function runItems(e) {
    const id = e.run_id, det = S.runs.get(id), shown = e.activity || [];
    if (det && det.items) {
      const first = shown.length ? shown[0].seq : Infinity;
      const before = det.items.filter((x) => x.seq < first);
      // loaded for this view already, or nothing missing: show what there is
      if (before.length >= (e.hidden || 0) || det._sig === S.sig) return { items: before.concat(shown), older: 0 };
      const s = S;   // the run grew past the rows loaded earlier: load it again (once per view)
      setTimeout(() => { if (S === s) loadRun(id); }, 0);
    }
    return { items: shown, older: e.hidden || 0, loading: det === "loading" || S.fetching.has(id) };
  }
  function issuesHtml(issues) {
    if (!issues || !issues.length) return "";
    return `<ul class="jv-issues">${issues.map((i) => {
      const big = i.severity === "blocker" || i.severity === "major";
      return `<li><span class="jv-chip ${big ? "big" : "small"}">${big ? "要改" : "小问题"}</span>
        <span class="jv-issue" data-no-tr>${i.file ? `<code>${txt(i.file)}</code> ` : ""}${inline(esc(i.detail || ""))}</span></li>`;
    }).join("")}</ul>`;
  }
  function runHtml(e, d) {
    const a = e.agent || { name: "AI", color: "other" };
    const v = e.verdict;
    const { items, older, loading } = runItems(e);
    const pass = v && v.result === "approve" ? '<span class="jv-pass">通过</span>' : "";
    let body = "";
    if (older) body += `<button type="button" class="jv-older" data-a="older" data-run="${txt(e.run_id)}">${loading ? "正在加载…" : `看前面的 ${older} 步`}</button>`;
    else if (loading) body += '<p class="jv-muted">正在加载…</p>';
    body += actsHtml(items, e);
    if (e.text) body += `<div class="jv-text" data-no-tr>${markdown(e.text)}</div>`;
    if (v) {
      body += issuesHtml(v.issues);
      if (v.result === "unreadable") body += `<p class="jv-muted">没读懂它的检查结果。</p>`;
    }
    if (e.live && !items.length && !e.text) body += `<p class="jv-muted">${e.role === "check" ? "正在看改动…" : "开始了…"}</p>`;
    if (e.error) body += `<div class="jv-runerr">${ICON.info}<span>${RUN_END[e.status] || "这一轮没有正常结束。"}</span></div>${raw(e.error, `re:${e.run_id}`)}`;
    const rk = `raw:${e.run_id}`, rawOpen = S.open.has(rk);
    const det = S.runs.get(e.run_id);
    return `<div class="jv-msg" data-key="run:${txt(e.run_id)}">${avatar(a, "md")}
      <div class="jv-bubble${e.live ? " live" : ""}"><div class="jv-bh"><strong data-no-tr>${txt(a.name)}</strong>
        ${ROLE_TAG[e.role] ? `<span class="jv-tag c-${COLORS.includes(a.color) ? a.color : "other"}">${ROLE_TAG[e.role]}</span>` : ""}${pass}
        <span class="jv-time">${e.at ? when(e.at) : ""}</span></div>
        ${body}
        <details class="jv-raw" data-k="${rk}"${rawOpen ? " open" : ""}><summary>看原始记录（命令和输出）${ICON.down}</summary>
          <div class="jv-raw-body">${rawOpen ? rawHtml(det) : ""}</div></details></div></div>`;
  }
  function rawHtml(det) {
    if (!det || det === "loading") return '<p class="jv-muted">正在加载…</p>';
    if (det.err) return `<p class="jv-muted">${txt(det.err)}</p>`;
    const rows = (det.items || []).filter((it) => it.kind !== "say").map((it) => {
      const head = it.kind === "run" ? `<code>$ ${txt(it.target)}</code>${it.exit_code != null ? `<span class="jv-am">退出码 ${it.exit_code}</span>` : ""}`
        : `<span class="jv-verb">${VERB[it.kind] || "用工具"}</span><code>${txt(it.target || it.detail || "")}</code>`;
      return `<div class="jv-ri"><div class="jv-rih">${head}</div>${it.output ? `<pre>${txt(it.output)}</pre>` : ""}</div>`;
    }).join("");
    const block = (label, text) => (text ? `<div class="jv-ri"><div class="jv-rih"><span class="jv-verb">${label}</span></div><pre>${txt(text)}</pre></div>` : "");
    const all = rows + block("最后的回复", det.summary) + block("出错信息", det.error) + block("Crossview 验收的输出", det.verify_log) +
      block("检查报告", det.review_md);
    return all || '<p class="jv-muted">没有记录。</p>';
  }
  function entryHtml(e, d, sec) {
    if (e.type === "run") return runHtml(e, d);
    if (e.type === "ask") {
      const mine = d.job.mine;
      const long = (e.text || "").length > 400;
      return `<div class="jv-me" data-key="ask"><div class="jv-mb">
        <div class="jv-mt" data-no-tr>${txt(long ? e.text.slice(0, 400) + "…" : e.text)}</div>
        ${long ? `<details class="raw jv-rawd" data-k="ask"${S.open.has("ask") ? " open" : ""}><summary><span>看全文</span>${ICON.down}</summary><pre data-no-tr>${txt(e.text)}</pre></details>` : ""}
        <div class="jv-mf">${mine ? "<span>你</span>" : `<span data-no-tr>${txt(e.by)}</span>`}${e.at ? `${sep}<span>${when(e.at)}</span>` : ""}${e.with_chat ? `${sep}<span>带上了聊天里的讨论</span>` : ""}</div></div></div>`;
    }
    if (e.type === "verify") {
      const t = e.tests, cls = e.live ? "going" : e.ok ? "ok" : "bad";
      const res = e.live ? '<span class="jv-spin sm"></span><span>进行中…</span>'
        : e.ok ? `<span>${t && t.total ? `${t.passed} 个通过` : "通过"}</span>` : t && t.failed ? `<span>${t.failed} 个没通过</span>` : "<span>没通过</span>";
      return `<div class="jv-verify ${cls}" data-key="v:${txt(e.run_id)}"><span class="mark"></span>
        <span class="jv-vt"><span>Crossview 自己又跑了一遍验收</span>${(e.commands || []).length ? `<code>${txt(e.commands.join(" ; "))}</code>` : ""}</span>
        <span class="jv-vr">${res}</span></div>`;
    }
    if (e.type === "decision") {
      const mine = e.by === me();
      const label = e.action === "stop_mode" ? (e.text === "plan" ? "改成：只在开始前看计划" : "改成：每做完一步都停") : DECISION[e.action] || "做了决定";
      const t = e.task_id && taskOf(d, e.task_id);
      return `<div class="jv-me" data-key="d:${e.at}:${e.action}"><div class="jv-mb small"><div class="jv-mk"><span>${label}</span>
          ${t && sec && sec.key !== e.task_id ? `${sep}<span>第 ${t.index} 项</span>` : ""}</div>
        ${e.text && e.action !== "stop_mode" ? `<div class="jv-mt" data-no-tr>${txt(e.text)}</div>` : ""}
        <div class="jv-mf">${mine ? "<span>你</span>" : `<span data-no-tr>${txt(e.by)}</span>`}${e.at ? `${sep}<span>${when(e.at)}</span>` : ""}</div></div></div>`;
    }
    if (e.type === "merged") {
      return `<div class="jv-verify ok" data-key="m:${e.at}"><span class="mark"></span><span class="jv-vt"><span>Crossview 合并好了</span>
        ${e.sha ? `<code>${txt(String(e.sha).slice(0, 7))}</code>` : ""}</span><span class="jv-vr"><span>${e.at ? when(e.at) : ""}</span></span></div>`;
    }
    if (e.type === "note") {
      return `<div class="jv-note ${txt(e.level || "info")}" data-key="n:${e.at}:${txt(e.code)}">${e.level === "info" ? ICON.info : ICON.error}
        <div class="jv-note-b"><span>${NOTE[e.code] || "有一条记录。"}</span>${raw(e.text, `n:${e.at}:${e.code}`)}</div></div>`;
    }
    return "";
  }
  function sumParts(s, d) {   // [the line that names the part, the parts of the line with what happened]
    const sm = s.summary || {}, parts = [];
    if (s.key === "plan") {
      if (sm.planner) parts.push(`${who(sm.planner)}<span> 出了方案</span>`);
      if (sm.rounds > 1) parts.push(`<span>商量了 ${sm.rounds} 轮</span>`);
      if (sm.approved_at) {
        const ok = (s.entries || []).filter((e) => e.type === "decision" && e.action === "approve").pop();
        const mine = !ok || ok.by === me();
        parts.push(mine ? `<span>你 ${when(sm.approved_at)} 同意了</span>` : `<span data-no-tr>${txt(ok.by)}</span><span> ${when(sm.approved_at)} 同意了</span>`);
      } else if (s.state === "stopped") parts.push("<span>没有商量完</span>");
      return ["<span>商量方案</span>", parts];
    }
    if (sm.agent) parts.push(`${who(sm.agent)}<span> 写的</span>`);
    if (sm.files) parts.push(`<span>改了 ${sm.files} 个文件</span>`);
    if (sm.tests) parts.push(sm.tests.ok ? `<span>${sm.tests.total ? `验收 ${sm.tests.passed} 个通过` : "验收通过"}</span>` : "<span>验收没通过</span>");
    if (sm.review_rounds) parts.push(`<span>检查了 ${sm.review_rounds} 轮</span>`);
    if (RESULT[sm.result]) parts.push(`<span>${sm.result === "running" && !isLive(d) ? "停在了一半" : RESULT[sm.result]}</span>`);
    return [`<span>第 ${s.index} 项</span>${sep}<span class="jv-st" data-no-tr>${txt(s.title)}</span>`, parts];
  }
  function sectionHtml(s, d, current) {
    const body = (s.entries || []).map((e) => entryHtml(e, d, s)).join("");
    if (current) {
      const sm = s.summary || {};
      const live = s.state === "live" && isLive(d);
      const lead = s.key === "plan" ? `${sm.planner ? avatar(sm.planner, "sm") : ""}<span>商量方案</span>`
        : `${sm.agent ? avatar(sm.agent, "sm") : ""}<span>第 ${s.index} 项</span><span class="jv-st" data-no-tr>${txt(s.title)}</span>`;
      return `<section class="jv-sec" data-key="sec:${txt(s.key)}"><div class="jv-sec-h">${lead}<span class="jv-rule"></span>
        ${live ? '<span class="jv-livetag"><span class="jv-ld"></span>实时</span>' : ""}</div>${body}</section>`;
    }
    const k = `sec:${s.key}`, open = S.open.has(k);
    const icon = s.state === "done" ? ((s.summary || {}).result === "discarded" ? ICON.no : ICON.ok) : ICON.pause;
    const [head, meta] = sumParts(s, d);
    return `<details class="jv-done ${s.state}" data-k="${txt(k)}" data-key="${txt(k)}"${open ? " open" : ""}>
      <summary class="jv-sum">${icon}<span class="jv-sum-t"><span class="jv-sum-h">${head}</span>${meta.length ? `<span class="jv-sum-m">${meta.map((m) => `<span>${m}</span>`).join(sep)}</span>` : ""}</span>
        <span class="jv-more"><span class="o">展开</span><span class="c">收起</span></span></summary>
      ${open ? `<div class="jv-sec-body">${body}</div>` : ""}</details>`;
  }
  function currentKeys(d) {
    const secs = d.sections || [];
    const fin = FINAL.includes(d.job.status);   // while it can go on, where it stopped stays open too
    const cur = new Set(secs.filter((s) => s.state === "live" || s.state === "waiting" || (s.state === "stopped" && !fin)).map((s) => s.key));
    if (!cur.size && secs.length && !fin) cur.add(secs[secs.length - 1].key);
    return cur;
  }
  function flowHtml(d) {
    const secs = d.sections || [];
    if (!secs.length) return d.job.error ? `<div class="notice">读不到这件事的全部记录。${raw(d.job.error, "je")}</div>` : "";
    const cur = currentKeys(d);
    const done = secs.filter((s) => !cur.has(s.key)), open = secs.filter((s) => cur.has(s.key));
    return (done.length ? `<div class="jv-dones">${done.map((s) => sectionHtml(s, d, false)).join("")}</div>` : "") +
      open.map((s) => sectionHtml(s, d, true)).join("");
  }

  // ---- the cards where the user decides ------------------------------------------------------------
  const composing = (...keys) => !!S.compose && keys.includes(S.compose.key);   // this card's own note box is open: its buttons wait
  // does the new view still have the card the open note box belongs to? (someone else may have decided meanwhile)
  function composeFits(c, d) {
    const cps = (d.checkpoints || []).filter((x) => (x.actions || []).includes(c.action));
    if (!d.job.can_act || FINAL.includes(d.job.status)) return false;
    if (c.kind === "replan") return cps.some((x) => x.kind === "plan");
    if (c.kind === "restart") return cps.some((x) => x.kind === "stopped");
    return cps.some((x) => (x.kind === "merge" || x.kind === "attention") && x.task_id === c.task && !x.queued);
  }
  function composeHtml(c, kind, task, extra = "") {
    const key = `${kind}:${task || "job"}`;
    if (!S.compose || S.compose.key !== key) return "";
    const [ph, hint] = COMPOSE[kind];
    return `<div class="jv-compose"><textarea data-k="${txt(key)}" rows="3" maxlength="4000" placeholder="${ph}" aria-label="写给它们的话">${txt(S.drafts.get(key) || "")}</textarea>
      <div class="jv-row">${btn("primary", `data-a="send"`, "发给它们")}${btn("ghost", `data-a="uncompose"`, "取消")}</div>
      <span class="jv-hint">${hint}</span>${extra}</div>`;
  }
  function cardHead(d, sub) {
    const j = d.job;
    return `<div class="jv-ch">${userAv(j, "md")}<span class="jv-ck">${j.mine ? "等你决定" : `等 ${txt(j.owner)} 决定`}</span>
      ${sub ? `<span class="jv-cs">${sub}</span>` : ""}</div>`;
  }
  const stillNote = (d) => (isLive(d) ? "其他的还在做" : "它们已经停下来了");
  function queuedLine(d, action) {
    if (!action) return "";
    return isLive(d) ? `<p class="jv-queued"><span class="jv-spin sm"></span><span>${QUEUED[action] || "等这一步做完就执行"}</span></p>`
      : `<p class="jv-queued">${ICON.info}<span>继续后会执行：</span><span>${ACT_LABEL[action] || ""}</span></p>`;
  }
  function planCard(c, d) {
    const p = d.plan || { tasks: [] }, can = d.job.can_act;
    const items = (p.tasks || []).map((t, i) => {
      const subs = [];
      if ((t.scope || []).length) subs.push(`<span>会改 </span>${t.scope.slice(0, 3).map((f) => `<code>${txt(f)}</code>`).join(" ")}${t.scope.length > 3 ? "<span> …</span>" : ""}`);
      if (t.agent) subs.push(`${who(t.agent)}<span> 来写</span>`);
      if (t.reviewer) subs.push(`${who(t.reviewer)}<span> 来检查</span>`);
      const dep = (t.depends_on || []).map((k) => (p.tasks || []).findIndex((x) => x.key === k || (x.task_id && x.task_id === k))).filter((x) => x >= 0);
      if (dep.length) subs.push(`<span>第 ${dep[0] + 1} 项合并后再做</span>`);
      return `<li><span class="jv-num">${i + 1}</span><span class="jv-pi"><strong data-no-tr>${txt(t.title)}</strong>
        ${subs.length ? `<span class="jv-pis">${subs.join(sep)}</span>` : ""}</span></li>`;
    }).join("");
    const verify = [...new Set((p.tasks || []).flatMap((t) => t.verify || []))];
    const warn = (p.warnings || []).length ? `<div class="jv-warn">${ICON.info}<div><span>出方案的 AI 提醒：</span>
      <ul data-no-tr>${p.warnings.map((w) => `<li>${txt(w)}</li>`).join("")}</ul></div></div>` : "";
    const acts = can && !composing("replan:job") ? `<div class="jv-row">${btn("primary", 'data-act="approve"', "照这个做")}
      ${btn("ghost", 'data-a="compose" data-kind="replan"', "我要改")}${btn("text danger", 'data-act="cancel"', "不做了")}</div>
      <span class="jv-hint">点「我要改」写一句意见，它会带着你的意见重新出方案。</span>` : "";
    return `<section class="jv-card" aria-label="等你决定" data-key="cp:plan">${cardHead(d, "它们已经停下来了")}
      <h2>${`商量好了：分 ${c.count || (p.tasks || []).length} 项做`}</h2>
      <ol class="jv-plan">${items}</ol>
      ${verify.length ? `<p class="jv-note2"><span>怎么算做完：运行</span> ${verify.map((v) => `<code class="jv-cmd">${txt(v)}</code>`).join(" ")}<span>，全部通过。</span></p>` : ""}
      ${warn}${acts}${can ? composeHtml(c, "replan", "") : ""}</section>`;
  }
  function filesBox(c) {
    const files = c.files || [], pv = c.preview;
    if (!files.length) return '<p class="jv-muted">没有改动文件。</p>';
    const main = pv ? files.find((f) => f.path === pv.path) || { path: pv.path } : files[0];
    const others = files.filter((f) => f !== main);
    const lines = pv ? pv.lines.map((l) => `<span class="${l[0] === "+" ? "a" : l[0] === "-" ? "d" : ""}">${txt(l)}</span>`).join("") : "";
    return `<div class="jv-diffbox"><div class="jv-dh"><code>${txt(main.path)}</code>${main.added != null || main.binary ? pm(main) : ""}
        ${btn("link", `data-a="diff" data-task="${txt(c.task_id)}"`, `看全部改动${ICON.next}`)}</div>
      ${lines ? `<pre class="jv-pv">${lines}</pre>` : ""}
      ${others.length ? `<div class="jv-more-files"><span>还改了：</span>${others.map((f) => `<span class="jv-mf1"><code>${txt(f.path)}</code>${pm(f)}</span>`).join("")}</div>` : ""}</div>`;
  }
  function costParts(c) {
    const b = c.billing_note || {}, out = [];
    const list = (names) => txt(names.join(I18N.lang === "en" ? ", " : "、"));   // the names are not translated
    if ((b.api || []).length) out.push(`<span data-no-tr>${list(b.api)}</span> <span>${money(c.cost_usd || 0)}</span>`);
    if ((b.subscription || []).length) out.push(`<span data-no-tr>${list(b.subscription)}</span><span> 用订阅</span>`);
    return out.length ? out.join(sep) : "<span>没有花钱</span>";
  }
  function mergeCard(c, d) {
    const can = d.job.can_act, prob = c.merge_problem || "";
    const t = c.tests, rv = c.review || {};
    let h2, body = "", acts = "";
    if (prob) {
      h2 = "合并时出了问题，已经撤销";
      body = `<p class="jv-p">${MERGE_PROBLEM[prob] || MERGE_PROBLEM.error}</p>${raw([c.merge_detail, c.merge_note].filter(Boolean).join("\n\n"), `cm:${c.task_id}`)}`;
      if (can && !c.queued && !composing(`revise:${c.task_id}`)) acts = `<div class="jv-row">${btn("primary", `data-act="revise" data-task="${txt(c.task_id)}" data-empty="1"`, "让它修一下")}
        ${(c.actions || []).includes("merge") ? btn("ghost", `data-act="merge" data-task="${txt(c.task_id)}"`, "再合并一次") : ""}
        ${btn("text danger", `data-act="discard" data-task="${txt(c.task_id)}"`, "不要了")}</div>`;
    } else {
      h2 = rv.result === "approve" ? `第 ${c.index} 项做好了，检查也通过了` : `第 ${c.index} 项做好了，验收通过了`;
      if (can && !c.queued && !composing(`revise:${c.task_id}`)) acts = `<div class="jv-row">${btn("primary", `data-act="merge" data-task="${txt(c.task_id)}"`, c.next ? "合并，做下一项" : "合并")}
        ${btn("ghost", `data-a="compose" data-kind="revise" data-task="${txt(c.task_id)}"`, "再改改")}
        ${btn("text danger", `data-act="discard" data-task="${txt(c.task_id)}"`, "不要了")}</div>
        <span class="jv-hint">点「再改改」写一句意见，它改完会再检查一遍。</span>`;
    }
    const testsText = t ? (t.failed ? `<span>${t.passed} 个通过，${t.failed} 个没通过</span>` : t.total ? `<span>${t.passed} 个全部通过</span>` : "<span>通过</span>")
      : "<span>验收通过</span>";   // a merge card is only for work that passed acceptance
    const review = rv.result === "approve" ? `${rv.agent ? who(rv.agent) : ""}<span> 检查通过了</span>${rv.minor ? `${sep}<span>还有 ${rv.minor} 个小问题，不影响合并</span>` : ""}`
      : "<span>这次没有别家的 AI 检查</span>";
    const next = c.next ? `<span>合并后接着做第 ${c.next.index} 项：</span><span class="jv-st" data-no-tr>${txt(c.next.title)}</span>${d.job.stop_mode === "plan" ? "" : "<span>，做完再停下来给你看。</span>"}`
      : "<span>这是最后一项，合并后就全部做完了。</span>";
    return `<section class="jv-card${prob ? " problem" : ""}" aria-label="等你决定" data-key="cp:m:${txt(c.task_id)}">${cardHead(d, stillNote(d))}
      <span class="jv-kick"><span>第 ${c.index} / ${c.total} 项</span>${sep}<span data-no-tr>${txt(c.title)}</span></span>
      <h2>${h2}</h2>${body}${filesBox(c)}
      <dl class="jv-dl"><dt>测试结果</dt><dd>${testsText}</dd><dt>检查结果</dt><dd>${review}</dd>
        ${c.minutes ? `<dt>用时</dt><dd><span>${dur(c.minutes * 60)}</span></dd>` : ""}<dt>花费</dt><dd>${costParts(c)}</dd></dl>
      ${prob ? "" : `<p class="jv-p small">${next}</p>`}
      ${queuedLine(d, c.queued)}${acts}${can ? composeHtml(c, "revise", c.task_id) : ""}</section>`;
  }
  function attentionCard(c, d) {
    const can = d.job.can_act, ask = c.reason === "agent_blocked", acts = c.actions || [];
    const id = `data-task="${txt(c.task_id)}"`;
    let b = "";
    if (can && !c.queued && !composing(`revise:${c.task_id}`, `answer:${c.task_id}`)) {
      const list = [];
      if (acts.includes("revise")) list.push(btn(ask ? "primary" : "ghost", `data-a="compose" data-kind="${ask ? "answer" : "revise"}" ${id}`, ask ? "回答它" : "再改改"));
      if (acts.includes("retry")) list.push(btn(ask ? "ghost" : "primary", `data-act="retry" ${id}`, "再试一次"));
      if (acts.includes("accept")) list.push(btn("ghost", `data-act="accept" ${id}`, "照样合并"));
      if (acts.includes("discard")) list.push(btn("text danger", `data-act="discard" ${id}`, "不要了"));
      if (!ask) list.sort((x, y) => y.includes("primary") - x.includes("primary"));
      b = `<div class="jv-row">${list.join("")}</div>`;
    }
    const q = ask && c.question ? `<div class="jv-q"><span class="jv-qk">它问：</span><p data-no-tr>${txt(c.question)}</p></div>` : "";
    return `<section class="jv-card warn" aria-label="等你决定" data-key="cp:a:${txt(c.task_id)}">${cardHead(d, stillNote(d))}
      <span class="jv-kick"><span>第 ${c.index} / ${c.total} 项</span>${sep}<span data-no-tr>${txt(c.title)}</span></span>
      <h2>${REASON_TITLE[c.reason] || REASON_TITLE.other}</h2>${q}<p class="jv-p">${REASON_TEXT[c.reason] || REASON_TEXT.other}</p>
      ${issuesHtml(c.issues)}${ask ? "" : raw(c.note, `ca:${c.task_id}`)}${queuedLine(d, c.queued)}${b}
      ${can ? composeHtml(c, ask ? "answer" : "revise", c.task_id) : ""}</section>`;
  }
  function stoppedCard(c, d) {
    const can = d.job.can_act, acts = c.actions || [];
    let b = "";
    if (can && !composing("restart:job")) {
      const list = [];
      if (acts.includes("resume")) list.push(btn("primary", 'data-act="resume"', "继续做"));
      if (acts.includes("replan")) list.push(btn(acts.includes("resume") ? "ghost" : "primary", 'data-a="compose" data-kind="restart"', "重新商量"));
      list.push(btn("text danger", 'data-act="cancel"', "不做了"));   // always there on a stopped job
      b = `<div class="jv-row">${list.join("")}</div>`;
    }
    const go = acts.includes("resume"), re = acts.includes("replan");
    const next = can ? STOP_NEXT[go && re ? "both" : go ? "resume" : re ? "replan" : "none"] : "";   // no buttons, no advice
    const text = [STOP_TEXT[c.reason], next].filter(Boolean).map((t) => `<span>${t}</span>`).join(I18N.lang === "en" ? " " : "");
    const pend = (d.job.pending || []).filter((p) => ACT_LABEL[p.action]).map((p) => {
      const t = taskOf(d, p.task_id);
      return `<li><span>${ACT_LABEL[p.action]}</span>${t ? `${sep}<span>第 ${t.index} 项</span>` : ""}</li>`;
    }).join("");
    return `<section class="jv-card${c.reason === "paused" ? "" : " warn"}" aria-label="等你决定" data-key="cp:stop">${cardHead(d, "")}
      <h2>${STOP_TITLE[c.reason] || "停下来了"}</h2>${text ? `<p class="jv-p">${text}</p>` : ""}
      ${pend ? `<div class="jv-pend"><span>继续后会执行：</span><ul>${pend}</ul></div>` : ""}
      ${raw(c.error, "cs")}${b}${can ? composeHtml(c, "restart", "") : ""}</section>`;
  }
  // ---- 放进我的项目文件夹 (the end card here, the project page too) ---------------------------------------
  // merged work sits on orch's integration branch until the user clicks; l is the server's landing state
  // (d.result.land, the project page's land), LAND what this page shows meanwhile
  const LAND = new Map();   // project id -> { open, at, busy, err, sha, note }
  const CHANGE = { new: "新文件", changed: "会更新", deleted: "会删掉" };   // what happens to each file on 确定
  const ASK_SHOWN = 8, SETTLE_MS = 600;   // files in the confirm list (the new ones are cut first); 确定 waits that long
  // the small line for people who know git: which branch goes where, and the command that does the same
  const gitLine = (branch, into) => `<p class="jv-land-git"><span>分支：</span><code>${txt(branch)}</code>${into ? ` → <code>${txt(into)}</code>` : ""}${sep}<span>命令：</span><code>git merge ${txt(branch)}</code></p>`;
  function landHtml(pid, l, where) {
    if (!l) return "";
    const u = LAND.get(pid) || {}, n = l.file_count || 0, job = where === "job";
    const done = (t) => `<div class="jv-landed">${ICON.ok}<span>${t}</span></div>`;
    const landed = (last) => (last.mine || !last.by ? `已经放进你的项目文件夹了（${Number(last.files) || 0} 个文件）`
      : `${txt(last.by)} 已经放进项目文件夹了（${Number(last.files) || 0} 个文件）`);   // someone else clicked it
    const note = u.note ? `<div class="jv-warn">${ICON.info}<span>${txt(u.note)}</span></div>` : "";
    if (job && l.last) {   // the landing that brought this job's work in; newer work is the project page's business
      const later = n ? `<p class="jv-land-later"><a href="#projects/${pid}">后来又有 ${n} 个文件的改动，到项目页放进去</a></p>` : "";
      return done(landed(l.last)) + note + later;
    }
    if (!n) {   // on the project page last is the newest landing
      if (l.text) return job ? `<div class="jv-warn">${ICON.info}<span>${txt(l.text)}</span></div>` : "";   // folder gone, cannot look
      if (l.last && Date.now() / 1000 - l.last.at < 600) return done(landed(l.last)) + note;
      return job && l.exists ? done("已经在你的项目文件夹里了") : "";
    }
    // a refusal stays until the next try or until there is other work to put in (it may repeat the state's reason)
    const err = u.err && u.sha === l.sha ? `<div class="jv-warn bad" role="alert">${ICON.error}<span>${txt(u.err)}</span></div>` : "";
    let body;
    if (l.running || l.reason === "unchecked") body = `<div class="jv-row">${btn("primary", "disabled", "放进我的项目文件夹")}</div><span class="jv-hint">${txt(l.text)}</span>`;
    else if (u.open || u.busy) {
      // files already in the folder (deleted, changed) are always listed; only new ones are cut short
      const files = l.files || [], mine = files.filter((f) => f.change !== "new"), fresh = files.filter((f) => f.change === "new");
      const shown = mine.concat(fresh.slice(0, Math.max(0, ASK_SHOWN - mine.length))), more = n - shown.length;
      const hiddenNew = l.counts ? l.counts.new - (shown.length - mine.length) : -1;
      const rows = shown.map((f) => `<li><span class="jv-chg ${CHANGE[f.change] ? f.change : "changed"}">${CHANGE[f.change] || CHANGE.changed}</span><code>${txt(f.path)}</code></li>`).join("");
      body = `<div class="jv-landask" tabindex="-1"><span class="jv-landk">你的项目文件夹里会有这些变化：</span><ul class="jv-landfiles">${rows}</ul>
        ${more > 0 ? `<span class="jv-landmore">${hiddenNew === more ? `还有 ${more} 个新文件` : `还有 ${more} 个`}</span>` : ""}
        <p class="jv-landwhere"><span>文件夹：</span><code>${txt(l.folder)}</code></p>
        ${l.text && !u.busy ? `<div class="jv-warn">${ICON.info}<span>${txt(l.text)}</span></div>` : ""}
        <div class="jv-row">${u.busy ? btn("primary", "disabled", "正在放…")
          : btn("primary", 'data-a="land" data-v="go"', "确定") + btn("ghost", 'data-a="land" data-v="no"', "先不放")}</div>
        <span class="jv-hint">${more > 0 ? "除了这次的改动，你自己的其他文件不会动。" : "除了上面这些，你自己的其他文件不会动。"}</span></div>`;
    } else body = `<div class="jv-row">${btn("primary", 'data-a="land" data-v="open"', "放进我的项目文件夹")}</div>`;
    return `<div class="jv-land"><p class="jv-landh">${ICON.list}<span>有 ${n} 个文件的改动还没放进你的项目文件夹</span></p>
      ${err}${body}${gitLine(l.branch, l.current)}</div>`;
  }
  // a click on one of its buttons; after 确定 resolves to the new landing state (null: refused), else undefined
  async function landClick(b, pid, l, draw) {
    const v = b.dataset.v, u = LAND.get(pid) || {}, box = b.closest("[data-r], [data-slot]");
    LAND.set(pid, u);
    if (u.busy) return undefined;
    const show = () => {   // the list gets the focus, not 确定: a second tap or Enter cannot land unread
      const ask = u.open && box && box.querySelector(".jv-landask");
      if (ask) { ask.scrollIntoView({ block: "nearest" }); ask.focus({ preventScroll: true }); }
    };
    if (v === "open" || v === "no") {
      u.open = v === "open"; u.at = Date.now(); u.err = ""; u.note = ""; draw(); show();
      return undefined;
    }
    if (v !== "go" || Date.now() - (u.at || 0) < SETTLE_MS) return undefined;   // the tap that opened it, landing twice
    u.busy = true; draw();
    let land = null;
    try {
      const r = await api("/api/projects/land", { method: "POST", body: { project_id: pid, sha: l ? l.sha : "" } });
      land = r.land || null; u.err = ""; u.note = r.note || ""; u.open = false;
    } catch (err) {
      const fresh = err.data && err.data.land;   // more work came in after the list was shown: show the new list
      u.err = err.message; u.sha = fresh ? fresh.sha : l ? l.sha : ""; u.open = !!fresh; u.at = Date.now();
      land = fresh || null;
    }
    u.busy = false;
    if (u.open) setTimeout(show, 0);
    return land;
  }
  function whereHtml(d) {
    const r = d.result || {};
    return r.land ? landHtml(S.pid, r.land, "job") : gitLine(r.branch || "orch/integration");
  }
  function endCard(d) {
    const j = d.job, tasks = d.tasks || [];
    const merged = tasks.filter((t) => t.status === "merged").length, dropped = tasks.filter((t) => t.status === "cancelled").length;
    if (j.status === "done") {
      return `<section class="jv-card end" data-key="cp:end"><div class="jv-ch"><span class="jv-endic">${ICON.ok}</span><h2>全部做完了</h2></div>
        <p class="jv-p">${[merged ? `<span>合并了 ${merged} 项</span>` : "", dropped ? `<span>${dropped} 项放弃了</span>` : ""].filter(Boolean).join(sep)}</p>
        ${merged ? whereHtml(d) : ""}
        <div class="jv-row"><a class="jv-btn ghost" href="#projects/${S.pid}">回到项目</a></div></section>`;
    }
    if (j.status === "cancelled") {
      return `<section class="jv-card end" data-key="cp:end"><div class="jv-ch"><span class="jv-endic no">${ICON.no}</span><h2>这件事不做了</h2></div>
        <p class="jv-p">${merged ? "已经合并的改动会留着，其他的都不要了。" : "什么都没有合并，项目没有变。"}</p>${merged ? whereHtml(d) : ""}
        <div class="jv-row"><a class="jv-btn ghost" href="#projects/${S.pid}">回到项目</a></div></section>`;
    }
    return "";
  }
  function cardsHtml(d) {
    const html = (d.checkpoints || []).map((c) => c.kind === "plan" ? planCard(c, d) : c.kind === "merge" ? mergeCard(c, d)
      : c.kind === "attention" ? attentionCard(c, d) : c.kind === "stopped" ? stoppedCard(c, d) : "").join("");
    return html + endCard(d);
  }

  // ---- 停下来的时机 (phone at the end, desktop in the side column) -----------------------------------------
  function stopRow(d) {
    const j = d.job;
    if (FINAL.includes(j.status)) return "";
    const label = j.stop_mode === "plan" ? "只在开始前看计划" : "每做完一步";
    const pick = S.stopPick && j.can_act ? `<div class="jv-stoppick">${[["each", "每做完一步都停（推荐）"], ["plan", "只在开始前看计划"]].map(([v, t]) =>
      btn(v === j.stop_mode ? "primary" : "ghost", `data-a="stopset" data-v="${v}"`, t)).join("")}<span class="jv-hint">下一步开始时生效</span></div>` : "";
    return `<div class="jv-stop"><span><span>停下来的时机：</span><span>${label}</span></span>${j.can_act ? `${sep}${btn("link", 'data-a="stoppick"', S.stopPick ? "收起" : "改一下")}` : ""}</div>${pick}`;
  }

  // ---- the right column on a wide screen -------------------------------------------------------------
  function sideHtml(d) {
    const dropped = d.job.status === "cancelled";   // dropped: done = its part was finished (a plan, merged items); the rest stopped
    const team = (d.team || []).map((m) => {
      const st = dropped && m.state === "idle" ? "stopped" : m.state;
      return `<div class="jv-tm">${avatar(m.agent, "sm")}<span class="jv-tmn"><span data-no-tr>${txt(m.agent && m.agent.name)}</span>${sep}<span>${TEAM_ROLE[m.role] || ""}</span></span>
      <span class="jv-tms ${txt(st)}">${TEAM_STATE[st] || ""}</span></div>`;
    }).join("");
    const files = (d.tasks || []).filter((t) => (t.files || []).length).map((t) => `<div class="jv-ft"><span>${t.index}.</span><span data-no-tr>${txt(t.title)}</span>
        ${btn("link", `data-a="diff" data-task="${txt(t.id)}"`, "看改动")}</div>
      ${t.files.map((f) => `<div class="jv-f"><code>${txt(f.path)}</code>${f.added != null || f.binary ? pm(f) : ""}</div>`).join("")}`).join("");
    const u = d.usage || {};
    const rows = new Map();   // one row per name and billing: a planner and a reviewer pool may both be "Claude"
    for (const x of u.by_agent || []) {
      const k = `${x.agent && x.agent.name}|${x.billing}`, r = rows.get(k);
      if (r) { r.tokens = (r.tokens || 0) + (x.tokens || 0); r.cost_usd = (r.cost_usd || 0) + (x.cost_usd || 0); }
      else rows.set(k, { ...x });
    }
    const by = [...rows.values()].map((x) => `<div class="jv-ua">${avatar(x.agent, "sm")}<span data-no-tr>${txt(x.agent && x.agent.name)}</span>
      <span class="jv-uv">${x.tokens ? `<span>用了 ${tokens(x.tokens)} token</span>${sep}` : ""}${x.billing === "api" ? `<span>${money(x.cost_usd || 0)}</span>` : "<span>订阅</span>"}</span></div>`).join("");
    return `${nowHtml(d, true)}
      ${team ? `<section class="jv-ss1"><h2>这次的团队</h2>${team}</section>` : ""}
      ${itemsHtml(d, true)}
      <section class="jv-ss1"><h2>改了哪些文件</h2>${files || '<p class="jv-muted">还没有改动。</p>'}</section>
      <section class="jv-ss1"><h2>用量</h2><span class="jv-ut"><span>${u.tokens ? `这次一共 ${tokens(u.tokens)} token` : "还没用 token"}</span>${u.cost_usd ? `${sep}<span>按量花了 ${money(u.cost_usd)}</span>` : ""}</span>${by}
        ${stopRow(d)}</section>`;
  }

  // ---- drawing: each region only when it changed ---------------------------------------------------
  const REGIONS = ["head", "title", "stages", "now", "items", "flow", "cards", "foot", "side", "live"];
  function skeleton(root) {
    root.innerHTML = `<div class="jv">
      <div class="jv-head" data-r="head"></div>
      <div class="jv-titlebox" data-r="title"></div>
      <nav class="jv-stages" aria-label="进度" data-r="stages"></nav>
      <div class="jv-cols"><div class="jv-main">
          <div class="jv-phone" data-r="now"></div><div class="jv-phone" data-r="items"></div>
          <div class="jv-flow" data-r="flow"></div><div class="jv-cards" data-r="cards"></div>
          <div class="jv-phone jv-foot" data-r="foot"></div></div>
        <aside class="jv-side" aria-label="这次任务的情况" data-r="side"></aside></div>
      <p class="sr-only" aria-live="polite" data-r="live"></p>
      <dialog class="jv-dlg" aria-label="改动"><div class="jv-dlg-h"><h2>改动</h2>
        <button type="button" class="icon-btn" data-a="dlgclose" aria-label="关闭">${ICON.no}</button></div><div class="jv-dlg-b"></div></dialog></div>`;
    const el = {};
    root.querySelectorAll("[data-r]").forEach((x) => (el[x.dataset.r] = x));
    return el;
  }
  function put(name, html) {
    const el = S.el[name];
    if (!el || el._src === html) return;
    const f = document.activeElement;
    if (f && f.tagName === "TEXTAREA" && el.contains(f)) { S.later.add(name); return; }   // typing: draw it afterwards
    S.later.delete(name);
    el._src = html; el.innerHTML = html;
  }
  function scroller() { return S.root.closest(".page") || document.scrollingElement; }
  function render() {
    const d = S.data;
    if (!d) return;
    const box = scroller(), top = box.getBoundingClientRect().top;
    const atEnd = box.scrollTop > 0 && box.scrollHeight - box.scrollTop - box.clientHeight < 40;
    let anchor = null, y = 0;
    for (const x of S.root.querySelectorAll("[data-key]")) {   // keep what the user looks at where it is
      const r = x.getBoundingClientRect();
      if (r.bottom > top + 1) { anchor = x.dataset.key; y = r.top; break; }
    }
    const html = { head: headHtml(d), title: titleHtml(d), stages: stagesHtml(d), now: nowHtml(d, false), items: itemsHtml(d, false),
      flow: flowHtml(d), cards: cardsHtml(d), foot: stopRow(d), side: sideHtml(d), live: liveHtml(d) };
    for (const r of REGIONS) put(r, html[r]);
    S.root.querySelector(".jv").dataset.status = d.job.status;
    if (atEnd) box.scrollTop = box.scrollHeight;
    else if (anchor) {
      const x = [...S.root.querySelectorAll("[data-key]")].find((n) => n.dataset.key === anchor);
      if (x) box.scrollTop += x.getBoundingClientRect().top - y;
    }
    tick();
  }
  function tick() {
    if (!S) return;
    const now = Date.now() / 1000;
    S.root.querySelectorAll("[data-since]").forEach((x) => { x.textContent = clock(now - Number(x.dataset.since)); });
  }

  // ---- loading and polling ---------------------------------------------------------------------------
  function failed(msg) {
    if (S.data) { if (!S.failing) toast(msg); S.failing = true; return; }   // keep the last view; say it once
    S.el.flow._src = null;
    S.el.head.innerHTML = `<a class="jv-back" href="#projects/${S.pid}">${ICON.back}<span>回到项目</span></a>`;
    S.el.flow.innerHTML = `<div class="notice">${txt(msg)}</div>`;
  }
  async function load(full) {
    const s = S;
    if (!s) return;
    let d;
    try { d = await api(`/api/job?id=${s.jid}${!full && s.sig ? `&sig=${encodeURIComponent(s.sig)}` : ""}`); }
    catch (err) {
      if (S !== s) return;
      failed(err.message);
      if (/没有这个任务/.test(err.message)) return;   // gone or not this user's: nothing to wait for
      return schedulePoll("projects", true, 10000);
    }
    if (S !== s) return;
    s.failing = false;
    if (!d.same && d.job) {
      if (s.compose && !composeFits(s.compose, d)) s.compose = null;   // its card is gone; the draft stays in s.drafts
      if (FINAL.includes(d.job.status) || !d.job.can_act) s.stopPick = false;
      s.data = d; s.sig = d.sig || ""; render(); refreshBadge();
      fetchRuns(d);
    }
    else if (s.later.size && !(document.activeElement && document.activeElement.tagName === "TEXTAREA")) render();
    schedulePoll("projects", true, isLive(s.data) ? 2500 : 10000);
  }
  async function loadRun(runId) {
    if (!S || S.fetching.has(runId)) return;
    const s = S, old = s.runs.get(runId), sig = s.sig;   // sig: the view this record is at least as new as
    s.fetching.add(runId);
    if (!old || old.err) { s.runs.set(runId, "loading"); render(); }   // a refresh keeps the rows already on screen
    let det;
    try { det = await api(`/api/job/run?id=${s.jid}&run=${encodeURIComponent(runId)}`); det._at = Date.now(); det._sig = sig; }
    catch (err) { det = old && old.items ? old : { err: err.message, items: null }; }
    s.fetching.delete(runId);
    s.runs.set(runId, det);
    if (S === s) render();
  }
  // after a new view: records to load without a tap, and records loaded while their run was going that are out of date
  function fetchRuns(d) {
    const cur = currentKeys(d);
    for (const sec of d.sections || []) {
      const shown = cur.has(sec.key) || S.open.has(`sec:${sec.key}`);
      for (const e of sec.entries || []) {
        if (e.type !== "run") continue;
        const id = e.run_id, det = S.runs.get(id), raw = S.open.has(`raw:${id}`);
        if (!det) {   // a stopped section kept open (paused, plan failed) gets no rows in the view: load them, as 展开 does
          if (cur.has(sec.key) && sec.state === "stopped" && e.hidden && !(e.activity || []).length) loadRun(id);
        } else if (det.live) {
          if (!e.live || det.status !== e.status) { if (shown || raw) loadRun(id); else S.runs.delete(id); }   // it ended
          else if (raw && Date.now() - (det._at || 0) > 10000) loadRun(id);   // an open raw log follows the run
        }
      }
    }
  }
  function sectionRuns(key) {
    const sec = ((S.data && S.data.sections) || []).find((s) => `sec:${s.key}` === key);
    return sec ? sec.entries.filter((e) => e.type === "run" && e.hidden && !(e.activity || []).length).map((e) => e.run_id) : [];
  }

  // ---- the diff dialog -----------------------------------------------------------------------------
  async function openDiff(taskId) {
    const dlg = S.root.querySelector(".jv-dlg"), b = dlg.querySelector(".jv-dlg-b"), s = S;
    const t = taskOf(S.data, taskId);
    dlg.querySelector("h2").innerHTML = t ? `<span>第 ${t.index} 项的改动</span>` : "<span>改动</span>";
    b.innerHTML = '<p class="jv-muted">正在加载…</p>';
    if (!dlg.open) dlg.showModal();
    let x;
    try { x = await api(`/api/job/diff?id=${s.jid}&task=${encodeURIComponent(taskId)}`); }
    catch (err) { if (S === s) b.innerHTML = `<p class="jv-muted">${txt(err.message)}</p>`; return; }
    if (S !== s) return;
    const files = (x.files || []).map((f) => `<div class="jv-f"><code>${txt(f.path)}</code>${pm(f)}</div>`).join("");
    const lines = (x.patch || "").split("\n").map((l) => {
      const c = l.startsWith("+++") || l.startsWith("---") || l.startsWith("diff ") || l.startsWith("index ") ? "h"
        : l.startsWith("@@") ? "at" : l[0] === "+" ? "a" : l[0] === "-" ? "d" : "";
      return `<span class="${c}">${txt(l) || " "}</span>`;
    }).join("");
    b.innerHTML = `${files ? `<div class="jv-dfiles">${files}</div>` : ""}
      ${x.truncated ? '<p class="jv-muted">改动太多，只显示了前面一部分。</p>' : ""}
      ${x.patch ? `<pre class="jv-patch">${lines}</pre>` : '<p class="jv-muted">没有改动。</p>'}`;
  }

  // ---- what the user does ------------------------------------------------------------------------
  async function act(action, taskId = "", text = "", fromNote = false) {
    if (!S || S.busy) return;
    if (CONFIRM[action] && !confirm(CONFIRM[action])) return;
    const s = S;
    s.busy = true;
    s.root.querySelectorAll("[data-act], [data-a='send']").forEach((b) => (b.disabled = true));
    try {
      const r = await api("/api/job/action", { method: "POST", body: { job_id: s.jid, action, task_id: taskId, text } });
      toast(r && r.queued ? "记下了，等这一步做完就执行" : DONE_TOAST[action] || "好的");
      if (fromNote && s.compose) { s.drafts.delete(s.compose.key); s.compose = null; }   // a button on another card keeps this note
      s.stopPick = false;
      refreshBadge();
    } catch (err) { toast(err.message); }
    finally { s.busy = false; s.root.querySelectorAll("[data-act], [data-a='send']").forEach((x) => (x.disabled = false)); }
    if (S === s) await load(true);
  }
  function onClick(e) {
    const b = e.target.closest("[data-act], [data-a]");
    if (!b || !S || !S.root.contains(b)) return;
    const a = b.dataset.a, task = b.dataset.task || "";
    if (b.dataset.act) {
      if (b.dataset.act === "revise" && b.dataset.empty) return act("revise", task, "");
      return act(b.dataset.act, task);
    }
    if (a === "compose") {
      const kind = b.dataset.kind;
      S.compose = { key: `${kind}:${task || "job"}`, kind, task, action: kind === "replan" || kind === "restart" ? "replan" : "revise" };
      render();
      const ta = S.root.querySelector(`textarea[data-k="${S.compose.key}"]`);
      if (ta) { ta.focus(); ta.scrollIntoView({ block: "nearest" }); }
    } else if (a === "uncompose") { S.compose = null; render(); }
    else if (a === "send") {
      const c = S.compose; if (!c) return;
      const ta = S.root.querySelector(`textarea[data-k="${c.key}"]`), text = ta ? ta.value.trim() : "";
      if (!text && c.kind !== "restart") { if (ta) ta.focus(); return toast(c.kind === "answer" ? "请写下你的回答" : "请写下要怎么改"); }
      act(c.action, c.task, text, true);
    } else if (a === "diff") openDiff(task);
    else if (a === "dlgclose") S.root.querySelector(".jv-dlg").close();
    else if (a === "older") loadRun(b.dataset.run);
    else if (a === "land") {
      const s = S;
      landClick(b, s.pid, s.data && s.data.result && s.data.result.land, () => { if (S === s) render(); }).then((land) => {
        if (S !== s || land === undefined) return;
        if (land && s.data.result) s.data.result.land = land;
        render(); load(true);
      });
    }
    else if (a === "jump") { const c = S.el.cards.querySelector(".jv-card"); if (c) c.scrollIntoView({ behavior: "smooth", block: "start" }); }
    else if (a === "stoppick") { S.stopPick = !S.stopPick; render(); }
    else if (a === "stopset") {
      if (S.data && b.dataset.v === S.data.job.stop_mode) { S.stopPick = false; return render(); }
      act("stop_mode", "", b.dataset.v);
    }
  }
  function onToggle(e) {
    const d = e.target;
    if (!S || d.tagName !== "DETAILS" || !d.dataset.k) return;
    const k = d.dataset.k;
    if (d.open === S.open.has(k)) return;
    if (d.open) S.open.add(k); else S.open.delete(k);
    if (d.open && k.startsWith("raw:")) { const id = k.slice(4), det = S.runs.get(id); if (!det || det.err || (det.live && det !== "loading")) loadRun(id); }
    if (d.open && k.startsWith("sec:")) sectionRuns(k).forEach((id) => { if (!S.runs.has(id)) loadRun(id); });
    render();
  }
  function onInput(e) {
    const t = e.target;
    if (S && t.tagName === "TEXTAREA" && t.dataset.k) S.drafts.set(t.dataset.k, t.value);
  }

  // ---- open / close (called by projects.js) ----------------------------------------------------------
  function open(root, { projectId, jobId }) {
    close();
    S = { root, pid: Number(projectId) || 0, jid: Number(jobId) || 0, data: null, sig: "", open: new Set(), drafts: new Map(),
      compose: null, runs: new Map(), fetching: new Set(), later: new Set(), busy: false, stopPick: false };
    S.el = skeleton(root);
    S.el.flow.innerHTML = '<p class="jv-muted jv-loading">正在加载…</p>';
    root.addEventListener("click", onClick);
    root.addEventListener("toggle", onToggle, true);
    root.addEventListener("input", onInput);
    S.timer = setInterval(() => { if (!document.hidden) tick(); }, 1000);
    load(true);
  }
  function close() {
    if (!S) return;
    const s = S;
    S = null;
    clearInterval(s.timer);
    s.root.removeEventListener("click", onClick);
    s.root.removeEventListener("toggle", onToggle, true);
    s.root.removeEventListener("input", onInput);
    const dlg = s.root.querySelector(".jv-dlg");
    if (dlg && dlg.open) dlg.close();
    s.root.innerHTML = "";
  }
  const poll = () => (S ? load(false) : Promise.resolve());
  return { open, close, poll, landHtml, landClick };
})();
