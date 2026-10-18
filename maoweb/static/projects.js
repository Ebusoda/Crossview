"use strict";
// 项目: the project list (#projects), one project (#projects/<id>), adding a folder (admin) and handing a chat
// discussion to a project. One job (#projects/<id>/<jobId>) is drawn by job.js: window.MAOJob.open / close.
// Uses app.js helpers: $, api, esc, toast, ago, money, tokens, schedulePoll, setBadge, refreshBadge.

const MAOProjects = (() => {
  const root = $("#projects-root"), jobRoot = $("#job-root"), topAdd = $("#top-add");   // topAdd: 添加项目 in the phone's top bar
  const ACTIVE = ["queued", "planning", "running", "pausing"], FINAL = ["done", "cancelled"];
  const COLORS = ["anthropic", "openai", "deepseek", "google", "alibaba", "other", "mao", "user"];
  const ICON = {
    back: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M12.5 4.5L7 10l5.5 5.5"/></svg>',
    next: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M7.5 4.5L13 10l-5.5 5.5"/></svg>',
    plus: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 4v12M4 10h12"/></svg>',
    folder: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3 6.5A1.5 1.5 0 0 1 4.5 5H8l1.5 2h6A1.5 1.5 0 0 1 17 8.5v6a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 3 14.5z"/></svg>',
    ok: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M5 10.5l3.2 3.2L15 7"/></svg>',
    no: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M6 6l8 8M14 6l-8 8"/></svg>',
    dots: '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="5" cy="10" r="1.2"/><circle cx="10" cy="10" r="1.2"/><circle cx="15" cy="10" r="1.2"/></svg>',
    up: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 15V5M5.5 9.5L10 5l4.5 4.5"/></svg>',
  };
  let route = { kind: "list" }, openKey = null, listData = null, page = null;
  const failing = { list: false };   // a list poll failed and the person was told

  // ---- small pieces ------------------------------------------------------------------------------
  const strong = (t) => `<strong class="pc-task" data-no-tr>${esc(String(t || ""))}</strong>`;
  const who = (a) => `<span class="who" data-no-tr>${esc(a ? a.name : "")}</span>`;
  const sep = '<span class="sep" aria-hidden="true">&nbsp;· </span>';   // a line never starts with the dot
  function avatar(a, cls = "") {
    if (!a) return "";
    const c = COLORS.includes(a.color) ? a.color : "other";
    const inner = c === "mao" ? '<span class="mark"></span>' : esc(String(a.initials || (a.name || "?").slice(0, 2)));
    return `<span class="av av-${c}${cls ? " " + cls : ""}" aria-hidden="true">${inner}</span>`;
  }
  function dur(sec) {
    const m = Math.round(Math.max(0, sec) / 60);
    return m < 1 ? "不到 1 分钟" : m < 60 ? `${m} 分钟` : `${Math.floor(m / 60)} 小时 ${m % 60} 分钟`;
  }
  // replace a box's content only when it changed: keeps scroll, focus, open <details> and the English overlay
  function put(el, html) {
    if (!el || el._src === html) return false;
    const open = new Set([...el.querySelectorAll("details[open][data-k]")].map((d) => d.dataset.k));
    el._src = html; el.innerHTML = html;
    el.querySelectorAll("details[data-k]").forEach((d) => { if (open.has(d.dataset.k)) d.open = true; });
    return true;
  }
  const isLive = (j) => !!j && (ACTIVE.includes(j.status) || !!(j.now && j.now.live));

  // ---- what a job is doing, in one line ------------------------------------------------------------
  const NOW_VERB = { read: "正在看", list: "正在看目录", search: "正在找", edit: "正在改", write: "正在写", delete: "正在删",
    run: "正在运行", web: "正在查资料", todo: "正在列计划", agent: "正在派帮手", tool: "正在用工具" };
  const DID_VERB = { read: "看了", list: "看了目录", search: "找了", edit: "改了", write: "写了", delete: "删了",
    run: "运行了", web: "查了资料", todo: "列了计划", agent: "派了帮手", tool: "用了工具" };
  const RECENT = { planned: "出了方案", approved: "检查通过了", changes: "要求改一下", verified: "做完了，验收通过",
    asked: "提了一个问题", failed: "没做成", merged: "合并好了" };
  const STAGE_INDEX = { plan: 0, plan_gate: 0, build: 1, check: 2, merge_gate: 3 };

  const paused = (j) => j.status === "paused" || j.status === "pausing";
  function waitLine(j) {
    if (j.waiting === "stopped" && paused(j))   // the user paused it: nothing went wrong
      return `<span>${j.mine ? "暂停了，点「继续做」接着来：" : "暂停了："}</span>${strong(j.summary_task || j.title)}`;
    const pre = j.mine
      ? { plan: "方案出来了，等你看一下：", merge: "检查通过，等你点「合并」：", attention: "遇到问题，要你看一下：",
          stopped: "停下来了，等你决定怎么办：" }
      : { plan: "方案出来了，等着同意：", merge: "检查通过，等着合并：", attention: "遇到问题，等着处理：", stopped: "停下来了：" };
    return `<span>${pre[j.waiting] || pre.stopped}</span>${strong(j.summary_task || j.title)}`;
  }
  function busyLine(j) {
    const n = j.now, task = strong(j.summary_task || j.title), agent = n && n.agent && n.agent.vendor !== "mao" ? n.agent : null;
    if (j.cancelling || (n && n.kind === "cancel")) return `<span>正在停下来：</span>${task}`;   // 不做了 is on its way
    if (j.status === "pausing") return `<span>正在暂停：</span>${task}`;
    if (j.stage === "plan") return agent ? `${who(agent)}<span> 在看项目、出方案</span>` : "<span>正在准备商量方案</span>";
    if (n && n.kind === "verify") return `<span>Crossview 正在验收：</span>${task}`;
    if (n && n.kind === "merge") return `<span>Crossview 正在合并：</span>${task}`;
    if (n && n.kind === "wait") return `<span>等下一个 AI 空出来：</span>${task}`;
    if (j.status === "queued") return `<span>马上开始：</span>${task}`;
    if (j.stage === "check") return agent ? `${who(agent)}<span> 正在检查：</span>${task}` : `<span>正在检查这一项：</span>${task}`;
    return agent ? `${who(agent)}<span> 正在写：</span>${task}` : `<span>正在动手：</span>${task}`;
  }
  const jobLine = (j) => (j.waiting ? waitLine(j) : busyLine(j));
  function jobMeta(j) {
    const parts = [];
    if (j.waiting && j.recent) parts.push(`${who(j.recent.agent)}<span> ${RECENT[j.recent.what] || ""}</span>`, `<span>${ago(j.recent.at)}</span>`);
    else if (j.waiting) parts.push(`<span>${ago(j.updated_at)}</span>`);
    else {
      const n = j.now;
      if (n && n.target && DID_VERB[n.kind]) parts.push(`<span>刚才：</span><span>${DID_VERB[n.kind]}</span> <span class="tgt" data-no-tr>${esc(n.target)}</span>`);
      if (j.item && j.item.total > 1) parts.push(`<span>第 ${j.item.index} / ${j.item.total} 项</span>`);
      const age = Date.now() / 1000 - j.created_at;
      parts.push(age < 60 ? "<span>刚开始</span>" : `<span>已经 ${dur(age)}</span>`);
    }
    return parts.join(sep);
  }
  function bar(j, labels = true) {
    const p = j.progress || [0, 0, 0, 0], cur = j.stage in STAGE_INDEX ? STAGE_INDEX[j.stage] : -1;
    const gate = j.stage === "plan_gate" || j.stage === "merge_gate";
    const segs = p.map((v, i) => {
      const w = Math.round(Math.min(1, Math.max(0, v || 0)) * 100);
      return `<span class="pseg${w >= 100 ? " full" : ""}${i === cur && !gate && isLive(j) ? " live" : ""}"><span style="width:${w}%"></span></span>`;
    }).join("");
    const names = ["商量", "动手", "检查", "合并"].map((t, i) =>
      `<span class="${i === cur ? "cur" : ""}">${i === cur && gate ? (j.mine ? `${t} · 等你` : `${t} · 等着`) : t}</span>`).join("");
    return `<div class="pbar"><div class="pbar-segs" aria-hidden="true">${segs}</div>${labels ? `<div class="pbar-labels">${names}</div>` : ""}</div>`;
  }
  function jobBadge(j) {
    if (j.cancelling) return '<span class="badge">正在停下来</span>';
    if (j.waiting === "stopped" && paused(j)) return '<span class="badge">已暂停</span>';
    if (j.waiting === "attention" || j.waiting === "stopped") return `<span class="badge bad">${j.mine ? "要你看一下" : "停下来了"}</span>`;
    if (j.waiting) return j.mine ? '<span class="badge you">等你决定</span>' : `<span class="badge wait">等 ${esc(j.owner || "")} 决定</span>`;
    return '<span class="badge run"><span class="spinner small"></span>进行中</span>';
  }

  // ---- #projects ---------------------------------------------------------------------------------
  function card(p) {
    const j = p.job, href = `#projects/${p.id}`, jhref = j ? `#projects/${p.id}/${j.id}` : href;
    let badge, line, meta = "", btn, cls = "";
    if (p.state === "broken" || !p.ready) {
      badge = '<span class="badge bad">用不了</span>';
      line = `<span class="pc-problem">${esc(p.problem || "这个项目现在打不开。")}</span>`;
      btn = `<a class="btn ghost" href="${href}">看看</a>`; cls = "is-broken";
    } else if (j) {
      badge = jobBadge(j); line = jobLine(j); meta = jobMeta(j);
      const yours = j.waiting && j.mine;
      btn = yours ? `<a class="btn primary" href="${jhref}">去看看</a>` : `<a class="btn ghost" href="${jhref}">看过程</a>`;
      cls = yours ? "is-waiting" : j.waiting === "attention" || (j.waiting === "stopped" && !paused(j)) ? "is-attention" : "";
    } else if (p.busy_other) {   // only a flag here: it may be working or waiting for its owner
      badge = '<span class="badge wait">有人在用</span>';
      line = "<span>有人正在用这个项目，做完后才能开始新任务。</span>";
      btn = `<a class="btn ghost" href="${href}">看看</a>`;
    } else {
      badge = '<span class="badge">空闲</span>';
      line = p.last ? `<span class="dim">上次做的：</span>${strong(p.last.title)}${sep}<span class="dim">${ended(p.last.status, p.last.merged)[0]}</span>${sep}<span class="dim">${ago(p.last.at)}</span>`
        : '<span class="dim">还没有交代过任务。</span>';
      const free = p.free || [];
      meta = free.length === 1 ? `<span data-no-tr>${esc(free[0])}</span><span> 空着</span>` : free.length ? `<span>${esc(free.join("、"))} 都空着</span>` : "";
      btn = `<a class="btn primary" href="${href}">${ICON.plus}新任务</a>`;
    }
    return `<article class="proj-card ${cls}" data-href="${href}">
      <div class="pc-head"><h2><a href="${href}" data-no-tr>${esc(p.name)}</a></h2>${badge}</div>
      <div class="pc-path" data-no-tr>${esc(p.path)}</div>
      <p class="pc-line">${line}</p>
      ${j ? bar(j) : ""}
      <div class="pc-foot"><span class="pc-meta">${meta}</span>${btn}</div></article>`;
  }
  function renderList(d) {
    const cards = d.projects || [];
    const you = cards.filter((p) => p.job && p.job.waiting && p.job.mine).length;
    const doing = cards.filter((p) => p.job && p.job.mine && !p.job.waiting).length;
    // someone else's task (the admin gets it as card.job, a member only as busy_other): not "in progress" for this viewer
    const other = cards.filter((p) => (p.job && !p.job.mine) || (!p.job && p.busy_other)).length;
    const idle = cards.filter((p) => p.state === "idle" && !p.busy_other).length;
    const chips = [you ? `<span class="pchip you"><span class="dot"></span>${you} 件等你决定</span>` : "",
      doing ? `<span class="pchip doing">${doing} 件正在做</span>` : "", other ? `<span class="pchip">${other} 个有人在用</span>` : "",
      idle ? `<span class="pchip">${idle} 个空闲</span>` : ""].join("");
    const add = d.can_add ? `<button type="button" class="add-card" data-a="add"><span class="ac-ic">${ICON.folder}</span>
      <span class="ac-text"><strong>添加项目</strong><span>选这台电脑上的一个文件夹就行。还没准备好的文件夹，Crossview 会自己准备好，不用再开命令行。</span></span></button>` : "";
    const empty = cards.length ? "" : d.can_add ? '<p class="empty">还没有项目。添加一个文件夹，就可以把程序交给 AI 团队做。</p>'
      : '<p class="empty">管理员还没有添加项目。添加以后，这里就能把程序交给 AI 团队做。</p>';
    put(root, `<div class="page-head proj-head"><h1>项目</h1><span class="spacer"></span>
        ${d.can_add ? `<button type="button" class="btn ghost add-btn" data-a="add">${ICON.plus}添加项目</button>` : ""}</div>
      <p class="proj-intro">把程序交给 AI 团队做。每一步在做什么都看得到，关键的地方会停下来等你。</p>
      ${chips ? `<div class="pchips">${chips}</div>` : ""}
      ${empty}<div class="proj-cards">${cards.map(card).join("")}${add}</div>`);
    topAdd.hidden = !(d.can_add && state.view === "projects");
    setBadge(you, state.user.discuss_waiting);
  }
  async function loadList() {
    let d;
    try { d = await api("/api/projects"); }
    catch (err) {
      if (route.kind !== "list") return;
      if (listData) { if (!failing.list) toast(err.message); failing.list = true; }   // keep the cards; say it once
      else put(root, `<div class="page-head proj-head"><h1>项目</h1></div><div class="notice">${esc(err.message)}</div>`);
      return schedulePoll("projects", true, 15000);
    }
    if (route.kind !== "list") return;
    failing.list = false;
    listData = d; renderList(d);
    const busy = d.projects.some((p) => isLive(p.job) || p.busy_other);
    schedulePoll("projects", true, busy ? 5000 : 15000);
  }

  // ---- #projects/<id> ----------------------------------------------------------------------------
  const TEAM_STATE = { idle: "空闲", working: "工作中", paused: "已暂停", cooling: "暂时停用", missing: "没装好" };
  const ROLE_ROWS = [["plan", "商量", "负责出方案"], ["build", "动手", "负责写代码"], ["check", "检查", "负责检查"]];
  const HIST = { done: ["已合并", "ok"], cancelled: ["放弃了", "no"], failed: ["没有做成", "bad"], needs_attention: ["要你看一下", "bad"],
    interrupted: ["停在了一半", "bad"], paused: ["已暂停", "wait"], plan_ready: ["等你看方案", "wait"], waiting: ["等你决定", "wait"] };
  // how a job ended (or where it is): a dropped job that merged some items first kept those
  const ended = (status, merged) => (status === "cancelled" && merged ? ["合并了一部分", "ok"] : HIST[status] || ["正在做", "run"]);
  const draftKey = (pid) => `mao.goal.${pid}`;

  function stopModes(name, value) {
    return "<legend>什么时候停下来给你看</legend>" + [
      ["each", "每做完一步都停（推荐）", "商量好计划、每一项检查完，都先给你看，你点了才做下一步"],
      ["plan", "只在开始前看计划", "计划通过后自动做完、自动合并，出问题才叫你"],
    ].map(([v, t, s]) => `<label class="stop-mode"><input type="radio" name="${name}" value="${v}"${v === value ? " checked" : ""}>
      <span class="sm-text"><span class="sm-t">${t}</span><span class="sm-s">${s}</span></span></label>`).join("");
  }
  const savedStop = () => (localStorageGet("mao.stop") === "plan" ? "plan" : "each");

  function skeleton(pid, admin) {
    root._src = null;
    root.innerHTML = `<a class="back-link" href="#projects">${ICON.back}<span>项目</span></a>
      <div class="pp-head"><div class="pp-title" data-slot="title"></div>
        ${admin ? `<div class="pp-menu"><button type="button" class="icon-btn" data-a="menu" aria-label="项目设置" aria-haspopup="true" aria-expanded="false">${ICON.dots}</button>
          <div class="menu" role="menu" hidden><button type="button" role="menuitem" data-a="rename">改名字</button>
            <button type="button" role="menuitem" data-a="remove">从列表里移除</button></div></div>` : ""}</div>
      <div data-slot="problem"></div>
      <section class="team-sec" aria-label="这个项目的 AI" data-slot="team"></section>
      <div data-slot="banner"></div>
      <div class="pp-land" data-slot="land"></div>
      <form class="create-card composer-card" data-slot="form" autocomplete="off" hidden>
        <label class="cc-label" for="pp-goal">这次要做什么？</label>
        <textarea id="pp-goal" name="goal" rows="3" maxlength="8000" required placeholder="例如：给计算器加上减法，并补上测试。要能算负数和小数。"></textarea>
        <div class="cc-div"></div>
        <div class="who-does"><span class="cc-h">谁来做：Crossview 安排</span><div class="roles" data-slot="roles"></div>
          <span class="small-note" data-slot="rnote"></span></div>
        <div class="cc-div"></div>
        <fieldset class="stop-modes">${stopModes("stop_mode", savedStop())}</fieldset>
        <p class="error" role="alert"></p>
        <button class="primary go start-btn" type="submit">开始商量</button>
        <span class="small-note cc-foot">商量的时候只看文件，不会改动项目。</span>
      </form>
      <section class="history" data-slot="history"></section>`;
    const slots = {};
    root.querySelectorAll("[data-slot]").forEach((el) => (slots[el.dataset.slot] = el));
    const form = slots.form, goal = form.elements.goal;
    goal.value = localStorageGet(draftKey(pid)) || "";
    goal.addEventListener("input", () => localStorageSet(draftKey(pid), goal.value));
    form.addEventListener("submit", (e) => { e.preventDefault(); submitJob(pid, form); });
    return { pid, slots, data: null };
  }

  async function submitJob(pid, form) {
    const goal = form.elements.goal.value.trim(), btn = form.querySelector(".start-btn"), err = form.querySelector(".error");
    if (!goal) return form.elements.goal.focus();
    const stop = form.elements.stop_mode.value;
    localStorageSet("mao.stop", stop);
    err.textContent = ""; btn.disabled = true; btn.textContent = "正在提交…";
    try {
      const d = await api("/api/jobs", { method: "POST", body: { project_id: pid, goal, stop_mode: stop } });
      localStorageSet(draftKey(pid), ""); form.elements.goal.value = "";
      location.hash = `#projects/${pid}/${d.job_id}`;
    } catch (e) { err.textContent = e.message; }
    finally { btn.disabled = false; btn.textContent = "开始商量"; }
  }

  function teamHtml(team) {
    if (!team.length) return '<span class="sec-label">这个项目能用的 AI</span><p class="small-note">还没有能用的 AI。请检查项目的 .agents/orch.toml。</p>';
    return '<span class="sec-label">这个项目能用的 AI</span><div class="team-strip">' + team.map((m) => {
      const st = m.available === false ? "missing" : m.state;
      const q = m.quota && m.quota.five_hour;
      const second = st === "missing" ? "" : m.billing === "api" ? `今天花了 ${money(m.today_cost_usd || 0)}`
        : typeof q === "number" ? `5 小时额度用了 ${Math.round(q)}%` : m.today_tokens ? `今天 ${tokens(m.today_tokens)} token` : "";   // short: three cards share a phone row
      return `<div class="team-card${st === "working" ? " working" : st === "missing" || st === "cooling" || st === "paused" ? " off" : ""}">
        <div class="tc-head">${avatar(m.agent)}<strong data-no-tr>${esc(m.agent.name)}</strong>${st === "working" ? '<span class="live-dot" aria-hidden="true"></span>' : ""}</div>
        <span class="tc-line">${TEAM_STATE[st] || esc(st)} · ${m.billing === "api" ? "按量" : "订阅"}</span>
        ${second ? `<span class="tc-line">${second}</span>` : ""}</div>`;
    }).join("") + "</div>";
  }
  function rolesHtml(roles) {
    return ROLE_ROWS.map(([k, label, verb]) => {
      const a = roles && roles[k];
      return `<div class="role-row"><span class="role-k">${label}</span>${a ? `<span class="role-who">${avatar(a, "sm")}${who(a)}<span> ${verb}</span></span>`
        : `<span class="dim">${k === "check" ? "只跑验收命令" : "暂时没有能用的 AI"}</span>`}</div>`;
    }).join("");
  }
  function bannerHtml(d) {
    const j = d.active;
    if (j) {   // the line below already names the task when there is no shorter summary: no title twice
      const dup = (j.summary_task || j.title) === j.title && (j.waiting || j.stage !== "plan" || j.status === "pausing");
      return `<div class="pp-banner${j.waiting && j.mine ? " is-waiting" : ""}">
        <div class="pb-head">${jobBadge(j)}${dup ? "" : `<strong class="pb-title" data-no-tr>${esc(j.title)}</strong>`}</div>
        ${j.mine ? "" : `<span class="small-note">交代的人：${esc(j.owner || "")}</span>`}
        <p class="pc-line">${jobLine(j)}</p>${bar(j)}
        <div class="pc-foot"><span class="pc-meta">${jobMeta(j)}</span><a class="btn ${j.waiting && j.mine ? "primary" : "ghost"}" href="#projects/${d.project.id}/${j.id}">${j.waiting && j.mine ? "去看看" : "看过程"}</a></div>
        <p class="small-note">这件做完（或者点「不做了」）以后，才能交代新任务。</p></div>`;
    }
    const o = d.busy_other;
    if (o) {
      return `<div class="pp-banner other"><p class="pb-other">现在 ${esc(o.owner || "")} 在用这个项目，做完后才能开始新任务</p>
        <p class="pc-meta"><span data-no-tr>${esc(o.title || "")}</span>${o.since ? `${sep}<span>${ago(o.since)}</span>` : ""}</p></div>`;
    }
    return "";
  }
  function historyHtml(d) {
    const active = d.active ? d.active.id : 0;
    const rows = (d.jobs || []).filter((j) => j.id !== active);
    if (!rows.length) return '<h2 class="sec-h">做过的</h2><p class="empty">还没有做过任务。</p>';
    return '<h2 class="sec-h">做过的</h2><div class="hist-list">' + rows.map((j) => {
      const [label, cls] = ended(j.status, j.merged);
      const icon = cls === "ok" ? ICON.ok : cls === "no" ? ICON.no : cls === "run" ? '<span class="spinner small"></span>' : "!";
      const end = FINAL.includes(j.status) ? j.updated_at : Date.now() / 1000;
      const meta = [label, ago(j.created_at)];
      if (j.item && j.item.total) meta.push(`${j.item.total} 项`);
      if (FINAL.includes(j.status)) meta.push(dur(end - j.created_at));
      return `<a class="hist-row" href="#projects/${d.project.id}/${j.id}"><span class="hist-ic ${cls}">${icon}</span>
        <span class="hist-main"><span class="hist-title" data-no-tr>${esc(j.title)}</span>
          <span class="hist-meta">${meta.map((m) => `<span>${m}</span>`).join(sep)}${j.mine ? "" : `${sep}<span data-no-tr>${esc(j.owner || "")}</span>`}</span></span>${ICON.next}</a>`;
    }).join("") + "</div>";
  }
  function renderProject(d) {
    const s = page.slots, p = d.project;
    put(s.title, `<h1 class="pp-name" data-no-tr>${esc(p.name)}</h1><div class="pp-sub"><span class="pc-path" data-no-tr>${esc(p.path)}</span>
      ${p.ready ? `<span class="ready">${ICON.ok}已准备好</span>` : '<span class="not-ready">现在用不了</span>'}</div>`);
    put(s.problem, p.ready ? "" : `<div class="notice bad-notice"><strong>这个项目现在用不了。</strong> <span>${esc(p.problem || "")}</span>
      ${state.user.admin ? "<br><span>可以检查一下文件夹，或者从列表里移除后重新添加。</span>" : ""}</div>`);
    put(s.team, p.ready ? teamHtml(d.team || []) : "");
    put(s.banner, bannerHtml(d));
    put(s.land, p.ready && window.MAOJob ? MAOJob.landHtml(p.id, d.land, "project") : "");
    put(s.roles, rolesHtml(d.roles));
    put(s.rnote, d.roles && d.roles.check ? "检查的总是另一家公司的 AI，不让它自己查自己。" : "现在没有别家的 AI 能检查，只会跑验收命令。");
    s.form.hidden = !p.ready || !!d.active || !!d.busy_other;
    put(s.history, historyHtml(d));
  }
  async function loadProject(first) {
    const pid = route.pid;
    if (first || !page || page.pid !== pid) page = skeleton(pid, state.user.admin);
    let d;
    try { d = await api(`/api/project?id=${pid}`); }
    catch (err) {
      if (route.kind !== "project" || route.pid !== pid || !page) return;
      const gone = /没有这个项目/.test(err.message);
      if (page.data && !gone) {   // keep the page and the goal being typed; say it once
        if (!page.failing) toast(err.message);
        page.failing = true;
        return schedulePoll("projects", true, 15000);
      }
      put(page.slots.title, `<h1 class="pp-name">项目</h1>`);
      put(page.slots.problem, `<div class="notice">${esc(err.message)}</div>`);
      page.slots.form.hidden = true;
      return gone ? undefined : schedulePoll("projects", true, 15000);
    }
    if (route.kind !== "project" || route.pid !== pid || !page) return;
    page.failing = false;
    page.data = d; renderProject(d);
    refreshBadge();
    schedulePoll("projects", true, isLive(d.active) || (d.busy_other && true) ? 5000 : 15000);
  }

  // 放进我的项目文件夹 (job.js draws it and sends the request)
  async function land(b) {
    const pg = page, d = pg && pg.data;
    if (!d || !window.MAOJob) return;
    const l = await MAOJob.landClick(b, d.project.id, d.land, () => { if (page === pg) renderProject(pg.data); });
    if (page !== pg || l === undefined || route.kind !== "project") return;
    if (l) pg.data.land = l;
    renderProject(pg.data);
    loadProject(false);
  }

  // admin: the small menu on a project page
  async function projectMenu(a) {
    const menu = root.querySelector(".pp-menu .menu"), d = page && page.data;
    if (a === "menu") {
      menu.hidden = !menu.hidden;
      root.querySelector('[data-a="menu"]').setAttribute("aria-expanded", String(!menu.hidden));
      return;
    }
    menu.hidden = true;
    if (!d) return;
    const p = d.project;
    try {
      if (a === "rename") {
        const name = prompt("给这个项目起个新名字", p.name);
        if (!name || !name.trim() || name.trim() === p.name) return;
        await api("/api/admin/projects/rename", { method: "POST", body: { id: p.id, name: name.trim() } });
        toast("已改名"); await loadProject(false);
      }
      if (a === "remove") {
        if (!confirm(`把「${p.name}」从项目列表里移除？文件夹里的文件不会动。`)) return;
        await api("/api/admin/projects/remove", { method: "POST", body: { id: p.id } });
        toast("已从列表里移除"); location.hash = "#projects";
      }
    } catch (err) { toast(err.message); }
  }
  document.addEventListener("click", (e) => {
    const m = root.querySelector(".pp-menu .menu");
    if (m && !m.hidden && !e.target.closest(".pp-menu")) m.hidden = true;
  });

  topAdd.onclick = () => openAdd();
  root.addEventListener("click", (e) => {
    const b = e.target.closest("[data-a]");
    if (b) {
      if (b.dataset.a === "add") return openAdd();
      if (b.dataset.a === "land") return land(b);
      return projectMenu(b.dataset.a);
    }
    const c = e.target.closest(".proj-card");   // the whole card opens the project
    if (c && !e.target.closest("a, button")) location.hash = c.dataset.href;
  });

  // ---- routing (app.js calls load / poll / leave) ---------------------------------------------------
  function parse() {
    const [, a, b] = location.hash.slice(1).split("/");
    const pid = Number(a) || 0, jid = Number(b) || 0;
    return pid && jid ? { kind: "job", pid, jid } : pid ? { kind: "project", pid } : { kind: "list" };
  }
  function closeJob() {
    if (openKey && window.MAOJob && MAOJob.close) { try { MAOJob.close(); } catch (err) { console.error(err); } }
    openKey = null; jobRoot.hidden = true;
  }
  function openJob(r) {
    root.hidden = true; jobRoot.hidden = false;
    const key = `${r.pid}/${r.jid}`;
    if (openKey === key) {   // the same job again (e.g. the settings dialog closed): refresh it, keep it polling
      if (window.MAOJob && MAOJob.poll) MAOJob.poll();
      return;
    }
    schedulePoll("projects", false);
    closeJob(); jobRoot.hidden = false; openKey = key;
    if (window.MAOJob && MAOJob.open) MAOJob.open(jobRoot, { projectId: r.pid, jobId: r.jid });
    else jobRoot.innerHTML = '<div class="page-inner"><p class="empty">正在加载…</p></div>';
  }
  async function load() {
    const prev = route;
    route = parse();
    topAdd.hidden = true;
    if (route.kind === "job") return openJob(route);
    closeJob(); root.hidden = false;
    $("#conv-title").textContent = "项目";
    if (route.kind === "list") { page = null; if (prev.kind !== "list") { root._src = null; listData = null; } return loadList(); }
    return loadProject(prev.kind !== "project" || prev.pid !== route.pid);
  }
  function poll() {
    if (state.view !== "projects") return Promise.resolve();
    if (route.kind === "job") {
      const J = window.MAOJob;
      return Promise.resolve(J && (J.poll || J.refresh) ? (J.poll || J.refresh)() : undefined);
    }
    return route.kind === "list" ? loadList() : loadProject(false);
  }
  function leave() { closeJob(); topAdd.hidden = true; }
  const polling = () => route.kind !== "job";

  // ---- add a project (admin) ---------------------------------------------------------------------
  const STEP_TEXT = { create_folder: "新建这个文件夹", git_init: "让它能保存版本（git）", first_commit: "把现在的文件存成第一个版本",
    commit_changes: "把你还没保存的改动存成一个版本", orch_init: "写好 Crossview 的设置（.agents 文件夹）", register: "加到项目列表里" };
  const PROBLEM_TEXT = {
    not_absolute: "请填写完整的文件夹路径（从盘符或 / 开始）。",
    drive_root: "不能把整个磁盘当成项目，请选里面的一个文件夹。",
    home_folder: "这是你的个人文件夹（或者包含它），里面有私人文件，不能整个交给 AI。请选一个专门放项目的文件夹。",
    personal_folder: "这是放私人文件的地方（桌面、文档、下载等），不能整个交给 AI。请在里面新建一个专门的项目文件夹。",
    mao_folder: "这是 Crossview 自己的文件夹，不能当成项目。项目可以放在 Crossview 的 playground 文件夹里。",
    worktree: "这是 Crossview 给 AI 干活用的临时文件夹，不能当成项目。",
    inside_repo: "这个文件夹在另一个项目（git 仓库）里面。请直接添加外面那个文件夹：",
    no_git: "这台电脑还没有安装 git。先安装 git，再来添加项目。",
    git_error: "git 打不开这个文件夹。",
    missing: "找不到这个文件夹，它的上一级文件夹也不存在。",
    not_dir: "这是一个文件，不是文件夹。",
    registered: "这个文件夹已经添加过了：",
    contains_project: "这个文件夹里面已经有一个项目了，请直接用那个项目：",
    many_files: "这个文件夹里的文件太多了（超过 5 万个），不适合交给 AI。请选一个小一点的文件夹。",
  };
  const WARN_TEXT = {
    dirty: "有些改动还没有保存成版本。AI 会从上一次保存的版本开始做，看不到这些改动。",
    big_files: "这些大文件或文件夹不会放进给 AI 用的版本：",
    secrets: "这些像是密码或密钥的文件不会放进给 AI 用的版本：",
    many_files: "文件很多，第一次准备会慢一些。",
    nested_repos: "这些子文件夹自己也是 git 仓库，AI 看不到里面的文件：",
  };
  // the values inside a server sentence (folder names, git's own message), never the sentence itself
  function detailOf(code, text, chk) {
    text = String(text || "");
    if (code === "registered") return chk.registered || (text.match(/「(.+)」/) || [])[1] || "";
    if (code === "contains_project") return (text.match(/「(.+)」/) || [])[1] || "";
    if (code === "inside_repo") return (text.match(/：(.+?)。/) || [])[1] || "";
    if (["git_error", "big_files", "secrets", "nested_repos"].includes(code)) {
      const i = text.indexOf("：");
      return i >= 0 ? text.slice(i + 1) : "";
    }
    return "";
  }
  function lineHtml(code, item, map, chk, cls) {
    const known = map[code], detail = known ? detailOf(code, item.text, chk) : "", more = detail.match(/^(.*) 等 (\d+) 个$/);
    return `<li class="${cls}"><span>${known || "这个文件夹现在不能直接添加。"}</span>${detail ? ` <span class="cr-detail" data-no-tr>${esc(more ? more[1] : detail)}</span>` : ""}${more ? ` <span>（共 ${more[2]} 个）</span>` : ""}
      ${known ? "" : `<details class="raw"><summary>详细记录</summary><pre data-no-tr>${esc(item.text || code)}</pre></details>`}</li>`;
  }

  const add = { dlg: $("#addproj-dialog"), body: $("#addproj-body"), check: null, browse: null, busy: false };
  function openAdd() {
    add.check = null; add.browse = null;
    add.body.innerHTML = `<p class="small-note">选这台电脑上的一个文件夹。还没准备好的文件夹，Crossview 会自己准备好（保存版本、写好设置），不用再开命令行。</p>
      <form class="add-form" autocomplete="off">
        <label for="add-path">文件夹</label>
        <div class="path-row"><input id="add-path" name="path" spellcheck="false" placeholder="文件夹的完整路径，例如 D:\\code\\my-site">
          <button type="button" class="ghost" data-a="browse">浏览</button><button type="submit" class="primary">检查</button></div>
        <div class="browser" hidden></div>
        <div class="check-res" aria-live="polite"></div>
      </form>`;
    const form = add.body.querySelector("form");
    form.addEventListener("submit", (e) => { e.preventDefault(); runCheck(form.elements.path.value); });
    form.elements.path.addEventListener("input", () => { if (add.check) { add.check = null; drawCheck(); } });
    add.body.onclick = addClick;
    add.body.onchange = (e) => { if (e.target.name === "commit_changes" || e.target.name === "create") drawSteps(); };
    add.dlg.showModal();
    form.elements.path.focus();
  }
  add.dlg.addEventListener("click", (e) => { if (e.target.closest("[data-close]")) add.dlg.close(); });

  async function addClick(e) {
    const b = e.target.closest("[data-a]"); if (!b) return;
    const form = add.body.querySelector("form"), a = b.dataset.a;
    if (a === "browse") return browse(form.elements.path.value.trim());
    if (a === "up" && add.browse && add.browse.parent) return browse(add.browse.parent);
    if (a === "dir") return browse(b.dataset.path);
    if (a === "use" && add.browse) { form.elements.path.value = add.browse.path; add.body.querySelector(".browser").hidden = true; return runCheck(add.browse.path); }
    if (a === "close-browser") { add.body.querySelector(".browser").hidden = true; return; }
    if (a === "add") return doAdd(b);
  }
  async function browse(path) {
    const box = add.body.querySelector(".browser");
    box.hidden = false;
    if (!add.browse) box.innerHTML = '<p class="small-note">正在加载…</p>';
    try { add.browse = await api(`/api/admin/folders?path=${encodeURIComponent(path || "")}`); }
    catch (err) {
      if (path) return browse("");       // a typed path that cannot be opened: start from the home folder
      box.innerHTML = `<p class="error">${esc(err.message)}</p>`; return;
    }
    const b = add.browse;
    box.innerHTML = `<div class="br-head"><button type="button" class="ghost br-up" data-a="up" ${b.parent ? "" : "disabled"} aria-label="上一级">${ICON.up}<span>上一级</span></button>
        <code class="br-path" data-no-tr>${esc(b.path)}</code></div>
      <div class="br-roots">${(b.roots || []).map((r) => `<button type="button" class="pill" data-a="dir" data-path="${esc(r.path)}" data-no-tr>${esc(r.name)}</button>`).join("")}
        ${b.home ? `<button type="button" class="pill" data-a="dir" data-path="${esc(b.home)}">我的文件夹</button>` : ""}</div>
      <div class="br-list">${(b.folders || []).map((f) => `<button type="button" class="br-dir" data-a="dir" data-path="${esc(f.path)}">${ICON.folder}<span data-no-tr>${esc(f.name)}</span></button>`).join("")
        || '<p class="small-note">这里面没有文件夹。</p>'}${b.more ? '<p class="small-note">文件夹太多，只列出了前 300 个。</p>' : ""}</div>
      <div class="row-end"><button type="button" class="ghost" data-a="close-browser">收起</button><button type="button" class="primary" data-a="use">用这个文件夹</button></div>`;
  }
  async function runCheck(path) {
    path = String(path || "").trim();
    const res = add.body.querySelector(".check-res");
    if (!path) { add.body.querySelector("[name=path]").focus(); return; }
    res.innerHTML = '<p class="small-note"><span class="spinner small"></span>正在检查…</p>';
    try { add.check = await api("/api/admin/projects/check", { method: "POST", body: { path } }); }
    catch (err) { add.check = null; res.innerHTML = `<p class="error">${esc(err.message)}</p>`; return; }
    drawCheck();
  }
  function drawCheck() {
    const res = add.body.querySelector(".check-res"), c = add.check;
    if (!c) { res.innerHTML = ""; return; }
    const problems = c.problems || [], warnings = c.warnings || [], g = c.git || {};
    let html = "";
    if (problems.length) html += `<p class="cr-h bad">这个文件夹不能添加：</p><ul class="cr-list">${problems.map((x) => lineHtml(x.code, x, PROBLEM_TEXT, c, "bad")).join("")}</ul>`;
    if (problems.length && !(c.can_create && problems.every((x) => x.code === "missing"))) { res.innerHTML = html; return; }
    if (!c.exists) html += '<p class="cr-h">这个文件夹还不存在。</p>';
    html += `<p class="cr-h ok">${ICON.ok}<span>${c.steps && c.steps.length > 1 ? "可以添加。Crossview 会这样准备它：" : "可以添加，它已经准备好了。"}</span></p><ol class="cr-steps"></ol>`;
    if (warnings.length) html += `<ul class="cr-list">${warnings.map((x) => lineHtml(x.code, x, WARN_TEXT, c, "warn")).join("")}</ul>`;
    if (g.dirty_count) html += `<details class="raw" data-k="dirty"><summary>没保存的改动（${g.dirty_count} 处）</summary><pre data-no-tr>${esc((g.dirty || []).join("\n"))}</pre></details>`;
    html += `<label>项目名字<input name="name" maxlength="40" value="${esc(c.name || "")}"></label>`;
    if (g.dirty_count) html += '<label class="check-row"><input type="checkbox" name="commit_changes"><span>先把我还没保存的改动存一个版本</span></label>';
    if (!c.exists && c.can_create) html += '<label class="check-row"><input type="checkbox" name="create"><span>新建这个文件夹</span></label>';
    html += '<p class="error" role="alert"></p><div class="row-end"><button type="button" class="ghost" data-close>取消</button><button type="button" class="primary" data-a="add">添加</button></div>';
    res.innerHTML = html;
    drawSteps();
  }
  function drawSteps() {
    const c = add.check, list = add.body.querySelector(".cr-steps"); if (!c || !list) return;
    const ticked = add.body.querySelector("[name=commit_changes]"), create = add.body.querySelector("[name=create]");
    list.innerHTML = (c.steps || []).filter((s) => s !== "commit_changes" || (ticked && ticked.checked))
      .map((s) => `<li>${STEP_TEXT[s] || esc(s)}</li>`).join("");
    const go = add.body.querySelector('[data-a="add"]');
    if (go) go.disabled = add.busy || (!c.exists && !(create && create.checked));
  }
  async function doAdd(btn) {
    const c = add.check; if (!c || add.busy) return;
    const f = (n) => add.body.querySelector(`[name=${n}]`), err = add.body.querySelector(".check-res .error");
    add.busy = true; btn.disabled = true; btn.textContent = "正在准备…"; err.textContent = "";
    try {
      const d = await api("/api/admin/projects/add", { method: "POST", body: {
        path: c.path, name: (f("name") && f("name").value.trim()) || c.name, create: !!(f("create") && f("create").checked),
        commit_changes: !!(f("commit_changes") && f("commit_changes").checked), lang: I18N.lang } });
      add.dlg.close(); toast("已添加");
      location.hash = `#projects/${d.project.id}`;
    } catch (e) { err.textContent = e.message; }
    finally { add.busy = false; btn.textContent = "添加"; drawSteps(); }
  }

  // ---- chat: hand the discussion to a project, then follow it from the thread ------------------------
  const chatBox = $("#chat-jobs"), hd = $("#handoff-dialog"), hform = $("#handoff-form");
  let chatTimer = null, handCid = null;
  $("#handoff-stop").innerHTML = stopModes("stop_mode", "each");

  function chatCard(j) {
    const step = j.stage in STAGE_INDEX && !FINAL.includes(j.status) ? STAGE_INDEX[j.stage] + 1 : 0;
    const a = j.now && j.now.agent;
    let line;
    if (j.status === "done") line = `<span>做完了：</span>${strong(j.title)}`;
    else if (j.status === "cancelled") line = `<span>已经不做了：</span>${strong(j.title)}`;
    else line = (step ? `<span>第 ${step} 步：</span>` : "") + jobLine(j);
    const note = FINAL.includes(j.status) ? "" : j.waiting && j.mine ? "等你决定，点开看看。"
      : j.stage === "plan" ? "商量好了会在这里和项目页提醒你。点开看它们在说什么。" : "需要你决定时会在这里和项目页提醒你。点开看它们在做什么。";
    return `<a class="chat-job${j.waiting && j.mine ? " is-waiting" : ""}" href="#projects/${j.project_id}/${j.id}">
      <span class="cj-head"><span class="mark" aria-hidden="true"></span><span class="cj-title">已交给项目「${esc(j.project)}」</span>${ICON.next}</span>
      <span class="cj-now">${a ? avatar(a, "sm") : ""}<span class="cj-line">${line}</span></span>
      ${bar(j, false)}${note ? `<span class="cj-note">${note}</span>` : ""}</a>`;
  }
  async function chat() {
    clearTimeout(chatTimer);
    const cid = state.convId;
    if (!state.user || !state.user.can_code || !cid) { put(chatBox, ""); return; }
    if (state.view !== "chat") return;
    if (document.hidden) { chatTimer = setTimeout(chat, 4000); return; }
    try {
      const d = await api(`/api/jobs?conversation_id=${cid}`);
      if (cid !== state.convId) return;
      put(chatBox, d.jobs.map(chatCard).join(""));
      if (d.jobs.some(isLive)) chatTimer = setTimeout(chat, 4000);
    } catch { if (cid === state.convId) chatTimer = setTimeout(chat, 15000); }
  }

  $("#handoff-open").onclick = async () => {
    if (state.streaming) return toast("等 AI 说完，再交给项目");
    if (!state.convId) return toast("先在这里聊一聊要做什么，再交给项目");
    handCid = state.convId;
    const sel = hform.elements.project, why = $("#handoff-why");
    try {
      const [d, conv] = await Promise.all([api("/api/projects"), api(`/api/conversations/${handCid}`)]);
      if (!d.projects.length) return toast(state.user.admin ? "还没有项目。请先在「项目」页添加一个。" : "管理员还没有添加项目。");
      const reason = (p) => (!p.ready ? "现在用不了" : p.job && !p.job.mine ? "别人正在用" : p.job ? "还有一件没做完" : p.busy_other ? "别人正在用" : "");
      const last = localStorageGet("mao.handoff.project");
      sel.innerHTML = d.projects.map((p) => `<option value="${p.id}"${reason(p) ? " disabled" : ""}>${esc(p.name)}${reason(p) ? `（${reason(p)}）` : ""}</option>`).join("");
      const free = d.projects.filter((p) => !reason(p));
      const pick = free.find((p) => String(p.id) === last) || free[0];
      if (pick) sel.value = String(pick.id);
      why.hidden = !!free.length;
      why.textContent = free.length ? "" : "现在每个项目都在做别的事。等它们做完，或者先在项目页点「不做了」。";
      const said = conv.messages.filter((m) => m.role === "user" && m.content.trim().length >= 15);
      hform.elements.goal.value = said.length ? said[said.length - 1].content.trim() : "";
      hform.elements.with_chat.checked = true;
      $("#handoff-error").textContent = "";
      hform.querySelector('[type="submit"]').disabled = !free.length;
      hd.showModal(); hform.elements.goal.focus();
    } catch (err) { toast(err.message); }
  };
  hd.addEventListener("click", (e) => { if (e.target.closest("[data-close]")) hd.close(); });
  hform.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = hform.elements, btn = hform.querySelector('[type="submit"]');
    if (!f.goal.value.trim()) return f.goal.focus();
    btn.disabled = true; $("#handoff-error").textContent = "";
    try {
      await api("/api/jobs", { method: "POST", body: { project_id: Number(f.project.value), goal: f.goal.value.trim(),
        stop_mode: f.stop_mode.value, conversation_id: handCid, with_chat: f.with_chat.checked } });
      localStorageSet("mao.handoff.project", f.project.value);
      hd.close(); toast("已交给项目。商量好了会在这里提醒你。");
      chat(); refreshBadge();
    } catch (err) { $("#handoff-error").textContent = err.message; }
    finally { btn.disabled = false; }
  });
  document.addEventListener("visibilitychange", () => { if (!document.hidden && state.view === "chat" && state.convId) chat(); });

  return { load, poll, leave, polling, chat, openAdd, avatar };
})();
