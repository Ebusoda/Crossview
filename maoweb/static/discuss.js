"use strict";
// 讨论 (admin only): #discuss is the list, #discuss/new starts one, #discuss/DS-0003 is one discussion.
// GPT and Claude look at a requirement read-only, pick at each other's conclusions, and stop for the owner after
// each stage; once the owner approves, Cowork takes the brief from MAO over MCP. Boxes are drawn with put(), so open
// <details>, scroll position and the English overlay survive the polls. Model and owner text carries data-no-tr.
// Uses app.js helpers: $, api, esc, toast, ago, money, tokens, schedulePoll, setBadge, refreshBadge, state,
// localStorageGet, localStorageSet.

const MAODiscuss = (() => {
  const body = $("#discuss-body"), page = $("#view-discuss"), notice = $("#discuss-notice");
  const ACTIVE = ["queued", "running", "pausing"], RESUMABLE = ["paused", "interrupted", "failed"];
  const MODEL_ACTS = ["back_to_gpt", "claude_review", "one_more_round"];
  const OWNER_ACTS = [...MODEL_ACTS, "edit_requirement", "reject_plan", "approve"];
  const OWNER_ORDER = ["approve", "back_to_gpt", "claude_review", "one_more_round", "edit_requirement", "reject_plan"];
  const SIDES = ["gpt", "claude"];
  const DRAFT = "mao.discuss.draft";
  const ICON = {
    back: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M12.5 4.5L7 10l5.5 5.5"/></svg>',
    plus: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 4v12M4 10h12"/></svg>',
    pause: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M7.5 5v10M12.5 5v10"/></svg>',
    stop: '<svg viewBox="0 0 20 20" aria-hidden="true"><rect x="5.5" y="5.5" width="9" height="9" rx="1.5"/></svg>',
  };

  // ---- words for the codes the server sends -------------------------------------------------------------
  const NAME = { gpt: "GPT", claude: "Claude" };
  const STATUS_LABEL = { queued: "马上开始", running: "正在讨论", pausing: "正在暂停", waiting: "等你决定", paused: "已暂停",
    interrupted: "停在了中途", failed: "出错停下了", approved: "已批准", stopped: "已停止" };
  const STEP = { analysis: "独立分析", review: "评审", exchange: "交换意见", revision: "修订方案", ideate: "继续构思" };
  const FIRST = { gpt: "GPT 先说", claude: "Claude 先说" };
  const DEFAULT_SIDE = { gpt: "GPT 默认", claude: "Claude 默认" };
  const BRIEF = { approved: "等 Cowork 领取", taken: "Cowork 在做", done: "Cowork 报告完成" };
  // row errors (maoweb/discuss.py ERRORS); step_failed is worded from last_error
  const ERR = { worker_error: "后台进程出错了",
    restarted: "网页服务已重启。讨论停在了中途，点「继续」接着讨论；不会自动重复。",
    lost: "后台进程意外停止了。点「继续」接着讨论。" };
  // last_error.status (orch RunStatus values)
  const STATUS = { timeout: "超时", rate_limited: "额度用完", auth_error: "登录失效", unavailable: "命令不可用", crashed: "出错了",
    task_failed: "没完成", interrupted: "没完成" };
  // next_step.why (orch/discuss_record.py next_step), and the view's own: rejected, answered, changed
  const WHY = {
    answer_questions: "模型有问题要问你：先回答，再让它们接着讨论。",
    owner_decides: "两边的分歧需要你来定：改需求或加约束，说清楚要哪一种。",
    cap_reached: "已经到上限了：批准、改需求或停止。",
    tool_used: "有模型试着用了工具：先看看它的原始意见，再决定怎么做。",
    stale_plan: "方案是在你改需求之前写的：再来一轮，让它们按新的需求修订。",
    rejected_again: "新方案又用回了你否掉的做法：再来一轮，让它们避开。",
    unreadable: "有一方的回答读不出结构：再来一轮。",
    more_round: "还有重要分歧：再来一轮，让它们接着讨论。",
    stable: "方案已经稳定，没有重要分歧：可以批准，交给 Cowork。",
    review: "请 Claude 再评审一遍，看看有没有漏掉的地方。",
    rejected: "你否掉了方案：再来一轮，让它们换个做法。",
    answered: "你回答了问题：再来一轮，让它们按你的回答修订方案。",
    changed: "你改了需求：再来一轮，让它们按新的需求修订方案。",
  };
  const BLOCKING = ["rejected_reintroduced", "parse_failed", "cap_reached", "tool_call_seen", "stale_plan"];
  const WARN = {
    rejected_reintroduced: "又用回了你否掉的方案", parse_failed: "有一方的回答读不出结构",
    cap_reached: "到了轮数或调用次数的上限", tool_call_seen: "有模型试着用了工具（讨论是只读的）",
    stale_plan: "当前方案是在需求变更之前写的", over_limit: "有回答超出了字数上限", unverified_fact: "有说是事实的内容找不到来源",
    repaired: "有回答的格式不对，修过一次", rejected_touched: "提到了你否掉的方案", agree_not_proof: "两个模型都同意，不代表方案一定对",
    no_reason: "有意见没有给理由", prompt_large: "这次的提示很长，用的额度会多一些", stale_change: "有回答没看到最新的需求变更",
    auto_approved: "按设置自动批准了",
  };
  // capability reasons (orch/discuss_call.py) and the folder / codex refusals in last_error.error
  const REASON = {
    no_option: "订阅 CLI 没有这个选项", no_option_use_words: "订阅 CLI 没有这个选项；用每轮字数上限代替",
    cli_too_old: "这台电脑上的 claude 版本不支持 --effort，更新 Claude Code 后可用", cli_missing: "没找到这个命令",
    subscription_no_price: "订阅调用没有按量价格，只显示 token",
    estimate: "≈$ 是 Claude Code 自己估算的 API 价格，订阅不另收费",
    unsafe_cwd: "这个文件夹离 MAO 自己的数据太近，模型会读到别的讨论，所以没有开始",
    project_missing: "这个项目的文件夹不见了",
    codex_config_unreadable: "codex 的设置文件读不出来，没法确认已经关掉它的 MCP 工具，所以没有开始",
    mcp_name_unsupported: "codex 设置里有一个 MAO 关不掉的 MCP 工具，所以没有开始",
  };
  const ACT = { back_to_gpt: "继续让 GPT 构思", claude_review: "交给 Claude 评审", one_more_round: "再来一轮",
    edit_requirement: "改需求或加约束", approve: "批准，交给 Cowork", reject_plan: "否掉当前方案…" };
  const CONFIRM = { stop: "停止这场讨论？停止后不能再继续，记录会留着。", approve: "批准这个方案，交给 Cowork 开发？",
    reject_plan: "否掉当前方案？它们会避开这个方案重新想。" };
  const DONE = { pause: "正在暂停…", resume: "好的，接着讨论", stop: "已停止", back_to_gpt: "已经交给 GPT",
    claude_review: "已经交给 Claude 评审", one_more_round: "好的，再来一轮", edit_requirement: "已记下",
    answer: "已记下你的回答", approve: "已批准，等 Cowork 领取", reject_plan: "已否掉这个方案" };
  const NEED = { answer: "请写下你的回答", edit_requirement: "请写下要改的需求或约束", reject_plan: "请写下否掉的原因" };
  const PH = { answer: "写下你的回答", edit_requirement: "写下要改的需求或新的约束", note: "可以补充一句意见，也可以不写",
    more: "要改需求或加约束，写在这里" };
  const LEAD = { paused: "讨论已暂停。点「继续」接着讨论。", interrupted: "讨论停在了中途。点「继续」接着讨论。",
    failed: "这一步出错了。点「继续」重做这一步；也可以先改需求。" };
  const CHANGE = { change: "需求变更", constraint: "新约束", answer: "回答" };
  const EVENT = { back_to_gpt: "继续让 GPT 构思", claude_review: "交给 Claude 评审", one_more_round: "再来一轮",
    reject_plan: "否掉了当前方案", approve: "批准，交给 Cowork" };
  const SYS = { pause: "暂停了讨论", resume: "接着讨论", stop: "停止了讨论" };
  const SEV = { major: "重要", minor: "次要" };
  const OTHER = { silent: "没有回应", agrees: "同意", disagrees: "不同意" };
  const OPEN_RAW = { gpt: "展开 GPT 的原始意见", claude: "展开 Claude 的原始意见" };
  const KIND = { fact: "事实（MAO 记录）", assumption: "假设", model_suggestion: "模型建议", owner_decision: "你的决定" };
  const SECTION = { requirement_changes: "需求变更与约束", approved_decisions: "已批准决策", rejected_plans: "被否方案（及原因）",
    open_questions: "待解决问题", notes: "事实与假设", review_records: "模型评审记录", cowork_feedback: "Cowork 反馈",
    earlier: "同一项目以前的讨论" };
  const RECORD_ORDER = ["requirement_changes", "plans", "approved_decisions", "rejected_plans", "open_questions", "notes",
    "review_records", "cowork_feedback", "earlier"];
  const PLAN_ST = { current: "当前", superseded: "已替换", rejected: "被否", approved: "已批准" };
  const REPORT = { progress: "进展", blocker: "受阻", question: "问题", done: "完成" };
  const QUOTA = { five_hour: "5 小时", seven_day: "7 天" };
  const CHECKS = [["pause_after_each_stage", "每个阶段后暂停等我"], ["independent_review", "先各自独立分析"],
    ["converge", "最后合并成一个方案"], ["wait_for_approval", "结束时等我批准"]];
  const LISTS = [["agree", "同意的地方"], ["disagree", "不同意的地方"], ["risks", "风险"], ["assumptions", "假设"],
    ["questions_for_owner", "问你的问题"], ["changes", "改动"], ["facts", "说是事实的"]];

  let route = { kind: "" }, opts = null, live = null, sig = "", tab = "talk", draft = "", kind = "change", busy = false;
  let raws = {}, loading = new Set(), lastStatus = "", failing = false;

  // ---- small pieces ---------------------------------------------------------------------------------------
  // replace a box's content only when it changed: keeps scroll, focus, open <details> and the English overlay
  function put(el, html) {
    if (!el || el._src === html) return false;
    const open = new Set([...el.querySelectorAll("details[open][data-k]")].map((d) => d.dataset.k));
    el._src = html; el.innerHTML = html;
    el.querySelectorAll("details[data-k]").forEach((d) => { if (open.has(d.dataset.k)) d.open = true; });
    return true;
  }
  const str = (x) => String(x == null ? "" : x);
  const nt = (s) => `<span data-no-tr>${esc(str(s))}</span>`;                   // what people and models wrote
  const sep = '<span class="sep" aria-hidden="true"> · </span>';
  const parts = (list) => list.filter(Boolean).join(sep);
  const av = (side, cls = "") => `<span class="av av-${side === "gpt" ? "openai" : "anthropic"}${cls ? " " + cls : ""}" aria-hidden="true">${side === "gpt" ? "G" : "C"}</span>`;
  const avWho = (by) => by === "auto" || by === "MAO" ? '<span class="av av-mao" aria-hidden="true"><span class="mark"></span></span>'
    : `<span class="av av-user" aria-hidden="true" data-no-tr>${esc(str(by || "?").slice(0, 1).toUpperCase())}</span>`;
  const whoName = (by) => by === "auto" || by === "MAO" ? "<span>MAO</span>" : NAME[by] ? `<span>${NAME[by]}</span>` : nt(by || "—");
  const usd = (x) => (x == null ? "$ —" : money(x));
  const stLabel = (s) => `<span class="dsc-st st-${esc(str(s))}">${STATUS_LABEL[s] || esc(str(s))}</span>`;
  const tag = (text, cls = "") => `<span class="dsc-tag${cls ? " " + cls : ""}">${text}</span>`;
  function secs(s) {
    s = Math.max(0, Math.round(Number(s) || 0));
    if (s < 60) return `${s} 秒`;
    const m = Math.floor(s / 60);
    return m < 60 ? `${m} 分钟` : `${Math.floor(m / 60)} 小时 ${m % 60} 分钟`;
  }
  function day(ts) { const d = new Date(ts * 1000); return `${d.getMonth() + 1} 月 ${d.getDate()} 日`; }
  function clock(ts) {
    const d = new Date(ts * 1000), hm = `${d.getHours()}:${String(d.getMinutes()).padStart(2, "0")}`;
    return d.toDateString() === new Date().toDateString() ? hm : `${day(ts)} ${hm}`;
  }
  function showNotice(text) { notice.textContent = text; notice.hidden = !text; }
  function countWaiting(rows) {
    if (!state.user) return;
    state.user.discuss_waiting = rows.filter((r) => r.status === "waiting").length;
    setBadge(state.user.waiting, state.user.discuss_waiting);
  }

  // ---- the list (#discuss) ----------------------------------------------------------------------------------
  function cardHtml(r) {
    const meta = parts([nt(r.did), r.first ? `<span>${FIRST[r.first] || ""}</span>` : "", r.round ? `<span>第 ${r.round} 轮</span>` : "",
      r.project ? nt(r.project) : "<span>不关联项目</span>", r.brief_state ? `<span>${BRIEF[r.brief_state] || ""}</span>` : "",
      r.updated_at ? `<span>${ago(r.updated_at)}</span>` : ""]);
    return `<a class="dsc-card" data-did="${esc(r.did)}" href="#discuss/${esc(r.did)}">
      <span class="dsc-card-top"><strong class="dsc-card-t" data-no-tr>${esc(r.title || r.did)}</strong>${stLabel(r.status)}</span>
      <span class="dsc-card-m">${meta}</span></a>`;
  }
  async function loadList() {
    let d;
    try { d = await api("/api/discussions"); }
    catch (err) {
      if (route.kind !== "list") return;
      showNotice(err.message); schedulePoll("discuss", true, 10000); return;
    }
    if (route.kind !== "list") return;
    showNotice("");
    const rows = d.discussions || [];
    countWaiting(rows);
    put(body, `<div class="dsc-list-head"><a class="btn primary" id="discuss-new" href="#discuss/new">${ICON.plus}<span>新讨论</span></a></div>
      ${rows.length ? `<div class="dsc-list">${rows.map(cardHtml).join("")}</div>`
        : '<p class="empty">还没有讨论。写下想法，让 GPT 和 Claude 先讨论。</p>'}`);
    schedulePoll("discuss", true, 10000);
  }

  // ---- the start form (#discuss/new) ------------------------------------------------------------------------
  function costLine(side, x) {
    if (side === "gpt" || x == null) return "<span>$ —</span>";
    return `<span>≈${money(x)}（估算）</span>`;
  }
  function offRow(label, reason, control) {
    return `<div class="dsc-param off"><span class="dsc-pl">${label}</span>${control}<span class="why">${REASON[reason] || esc(str(reason))}</span></div>`;
  }
  function sideCard(side) {
    const cap = (opts.sides || {})[side] || {}, p = cap.params || {}, def = (opts.defaults.sides || {})[side] || {};
    const u = (opts.usage_7d || {})[side] || {}, lim = opts.limits.words || [100, 2000];
    const head = side === "gpt" ? "GPT · codex（ChatGPT 订阅）" : "Claude · claude（Max 订阅）";
    const found = cap.found ? `<p class="dsc-found ok"><span>已找到 ${esc(str(cap.version || cap.cli))}</span></p>`
      : `<p class="dsc-found bad"><span>没找到 ${esc(str(cap.cli || (side === "gpt" ? "codex" : "claude")))} 命令</span></p>`;
    const use = `<p class="dsc-use7"><span>最近 7 天：</span>${parts([`<span>${u.calls || 0} 次调用</span>`,
      `<span>输入 ${tokens(u.tokens_in || 0)} / 输出 ${tokens(u.tokens_out || 0)} token</span>`, costLine(side, u.est_cost_usd)])}</p>`;
    const m = p.model || {}, e = p.effort || {};
    const model = m.applies
      ? `<label class="dsc-param"><span class="dsc-pl">模型</span><input name="model" list="dsc-models-${side}" maxlength="64" autocomplete="off"
          spellcheck="false" placeholder="空 = CLI 默认" value="${esc(str(def.model))}"><datalist id="dsc-models-${side}">${(m.suggest || [])
          .map((s) => `<option value="${esc(str(s))}"></option>`).join("")}</datalist></label>`
      : offRow("模型", m.reason, '<input name="model" disabled aria-label="模型" placeholder="—">');
    const efforts = ["", ...(e.choices || ["low", "medium", "high"])]
      .map((v) => `<option value="${esc(v)}"${v === (def.effort || "") ? " selected" : ""}>${v ? esc(v) : "CLI 默认"}</option>`).join("");
    const effort = e.applies ? `<label class="dsc-param"><span class="dsc-pl">推理强度</span><select name="effort">${efforts}</select></label>`
      : offRow("推理强度", e.reason, `<select name="effort" disabled aria-label="推理强度"><option value="">CLI 默认</option></select>`);
    const words = `<label class="dsc-param"><span class="dsc-pl">每轮字数上限</span><input type="number" name="words" inputmode="numeric"
        min="${lim[0]}" max="${lim[1]}" step="50" value="${esc(str(def.output_limit_words || 600))}"><span class="hint">MAO 在提示里要求，并标出超出的回答</span></label>`;
    const t = p.temperature || {}, mo = p.max_output_tokens || {}, c = p.cost_usd || {};
    const temp = t.applies ? "" : offRow("温度", t.reason, '<input name="temperature" disabled aria-label="温度" placeholder="—">');
    const maxOut = mo.applies ? "" : offRow("最大输出 token", mo.reason, '<input name="max_output_tokens" disabled aria-label="最大输出 token" placeholder="—">');
    const cost = c.applies
      ? `<div class="dsc-param"><span class="dsc-pl">费用</span><span class="dsc-pv">≈$</span><span class="hint">${REASON[c.reason] || ""}</span></div>`
      : offRow("费用", c.reason, '<input name="cost" disabled aria-label="费用" value="$ —">');
    return `<div class="dsc-side" data-side="${side}"><div class="dsc-side-h">${av(side)}<strong>${head}</strong></div>
      ${found}${use}${model}${effort}${words}${temp}${maxOut}${cost}</div>`;
  }
  function formHtml() {
    const o = opts, d = o.defaults || {}, ctl = d.controls || {}, lim = o.limits || {};
    const projects = (o.projects || []).map((p) => `<option value="${Number(p.id)}" data-no-tr>${esc(str(p.name))}</option>`).join("");
    const first = d.first === "claude" ? "claude" : "gpt";
    const radio = (v) => `<label><input type="radio" name="first" value="${v}"${v === first ? " checked" : ""}><span>${NAME[v]}</span></label>`;
    const rounds = [1, 2, 3, 4].map((n) => `<option value="${n}"${n === Number(ctl.max_rounds || 2) ? " selected" : ""}>${n}</option>`).join("");
    const checks = CHECKS.map(([k, label]) => `<label class="dsc-check"><input type="checkbox" name="${k}"${ctl[k] ? " checked" : ""}><span>${label}</span></label>`).join("");
    return `<a class="back-link" href="#discuss">${ICON.back}<span>讨论</span></a>
      <form class="create-card dsc-form" id="discuss-form" autocomplete="off" novalidate>
        <label class="dsc-lbl" for="discuss-req">要讨论什么</label>
        <textarea id="discuss-req" name="requirement" rows="6" maxlength="${Number(lim.requirement) || 8000}"
          placeholder="写下想法或需求：要做什么、给谁用、有什么限制。GPT 和 Claude 会先各自分析，再互相挑错。"></textarea>
        <div class="dsc-count"><span id="discuss-count">0 / ${Number(lim.requirement) || 8000}</span></div>
        <div class="dsc-row">
          <div class="dsc-field"><label class="field-label" for="discuss-project">关联项目</label>
            <select name="project_id" id="discuss-project"><option value="0">不关联项目</option>${projects}</select>
            <button type="button" class="dsc-link" data-link="toggle" aria-expanded="false">登记文件夹</button>
            <div class="dsc-linkrow" id="discuss-link" hidden>
              <div class="row"><input name="link_path" spellcheck="false" aria-label="项目文件夹的完整路径"
                placeholder="项目文件夹的完整路径，例如 D:\\work\\my-app"><button type="button" class="ghost" data-link="save">登记</button></div>
              <p class="error" id="discuss-link-error" role="alert"></p></div></div>
          <div class="dsc-field"><span class="field-label" id="discuss-first-l">谁先发言</span>
            <div class="seg" role="radiogroup" aria-labelledby="discuss-first-l">${radio("gpt")}${radio("claude")}</div>
            <span class="small-note">第一个发言的一方也负责修订方案</span></div>
        </div>
        <details class="dsc-settings"><summary id="discuss-sum"></summary>
          <div class="dsc-sides">${sideCard("gpt")}${sideCard("claude")}</div>
          <fieldset class="dsc-controls"><legend>讨论方式</legend>
            <label class="dsc-param"><span class="dsc-pl">自动轮数</span><select name="max_rounds">${rounds}</select>
              <span class="hint">到了就停下来等你</span></label>
            ${checks}
            <div class="dsc-param off"><span class="dsc-pl">第三方裁判</span><input name="arbiter" disabled aria-label="第三方裁判" placeholder="—">
              <span class="why">这一版没有</span></div>
          </fieldset>
        </details>
        ${o.ready ? "" : `<p class="notice bad-notice dsc-problem">${esc(str(o.problem))}</p>`}
        <p class="error" id="discuss-error" role="alert"></p>
        <div class="create-row"><span class="small-note dsc-ro">模型只读：看得到项目文件，改不了任何东西</span><span class="spacer"></span>
          <button type="submit" class="primary go" id="discuss-start"${o.ready ? "" : " disabled"}>开始讨论</button></div>
      </form>`;
  }
  const field = (box, name) => { const el = box && box.querySelector(`[name="${name}"]`); return el && !el.disabled ? el.value.trim() : ""; };
  function sumHtml(f) {
    const side = (s) => {
      const box = f.querySelector(`.dsc-side[data-side="${s}"]`), v = [field(box, "model"), field(box, "effort")].filter(Boolean).join(" ");
      return v ? `<span>${NAME[s]} ${esc(v)}</span>` : `<span>${DEFAULT_SIDE[s]}</span>`;
    };
    return `<span class="dsc-sum-l">设置：</span>${parts([`<span>自动 ${esc(f.elements.max_rounds.value)} 轮</span>`,
      `<span>${f.elements.pause_after_each_stage.checked ? "每阶段暂停" : "阶段之间不暂停"}</span>`, side("gpt"), side("claude")])}`;
  }
  function count() {
    const f = $("#discuss-form"), c = $("#discuss-count");
    if (f && c) c.textContent = `${f.elements.requirement.value.length} / ${f.elements.requirement.maxLength}`;
  }
  async function showForm() {
    schedulePoll("discuss", false);
    try { opts = await api("/api/discuss/options"); }
    catch (err) { if (route.kind === "new") showNotice(err.message); return; }
    if (route.kind !== "new") return;
    showNotice("");
    body._src = null; body.innerHTML = formHtml();
    const f = $("#discuss-form");
    f.elements.requirement.value = localStorageGet(DRAFT) || "";
    count();
    $("#discuss-sum").innerHTML = sumHtml(f);
  }
  // 登记文件夹: a folder for 关联项目 without the old dispatch's preparation (no git, nothing written into it)
  async function link(what) {
    const f = $("#discuss-form"), row = $("#discuss-link"), err = $("#discuss-link-error");
    if (!f || !row) return;
    if (what === "toggle") {
      row.hidden = !row.hidden;
      f.querySelector('[data-link="toggle"]').setAttribute("aria-expanded", String(!row.hidden));
      if (!row.hidden) f.elements.link_path.focus();
      return;
    }
    const path = f.elements.link_path.value.trim();
    if (!path) { f.elements.link_path.focus(); return; }
    const btn = f.querySelector('[data-link="save"]');
    btn.disabled = true; err.textContent = "";
    try {
      const d = await api("/api/admin/projects/link", { method: "POST", body: { path } });
      const o = document.createElement("option"), sel = f.elements.project_id;
      o.value = String(Number(d.project.id)); o.textContent = str(d.project.name); o.setAttribute("data-no-tr", "");
      sel.appendChild(o); sel.value = o.value;
      if (opts) opts.projects = [...(opts.projects || []), { id: d.project.id, name: d.project.name }];
      f.elements.link_path.value = ""; row.hidden = true;
      f.querySelector('[data-link="toggle"]').setAttribute("aria-expanded", "false");
      toast("已登记这个文件夹");
    } catch (x) { err.textContent = x.message; }
    finally { btn.disabled = false; }
  }
  async function submit(e) {
    e.preventDefault();
    const f = e.target, err = $("#discuss-error"), btn = $("#discuss-start");
    const requirement = f.elements.requirement.value.trim();
    if (!requirement) { err.textContent = "请写清楚要讨论什么"; f.elements.requirement.focus(); return; }
    const sides = {};
    for (const s of SIDES) {
      const box = f.querySelector(`.dsc-side[data-side="${s}"]`), w = field(box, "words");
      sides[s] = { model: field(box, "model"), effort: field(box, "effort"), output_limit_words: w === "" ? "" : Number(w) };
    }
    const controls = { max_rounds: Number(f.elements.max_rounds.value), arbiter: "off" };
    for (const [k] of CHECKS) controls[k] = f.elements[k].checked;
    btn.disabled = true; err.textContent = "";
    try {
      const d = await api("/api/discussions", { method: "POST", body: { requirement, project_id: Number(f.elements.project_id.value) || 0,
        first: (f.querySelector("input[name=first]:checked") || {}).value || "gpt", sides, controls } });
      localStorageSet(DRAFT, "");
      location.hash = `#discuss/${d.did}`;
    } catch (x) { err.textContent = x.message; btn.disabled = !(opts && opts.ready); }
  }

  // ---- one discussion (#discuss/DS-0003) ---------------------------------------------------------------------
  function headHtml() {
    const v = live, acts = v.actions || [], dis = busy ? " disabled" : "";
    const btn = (a, label, icon, cls = "") => (acts.includes(a)
      ? `<button type="button" class="dsc-hbtn${cls}" data-act="${a}"${dis}>${icon}<span>${label}</span></button>` : "");
    const meta = parts([nt(v.did), v.project ? nt(v.project.name) : "<span>不关联项目</span>", `<span>第 ${v.round} 轮</span>`,
      `<span>调用 ${v.calls} / ${(v.caps || {}).calls || 40} 次</span>`]);
    return `<div class="dsc-head"><div class="dsc-head-t"><h2 class="dsc-title" data-no-tr>${esc(str(v.title || v.did))}</h2>
        <div class="dsc-meta">${stLabel(v.status)}<span class="dsc-meta-p">${meta}</span></div></div>
      <div class="dsc-hacts">${btn("pause", "暂停", ICON.pause)}${btn("stop", "停止", ICON.stop, " danger")}</div></div>`;
  }
  function quotaHtml(q) {
    if (!q || typeof q !== "object") return "";
    return Object.keys(QUOTA).filter((k) => q[k] && typeof q[k].utilization === "number").map((k) => {
      const pct = Math.round(q[k].utilization * 100);                         // always a fraction, as in app.js
      return `<span>${QUOTA[k]}窗口已用 ${pct}%</span>`;
    }).join(sep);
  }
  function usageHtml() {
    const u = live.usage || {};
    return `<div class="dsc-usage">${SIDES.map((s) => {
      const x = u[s] || {};
      const cost = s === "gpt" ? "<span>$ —（订阅没有价格）</span>" : costLine(s, x.est_cost_usd);
      return `<div class="dsc-use" data-side="${s}">${av(s, "sm")}<span class="dsc-use-b">${parts([`<strong>${NAME[s]}</strong>`,
        `<span>${x.calls || 0} 次调用</span>`, `<span>输入 ${tokens(x.tokens_in || 0)} / 输出 ${tokens(x.tokens_out || 0)} token</span>`,
        cost, quotaHtml(x.quota)])}</span></div>`;
    }).join("")}</div>`;
  }
  function reqHtml() {
    const v = live, text = str(v.requirement), lines = text.split("\n");
    const by = ((v.events || []).find((e) => e.action === "create") || {}).by || "";
    const head = `<div class="dsc-th">${avWho(by)}<strong>你的需求</strong>${v.created_at ? `${sep}<span>${ago(v.created_at)}</span>` : ""}</div>`;
    if (lines.length <= 6 && text.length <= 600) return `<div class="dsc-req">${head}<p class="dsc-reqt" data-no-tr>${esc(text)}</p></div>`;
    return `<div class="dsc-req">${head}<p class="dsc-reqt clip" data-no-tr>${esc(lines.slice(0, 6).join("\n").slice(0, 600))}…</p>
      <details class="dsc-reqmore" data-k="req"><summary>看完整需求</summary><p class="dsc-reqt" data-no-tr>${esc(text)}</p></details></div>`;
  }
  function carriedHtml() {
    const list = ((live.record || {}).sections || {}).earlier || [];
    if (!live.carried || !list.length) return "";
    return `<details class="dsc-carried" data-k="carried"><summary>同一项目以前的讨论</summary>
      <ul class="dsc-entries">${list.map(entryHtml).join("")}</ul></details>`;
  }
  function turnBadges(t) {
    const codes = new Set((t.warnings || []).map((w) => w.code)), out = [];
    if (t.status === "parse_failed") out.push(tag("读不出结构", "bad"));
    if (t.repaired || codes.has("repaired")) out.push(tag("格式已修复"));
    if (t.over_limit || codes.has("over_limit")) out.push(tag("超出字数", "warn"));
    if (codes.has("stale_change")) out.push(tag("没看到最新的需求变更", "warn"));
    if ((t.tool_calls || []).length || codes.has("tool_call_seen")) out.push(tag("调用了工具", "bad"));
    if (codes.has("rejected_reintroduced")) out.push(tag("又用回了被否掉的方案", "bad"));
    return out.join("");
  }
  function itemHtml(key, x) {
    if (typeof x === "string") return `<li>${nt(x)}</li>`;
    if (!x || typeof x !== "object") return "";
    if (key === "risks") return `<li>${nt(x.risk)}${x.mitigation ? `<span class="dsc-sub"><span>应对：</span>${nt(x.mitigation)}</span>` : ""}</li>`;
    if (key === "facts") return `<li>${nt(x.text)}${x.source ? ` <code data-no-tr>${esc(str(x.source))}</code>` : ""}</li>`;
    const sev = key === "disagree" && SEV[x.severity] ? ` ${tag(SEV[x.severity], x.severity === "major" ? "bad" : "")}` : "";
    return `<li>${nt(x.point)}${sev}${x.reason ? `<span class="dsc-sub"><span>理由：</span>${nt(x.reason)}</span>` : ""}</li>`;
  }
  function rawHtml(t) {
    const r = raws[t.id];
    if (!r) return '<p class="dsc-muted"><span class="spinner small" aria-hidden="true"></span><span>正在读取原始回答…</span></p>';
    if (r.error) return `<p class="error">${esc(r.error)}</p>`;
    const one = (label, x) => `<span class="dsc-lt">${label}</span><pre class="dsc-rawtext" data-no-tr>${esc(str(x.text))}</pre>
      ${x.clipped ? '<p class="dsc-muted">（已截断）</p>' : ""}`;
    return one("模型的原始回答", r) + (r.repair ? one("修格式那一次的回答", r.repair) : "");
  }
  function turnHtml(t) {
    const o = t.output || {}, plan = o.plan || {};
    const said = t.status === "ok" && plan.summary ? `<p class="dsc-said" data-no-tr>${esc(str(plan.summary))}</p>`
      : '<p class="dsc-muted">这一步的回答读不出结构，可以展开原始意见看看。</p>';
    const steps = (plan.steps || []).length ? `<span class="dsc-lt">步骤</span><ol>${plan.steps.map((s) => `<li>${nt(s)}</li>`).join("")}</ol>` : "";
    const accept = (plan.acceptance || []).length ? `<span class="dsc-lt">验收标准</span><ul>${plan.acceptance.map((s) => `<li>${nt(s)}</li>`).join("")}</ul>` : "";
    const lists = LISTS.filter(([k]) => (o[k] || []).length)
      .map(([k, label]) => `<span class="dsc-lt">${label}</span><ul>${o[k].map((x) => itemHtml(k, x)).join("")}</ul>`).join("");
    return `<article class="dsc-turn" data-side="${esc(t.side)}" data-step="${esc(t.step)}" data-turn="${esc(t.id)}">
      <div class="dsc-th">${av(t.side)}${parts([`<strong>${NAME[t.side] || esc(t.side)}</strong>`, `<span>${STEP[t.step] || esc(t.step)}</span>`,
        `<span>第 ${t.round} 轮</span>`, t.seconds != null ? `<span>${secs(t.seconds)}</span>` : ""])}${turnBadges(t)}</div>
      ${said}
      <details class="dsc-raw" data-k="raw-${esc(t.id)}" data-turn="${esc(t.id)}" id="dsc-raw-${esc(t.id)}"><summary>原始意见</summary>
        <div class="dsc-lists">${steps}${accept}${lists}</div>${rawHtml(t)}</details></article>`;
  }
  function planBlock(plan) {
    if (!plan) return '<p class="dsc-muted">还没有方案</p>';
    return `<p class="dsc-plan-s" data-no-tr>${esc(str(plan.summary))}</p>
      ${(plan.steps || []).length ? `<ol>${plan.steps.map((s) => `<li>${nt(s)}</li>`).join("")}</ol>` : ""}
      ${(plan.acceptance || []).length ? `<span class="dsc-lt">验收标准</span><ul>${plan.acceptance.map((s) => `<li>${nt(s)}</li>`).join("")}</ul>` : ""}`;
  }
  function stageHtml(s) {
    const sec = (title, inner) => (inner ? `<div class="dsc-sec"><h4>${title}</h4>${inner}</div>` : "");
    const ul = (list, fn) => ((list || []).length ? `<ul>${list.map(fn).join("")}</ul>` : "");
    const reasons = (r) => SIDES.filter((x) => r && r[x]).map((x) => `<span class="dsc-sub"><b>${NAME[x]}</b> ${nt(r[x])}</span>`).join("");
    const title = s.kind === "ideate" ? "<span>GPT 继续构思后的小结</span>" : s.kind === "review" ? "<span>Claude 评审后的小结</span>"
      : `<span>第 ${s.round} 轮小结</span>`;
    const plan = s.current_plan;
    const consensus = ul(s.consensus, (c) => `<li>${nt(c.point)} ${tag(c.sides === "both" ? "双方" : "只有一方提到", c.sides === "both" ? "ok" : "")}${reasons(c.reasons)}</li>`);
    const dis = ul(s.disagreements, (x) => {
      const o = x.by === "gpt" ? "claude" : "gpt";
      return `<li>${nt(x.point)} ${tag(SEV[x.severity] || esc(str(x.severity)), x.severity === "major" ? "bad" : "")}${x.persistent ? tag("持续分歧", "bad") : ""}
        <span class="dsc-sub"><b>${NAME[x.by] || esc(str(x.by))}</b> ${x.reason ? nt(x.reason) : "<span>—</span>"}</span>
        <span class="dsc-sub"><b>${NAME[o]}</b> ${tag(OTHER[x.other] || esc(str(x.other)))}${x.other_reason ? " " + nt(x.other_reason) : ""}</span></li>`;
    });
    const risks = ul(s.risks, (r) => `<li>${nt(r.risk)}${r.mitigation ? `<span class="dsc-sub"><span>应对：</span>${nt(r.mitigation)}</span>` : ""}</li>`);
    const qs = ul(s.questions_for_owner, (q) => `<li>${nt(q.text)} ${tag(NAME[q.by] || esc(str(q.by)))}</li>`);
    const ns = s.next_step || {};
    const next = ns.action ? `<p class="dsc-next"><strong>${ACT[ns.action] || esc(ns.action)}</strong>${WHY[ns.why] ? `<span>${WHY[ns.why]}</span>` : ""}</p>` : "";
    const ws = (s.warnings || []).slice().sort((a, b) => BLOCKING.includes(b.code) - BLOCKING.includes(a.code));
    const warns = ul(ws, (w) => `<li class="dsc-w${BLOCKING.includes(w.code) ? " bad" : ""}">${w.side && NAME[w.side] ? `<b>${NAME[w.side]}</b> ` : ""}<span>${WARN[w.code] || esc(str(w.code))}</span></li>`);
    const opens = SIDES.filter((x) => s.sides && s.sides[x] && s.sides[x].turn)
      .map((x) => `<button type="button" class="dsc-link" data-open="${esc(s.sides[x].turn)}">${OPEN_RAW[x]}</button>`).join("");
    return `<section class="dsc-stage" data-n="${Number(s.n)}">
      <div class="dsc-stage-h">${title}${plan ? `${sep}<span>方案 v${Number(plan.v)}</span>` : ""}</div>
      ${sec("当前方案", planBlock(plan))}${sec("共识", consensus)}${sec("未解决分歧", dis)}${sec("主要风险", risks)}
      ${sec("需要你决定的问题", qs)}${sec("建议下一步", next)}${warns ? `<div class="dsc-sec dsc-warns">${warns}</div>` : ""}
      ${opens ? `<div class="dsc-opens">${opens}</div>` : ""}</section>`;
  }
  function eventHtml(e) {
    if (SYS[e.action]) return `<p class="dsc-sys">${whoName(e.by)} <span>${SYS[e.action]}</span>${sep}<span>${ago(e.at)}</span></p>`;
    const label = e.action === "edit_requirement" ? CHANGE[e.kind] || CHANGE.change
      : e.action === "approve" && e.by === "auto" ? "按设置自动批准" : EVENT[e.action];
    if (!label) return "";
    return `<div class="dsc-ev"><div class="dsc-th">${avWho(e.by)}${parts([whoName(e.by), `<strong>${label}</strong>`, `<span>${ago(e.at)}</span>`])}</div>
      ${e.text ? `<p class="dsc-evt" data-no-tr>${esc(str(e.text))}</p>` : ""}</div>`;
  }
  function talkHtml() {
    const v = live, items = [], start = {};
    for (const t of v.turns || []) start[t.group] = Math.min(start[t.group] ?? Infinity, Number(t.started_at || t.ended_at || 0));
    for (const t of v.turns || []) items.push([start[t.group] + Number(String(t.id).slice(1)) * 1e-6, turnHtml(t)]);
    for (const s of v.stages || []) items.push([Number(s.at || 0), stageHtml(s)]);
    for (const e of v.events || []) if (e.action !== "create") items.push([Number(e.at || 0), eventHtml(e)]);
    items.sort((a, b) => a[0] - b[0]);
    return reqHtml() + carriedHtml() + `<div class="dsc-tl">${items.map((x) => x[1]).join("")}</div>`;
  }
  function pendHtml() {
    const v = live, now = Date.now() / 1000;
    const pend = (v.running || []).map((r) => `<article class="dsc-turn pending" data-side="${esc(r.side)}" data-step="${esc(r.step)}">
      <div class="dsc-th">${av(r.side, "pulse")}<strong>${NAME[r.side] || esc(str(r.side))}</strong></div>
      <p class="dsc-pend"><span>${NAME[r.side] || "GPT"} 正在${STEP[r.step] || "讨论"}…</span>${r.since ? `${sep}<span>已经 ${secs(now - r.since)}</span>` : ""}</p></article>`).join("");
    if (pend) return pend;
    if (v.status === "queued" || (v.status === "running" && v.active)) return '<p class="dsc-sys"><span class="spinner small" aria-hidden="true"></span><span>马上开始…</span></p>';
    if (v.status === "pausing") return '<p class="dsc-sys"><span class="spinner small" aria-hidden="true"></span><span>正在暂停，等这一步停下来…</span></p>';
    if (v.status === "approved") return '<p class="dsc-end"><span>已批准。Cowork 领取以后的进展在「决策记录」里。</span><button type="button" class="dsc-link" data-tab="record">看决策记录</button></p>';
    if (v.status === "stopped") return '<p class="dsc-end"><span>这场讨论已停止，记录会留着。</span></p>';
    return "";
  }
  function stepFailed(le) {
    const head = `<p><span>${NAME[le.side] || "GPT"} 在 ${esc(str(le.step || "?"))} 失败了：${STATUS[le.status] || "出错了"}</span></p>`;
    const detail = REASON[le.error] ? `<p><span>${REASON[le.error]}</span></p>`
      : le.error ? `<p><code data-no-tr>${esc(str(le.error).slice(0, 300))}</code></p>` : "";
    const reset = le.resets_at ? `<p><span>额度会在 ${clock(le.resets_at)} 恢复</span></p>` : "";
    return head + detail + reset;
  }
  function errHtml() {
    const v = live, le = v.last_error;
    let msg = "";
    if (v.error === "step_failed" && le) msg = stepFailed(le);
    else if (ERR[v.error]) msg = `<p><span>${ERR[v.error]}</span></p>`;
    else if (le && RESUMABLE.includes(v.status)) msg = stepFailed(le);
    return msg ? `<div class="notice bad-notice dsc-err" role="alert">${msg}</div>` : "";
  }
  function ownerHtml() {
    const v = live, acts = v.actions || [], dis = busy ? " disabled" : "";
    const btn = (a, cls, label = ACT[a], data = "") => `<button type="button" class="btn ${cls}" data-act="${a}"${data}${dis}>${label}</button>`;
    const ta = (ph) => `<textarea id="discuss-text" rows="3" maxlength="${(opts && opts.limits && opts.limits.text) || 4000}" placeholder="${ph}"></textarea>`;
    const kindSel = `<label class="dsc-kind"><span class="field-label">这是</span><select id="discuss-kind">
      <option value="change">需求变更</option><option value="constraint">新约束</option></select></label>`;
    const more = (list, withText) => (list.length ? `<details class="dsc-more" data-k="more"><summary>其他选择</summary><div class="dsc-more-b">
      ${withText ? ta(PH.more) : ""}${list.map((a) => (a === "edit_requirement" ? `<div class="dsc-edit">${kindSel}${btn(a, "ghost")}</div>`
        : btn(a, a === "reject_plan" ? "ghost danger" : "ghost"))).join("")}</div></details>` : "");
    const wrap = (inner) => `<section class="dsc-owner" id="discuss-owner">${inner}<p class="error" id="discuss-act-error" role="alert"></p></section>`;
    if (RESUMABLE.includes(v.status) && acts.includes("resume"))
      return wrap(`<p class="dsc-lead"><span>${LEAD[v.status]}</span></p><div class="dsc-obtns">${btn("resume", "primary", "继续")}</div>
        ${more(acts.filter((a) => a === "edit_requirement"), true)}`);
    if (v.status !== "waiting" || !acts.some((a) => OWNER_ACTS.includes(a))) return "";
    const q = v.first_question;
    if (q) {
      return wrap(`<p class="dsc-lead"><span>${NAME[q.by] || "GPT"} 问你：</span></p><blockquote class="dsc-q" data-no-tr>${esc(str(q.text))}</blockquote>
        ${ta(PH.answer)}<div class="dsc-obtns">${btn("edit_requirement", "primary", "回答这个问题", ` data-kind="answer" data-ref="${esc(str(q.id))}"`)}</div>
        ${more(OWNER_ORDER.filter((a) => acts.includes(a)), false)}`);
    }
    const rec = v.recommended && acts.includes(v.recommended) ? v.recommended : ["edit_requirement", "one_more_round"].find((a) => acts.includes(a));
    const why = WHY[v.why] ? `<p class="dsc-lead"><span>${WHY[v.why]}</span></p>` : "";
    return wrap(`${why}${ta(PH[rec] || PH.note)}${rec === "edit_requirement" ? kindSel : ""}<div class="dsc-obtns">${btn(rec, "primary")}</div>
      ${more(OWNER_ORDER.filter((a) => a !== rec && acts.includes(a)), false)}`);
  }

  // ---- 决策记录 -------------------------------------------------------------------------------------------
  function entryText(e) {
    const t = str(e.text);
    let m;
    if (e.section === "review_records" && (m = t.match(/^(S\d+) (GPT|Claude) (\S+) r(\d+): ([\s\S]*)$/)))
      return `${nt(m[1])} <b>${m[2]}</b> <span>${esc(m[3])}</span> <span>第 ${m[4]} 轮</span> ${m[5] === "（读不出结构）" ? "<span>（读不出结构）</span>" : nt(m[5])}`;
    if ((m = t.match(/^批准方案 v(\d+)：([\s\S]*?)(?:（备注：([\s\S]*)）)?$/)))
      return `<span>批准方案 v${m[1]}：</span>${nt(m[2])}${m[3] ? ` <span class="dsc-sub"><span>备注：</span>${nt(m[3])}</span>` : ""}`;
    if ((m = t.match(/^否掉方案 v(\d+)：([\s\S]*?)；原因：([\s\S]*)$/)))
      return `<span>否掉方案 v${m[1]}：</span>${nt(m[2])}<span class="dsc-sub"><span>原因：</span>${nt(m[3])}</span>`;
    if ((m = t.match(/^被否：([\s\S]*?)；原因：([\s\S]*)$/)))
      return `<span>被否：</span>${nt(m[1])}<span class="dsc-sub"><span>原因：</span>${nt(m[2])}</span>`;
    if ((m = t.match(/^回答「([\s\S]*?)」：([\s\S]*)$/)))
      return `${tag(CHANGE.answer)} <q class="dsc-q-in" data-no-tr>${esc(m[1])}</q> ${nt(m[2])}`;
    if ((m = t.match(/^(进展|受阻|问题|完成)：([\s\S]*)$/)) && e.section === "earlier") return `${tag(esc(m[1]))} ${nt(m[2])}`;
    if (e.by === "MAO") return `<span>${esc(t)}</span>`;                       // MAO's own words: 已被 X 领取 / 提交 … 存在
    return nt(t);
  }
  function entryHtml(e) {
    const meta = parts([`<span class="dsc-kind-b kind-${esc(str(e.kind))}">${KIND[e.kind] || esc(str(e.kind))}</span>`, whoName(e.by),
      e.at ? `<span>${day(e.at)}</span>` : "", e.ref && !/^S\d+$/.test(e.ref) ? nt(e.ref) : ""]);
    const ck = e.change_kind && CHANGE[e.change_kind] && e.change_kind !== "answer" ? tag(CHANGE[e.change_kind]) + " " : "";
    const rk = e.report_kind ? tag(REPORT[e.report_kind] || esc(str(e.report_kind))) + " " : "";
    const claim = e.claim === "fact" ? ` ${tag("自称事实")}${e.source_exists === true ? tag("来源存在", "ok") : e.source_exists === false ? tag("来源找不到", "bad") : ""}` : "";
    const ans = e.answered_by ? ` ${tag("已回答", "ok")}` : "";
    const src = e.source && !/^turns\//.test(e.source) ? ` <code data-no-tr>${esc(str(e.source))}</code>` : "";
    const open = e.section === "review_records" && /^S\d+$/.test(e.ref || "") ? `<button type="button" class="dsc-link" data-open="${esc(e.ref)}">看原始意见</button>` : "";
    return `<li class="dsc-entry"><div class="dsc-em">${meta}</div><div class="dsc-et">${ck}${rk}${entryText(e)}${claim}${ans}${src}</div>
      ${e.evidence ? `<pre class="dsc-rawtext" data-no-tr>${esc(str(e.evidence))}</pre>` : ""}${open}</li>`;
  }
  function plansHtml() {
    const ps = (live.plans || []).slice().reverse();
    const item = (p) => `<li class="dsc-pv" data-status="${esc(str(p.status))}"><div class="dsc-em">${tag(PLAN_ST[p.status] || esc(str(p.status)),
        p.status === "rejected" ? "bad" : p.status === "superseded" ? "" : "ok")}${parts([`<span>方案 v${Number(p.v)}</span>`,
        `<span>${NAME[p.by] || esc(str(p.by))}</span>`, `<span>第 ${Number(p.round)} 轮</span>`])}</div>
      ${p.status === "current" || p.status === "approved" ? planBlock(p) : `<p class="dsc-plan-s" data-no-tr>${esc(str(p.summary))}</p>`}</li>`;
    return `<section class="dsc-rsec" data-sec="plans"><h3>方案版本</h3>${ps.length ? `<ul class="dsc-plans">${ps.map(item).join("")}</ul>`
      : '<p class="dsc-muted">还没有方案</p>'}</section>`;
  }
  function briefHtml() {
    const b = live.brief;
    if (!b) return "";
    const head = b.state === "taken" ? `<strong>已被 ${esc(str(b.taken_by || "cowork"))} 领取（${b.taken_at ? ago(b.taken_at) : "刚刚"}）</strong>`
      : b.state === "done" ? '<strong>Cowork 报告完成</strong><span class="dsc-muted">MAO 只核对了提交是否存在</span>'
      : '<strong>等 Cowork 领取</strong>';
    const hint = b.state === "approved" ? `<p class="dsc-hint"><span>在 Cowork 里说：用 MAO 的 mao_get_brief 领取 ${esc(live.did)} 开始开发</span></p>` : "";
    const commit = (r) => (r.commit ? `<p class="dsc-commit"><code data-no-tr>${esc(str(r.commit).slice(0, 12))}</code> <span>${r.commit_exists === true ? "提交存在"
      : r.commit_exists === false ? "项目仓库里没有这个提交" : "没法核对这个提交"}</span></p>` : "");
    const reports = (b.reports || []).map((r) => `<li class="dsc-entry"><div class="dsc-em">${parts([tag(REPORT[r.kind] || esc(str(r.kind))), nt(r.by),
      r.at ? `<span>${ago(r.at)}</span>` : ""])}</div><p class="dsc-et" data-no-tr>${esc(str(r.text))}</p>
      ${r.evidence ? `<pre class="dsc-rawtext" data-no-tr>${esc(str(r.evidence))}</pre>` : ""}${commit(r)}</li>`).join("");
    return `<section class="dsc-brief" data-state="${esc(str(b.state))}"><div class="dsc-brief-h">${head}${b.auto ? tag("按设置自动批准") : ""}</div>
      ${hint}${reports ? `<ul class="dsc-entries">${reports}</ul>` : ""}</section>`;
  }
  function recordHtml() {
    const sec = (live.record || {}).sections || {};
    let out = briefHtml() + `<section class="dsc-rsec" data-sec="requirement"><h3>原始需求（原文）</h3><p class="dsc-reqt" data-no-tr>${esc(str(live.requirement))}</p></section>`;
    for (const key of RECORD_ORDER) {
      if (key === "plans") { out += plansHtml(); continue; }
      const list = sec[key] || [];
      out += `<section class="dsc-rsec" data-sec="${key}"><h3>${SECTION[key]}</h3>${list.length
        ? `<ul class="dsc-entries">${list.map(entryHtml).join("")}</ul>` : '<p class="dsc-muted">（无）</p>'}</section>`;
    }
    return `<div class="dsc-record">${out}</div>`;
  }

  // ---- drawing and loading one discussion -----------------------------------------------------------------
  function skeleton(did) {
    body._src = null;
    body.innerHTML = `<div class="dsc-live" data-did="${esc(did)}">
      <a class="back-link" href="#discuss">${ICON.back}<span>讨论</span></a>
      <div data-slot="head"></div><div data-slot="usage"></div>
      <div class="dsc-tabs" role="tablist"><button type="button" role="tab" data-tab="talk">对话</button>
        <button type="button" role="tab" data-tab="record">决策记录</button></div>
      <div class="dsc-pane" data-pane="talk"><div data-slot="talk"></div><div data-slot="pend"></div><div data-slot="err"></div>
        <div data-slot="owner"></div></div>
      <div class="dsc-pane" data-pane="record" hidden><div data-slot="record"></div></div>
      <p class="sr-only" aria-live="polite" data-slot="live"></p></div>`;
  }
  // tell a screen reader when the status changes (not on the first load): outside put(), so only transitions speak
  function announce() {
    const el = body.querySelector('[data-slot="live"]');
    if (!el || !live) return;
    const said = [STATUS_LABEL[live.status] || String(live.status)];
    if (live.status === "waiting" && WHY[live.why]) said.push(WHY[live.why]);
    // one span per sentence, so the English overlay translates each of them
    el.replaceChildren(...said.map((t, i) => { const sp = document.createElement("span"); sp.textContent = (i ? " " : "") + t; return sp; }));
  }
  function setTab(name) {
    tab = name === "record" ? "record" : "talk";
    body.querySelectorAll(".dsc-tabs [data-tab]").forEach((b) => { b.setAttribute("aria-selected", String(b.dataset.tab === tab)); b.classList.toggle("on", b.dataset.tab === tab); });
    body.querySelectorAll(".dsc-pane").forEach((p) => { p.hidden = p.dataset.pane !== tab; });
  }
  function renderLive() {
    if (!live) return;
    if (!body.querySelector(`.dsc-live[data-did="${live.did}"]`)) skeleton(live.did);
    const slot = (n) => body.querySelector(`[data-slot="${n}"]`);
    put(slot("head"), headHtml()); put(slot("usage"), usageHtml()); setTab(tab);
    put(slot("talk"), talkHtml()); put(slot("pend"), pendHtml()); put(slot("err"), errHtml());
    if (put(slot("owner"), ownerHtml())) {
      const ta = $("#discuss-text"), sel = $("#discuss-kind");
      if (ta) ta.value = draft;
      if (sel) sel.value = kind;
    }
    put(slot("record"), recordHtml());
  }
  async function loadLive(fresh) {
    const did = route.did;
    let d;
    try { d = await api(`/api/discussion?did=${encodeURIComponent(did)}&sig=${fresh ? "" : encodeURIComponent(sig)}`); }
    catch (err) {
      if (route.kind !== "live" || route.did !== did) return;
      if (!live) put(body, `<a class="back-link" href="#discuss">${ICON.back}<span>讨论</span></a><p class="empty">${esc(err.message)}</p>`);
      else if (!failing) toast(err.message);
      failing = true;
      schedulePoll("discuss", true, 10000);
      return;
    }
    if (route.kind !== "live" || route.did !== did) return;
    failing = false;
    if (!d.same) { live = d; sig = d.sig; }
    renderLive();
    if (live.status !== lastStatus) { const first = !lastStatus; lastStatus = live.status; if (!first) { refreshBadge(); announce(); } }
    schedulePoll("discuss", true, live.active || ACTIVE.includes(live.status) ? 2500 : 10000);
  }
  async function fetchRaw(id) {
    if (!live || loading.has(id) || raws[id]) return;
    const t = (live.turns || []).find((x) => x.id === id);
    if (!t) return;
    loading.add(id);
    const did = live.did, get = (repair) => api(`/api/discussion/raw?did=${encodeURIComponent(did)}&turn=${encodeURIComponent(id)}&repair=${repair ? 1 : 0}`);
    try {
      const r = await get(false);
      if (t.has_repair) { try { r.repair = await get(true); } catch { /* the first answer is enough */ } }
      raws[id] = r;
    } catch (err) { raws[id] = { error: err.message }; }
    finally { loading.delete(id); }
    if (live && live.did === did) put(body.querySelector('[data-slot="talk"]'), talkHtml());
  }
  function openRaw(id) {
    setTab("talk");
    const d = body.querySelector(`details.dsc-raw[data-turn="${CSS.escape(id)}"]`);
    if (!d) return;
    d.open = true;
    fetchRaw(id);
    d.scrollIntoView({ block: "start", behavior: "smooth" });
  }
  async function act(b) {
    const a = b.dataset.act;
    if (busy || !live) return;
    const ta = $("#discuss-text"), text = ta ? ta.value.trim() : "", err = $("#discuss-act-error");
    let k = b.dataset.kind || "";
    if (a === "edit_requirement" && !k) k = ($("#discuss-kind") || {}).value || "change";
    const need = a === "edit_requirement" ? NEED[k === "answer" ? "answer" : "edit_requirement"] : NEED[a];
    if (need && !text) { if (err) err.textContent = need; else toast(need); if (ta) ta.focus(); return; }
    if (CONFIRM[a] && !confirm(CONFIRM[a])) return;
    busy = true; renderLive();
    try {
      await api("/api/discussion/action", { method: "POST", body: { did: live.did, action: a, text: OWNER_ACTS.includes(a) ? text : "",
        kind: a === "edit_requirement" ? k : "", ref: b.dataset.ref || "" } });
      if (OWNER_ACTS.includes(a)) draft = "";
      busy = false;
      toast(DONE[a === "edit_requirement" && k === "answer" ? "answer" : a] || "好的");
      body.querySelectorAll('[data-slot="owner"]').forEach((el) => { el._src = null; });
      await loadLive(true);
      if (a === "approve") setTab("record");
    } catch (x) {
      busy = false; renderLive();
      const e2 = $("#discuss-act-error");
      if (e2) e2.textContent = x.message; else toast(x.message);
    }
  }

  // ---- events -------------------------------------------------------------------------------------------------
  body.addEventListener("click", (e) => {
    const l = e.target.closest("[data-link]");
    if (l && body.contains(l)) return link(l.dataset.link);
    const b = e.target.closest("[data-act], [data-tab], [data-open]");
    if (!b || !body.contains(b)) return;
    if (b.dataset.act) return act(b);
    if (b.dataset.tab) { setTab(b.dataset.tab); return b.closest(".dsc-tabs") ? undefined : b.closest(".dsc-live").scrollIntoView({ block: "start" }); }
    if (b.dataset.open) openRaw(b.dataset.open);
  });
  body.addEventListener("toggle", (e) => {
    const d = e.target;
    if (d.matches && d.matches("details.dsc-raw") && d.open) fetchRaw(d.dataset.turn);
  }, true);
  body.addEventListener("input", (e) => {
    const t = e.target;
    if (t.id === "discuss-text") draft = t.value;
    if (t.id === "discuss-req") { localStorageSet(DRAFT, t.value); count(); }
    const f = t.closest && t.closest("#discuss-form");
    if (f && $("#discuss-sum")) $("#discuss-sum").innerHTML = sumHtml(f);
  });
  body.addEventListener("change", (e) => {
    const t = e.target;
    if (t.id === "discuss-kind") kind = t.value;
    const f = t.closest && t.closest("#discuss-form");
    if (f && $("#discuss-sum")) $("#discuss-sum").innerHTML = sumHtml(f);
  });
  body.addEventListener("submit", (e) => { if (e.target.id === "discuss-form") submit(e); });
  body.addEventListener("keydown", (e) => {   // Enter in the folder path registers it; it never starts the discussion
    if (e.key === "Enter" && e.target.name === "link_path") { e.preventDefault(); link("save"); }
  });

  // ---- routing (app.js calls load / poll / leave) ---------------------------------------------------------------
  function parse() {
    const id = location.hash.slice(1).split("/")[1] || "";
    return id === "new" ? { kind: "new" } : /^DS-\d+$/.test(id) ? { kind: "live", did: id } : { kind: "list" };
  }
  function reset() { live = null; sig = ""; raws = {}; loading = new Set(); lastStatus = ""; failing = false; draft = ""; kind = "change"; busy = false; tab = "talk"; }
  async function load() {
    const r = parse();
    if (r.kind !== route.kind || r.did !== route.did) { reset(); body._src = null; body.innerHTML = ""; }
    route = r;
    page.dataset.screen = r.kind;
    showNotice("");
    if (r.kind === "new") return showForm();
    if (r.kind === "live") return loadLive(true);
    return loadList();
  }
  function poll() {
    if (state.view !== "discuss") return Promise.resolve();
    if (route.kind === "live") return loadLive(false);
    if (route.kind === "list") return loadList();
    return Promise.resolve();
  }
  function leave() { route = { kind: "" }; reset(); }

  return { load, poll, leave };
})();
