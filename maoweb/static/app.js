"use strict";
// MAO web front end. No framework, no CDN: everything is served by the PC itself.

const $ = (sel) => document.querySelector(sel);
const state = { user: null, models: [], model: null, convId: null, convs: [], streaming: null };

// ---- api ---------------------------------------------------------------------
const OFFLINE = "连不上 MAO：请检查网络。在外面用时，手机上要打开 Tailscale，家里的电脑要开着 MAO。";
async function api(path, opts = {}) {
  let res;
  try {
    res = await fetch(path, {
      method: opts.method || "GET",
      headers: { "Content-Type": "application/json", "X-MAO": "1" },
      body: opts.body ? JSON.stringify(opts.body) : undefined,
      signal: opts.signal,
    });
  } catch (err) { if (err.name === "AbortError") throw err; throw new Error(OFFLINE); }
  if (res.status === 401 && !opts.allow401) { showAuth(false); throw new Error("请先登录"); }
  const data = await res.json().catch(() => ({}));
  if (res.status === 409 && data.code === "legacy_off" && state.user) refreshBadge();   // the old dispatch went off meanwhile
  if (!res.ok) { const e = new Error(data.error || `出错了（${res.status}）`); e.data = data; throw e; }   // data: what else the answer says
  return data;
}

function toast(text) {
  const t = $("#toast");
  t.textContent = text; t.hidden = false;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => (t.hidden = true), 2600);
}

// ---- markdown (small and safe: everything is escaped first) ----------------------
function esc(s) { return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function inline(s) {
  return s
    .replace(/`([^`]+)`/g, (_, c) => `<code>${c}</code>`)
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
}
function markdown(src) {
  const lines = esc(src).split("\n");
  const out = []; let list = null; let para = [];
  const flushPara = () => { if (para.length) { out.push(`<p>${inline(para.join("<br>"))}</p>`); para = []; } };
  const flushList = () => { if (list) { out.push(`<${list.tag}>${list.items.map((i) => `<li>${inline(i)}</li>`).join("")}</${list.tag}>`); list = null; } };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const fence = line.match(/^```(\w*)/);
    if (fence) {
      flushPara(); flushList();
      const code = [];
      while (++i < lines.length && !lines[i].startsWith("```")) code.push(lines[i]);
      out.push(`<pre><code>${code.join("\n")}</code></pre>`);
      continue;
    }
    const h = line.match(/^(#{1,3})\s+(.*)/);
    const ul = line.match(/^\s*[-*]\s+(.*)/);
    const ol = line.match(/^\s*\d+[.)]\s+(.*)/);
    const quote = line.match(/^&gt;\s?(.*)/);
    if (h) { flushPara(); flushList(); out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`); }
    else if (ul || ol) {
      flushPara();
      const tag = ul ? "ul" : "ol";
      if (!list || list.tag !== tag) { flushList(); list = { tag, items: [] }; }
      list.items.push((ul || ol)[1]);
    } else if (quote) { flushPara(); flushList(); out.push(`<blockquote>${inline(quote[1])}</blockquote>`); }
    else if (!line.trim()) { flushPara(); flushList(); }
    else { flushList(); para.push(line); }
  }
  flushPara(); flushList();
  return out.join("");
}

// ---- auth --------------------------------------------------------------------
async function boot() {
  const s = await api("/api/state", { allow401: true });
  if (s.user) { state.user = s.user; return enterApp(); }
  showAuth(s.needs_setup);
}

function showAuth(setup) {
  $("#app").hidden = true; $("#auth").hidden = false;
  $("#auth-title").textContent = setup ? "第一次使用" : "欢迎回来";
  $("#auth-sub").textContent = setup ? "先创建管理员账号（之后可以给家人加账号）" : "登录后开始使用";
  $("#display-row").hidden = !setup;
  $("#auth-submit").textContent = setup ? "创建并进入" : "登录";
  $("#auth-form").dataset.mode = setup ? "setup" : "login";
  $("#auth-form [name=password]").autocomplete = setup ? "new-password" : "current-password";
  $("#auth-form [name=name]").focus();
}

$("#auth-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  const body = Object.fromEntries(f.entries());
  $("#auth-error").textContent = "";
  try {
    await api(e.target.dataset.mode === "setup" ? "/api/setup" : "/api/login", { method: "POST", body, allow401: true });
    const s = await api("/api/state");
    state.user = s.user; e.target.reset(); enterApp();
  } catch (err) { $("#auth-error").textContent = err.message; }
});

async function enterApp() {
  $("#auth").hidden = true; $("#app").hidden = false;
  const name = state.user.display || state.user.name;
  $("#user-name").textContent = name;
  $("#avatar").textContent = name.slice(0, 1).toUpperCase();
  applyRole();
  setGreeting();
  await Promise.all([loadModels(), loadConvs()]);
  route();
}

// what this person sees: the admin's pages, and 项目 / 交给项目 only while the old dispatch is on (the server says so
// through can_code)
function applyRole() {
  $("#menu-settings").hidden = !state.user.admin;
  $("#nav-projects").hidden = !state.user.can_code;
  $("#handoff-open").hidden = !state.user.can_code;
  $("#nav-agents").hidden = !state.user.admin;
  $("#nav-discuss").hidden = !state.user.admin;
  setBadge(state.user.waiting, state.user.discuss_waiting);
}

// the legacy switch (or this member's 项目 permission) changed while the page is open
function roleChanged() {
  applyRole();
  if (state.view === "projects" && !state.user.can_code) {
    history.replaceState(null, "", state.convId ? `#${state.convId}` : location.pathname);
    showView("chat");
  }
  if (state.view === "chat") MAOProjects.chat();   // the project cards under the conversation come or go
  refreshPeople();                                 // 家人's 项目 switches follow the old dispatch
}

// redraw 家人 while the settings dialog is open (what the admin was typing into 添加家庭成员 is kept)
async function refreshPeople() {
  if (!state.user.admin || !$("#settings").open) return;
  const box = $("#tab-people");
  const typed = [...box.querySelectorAll("input[name]")].map((i) => [i.name, i.value]);
  try { renderPeople((await api("/api/admin/settings")).users); } catch { return; }   // keep the list as it was
  for (const [n, v] of typed) { const i = box.querySelector(`input[name="${n}"]`); if (i) i.value = v; }
}

function setGreeting() {
  const h = new Date().getHours();
  const part = h < 5 ? "夜深了" : h < 11 ? "早上好" : h < 14 ? "中午好" : h < 18 ? "下午好" : "晚上好";
  $("#greeting-text").textContent = `${part}，${state.user.display || state.user.name}`;
}

// ---- models ------------------------------------------------------------------
async function loadModels() {
  const d = await api("/api/models");
  state.models = d.models;
  const saved = localStorageGet("mao.model");
  const pick = [saved, d.default].find((m) => m && d.models.some((x) => x.id === m)) || (d.models[0] && d.models[0].id);
  setModel(pick || null);
  renderModelMenu();
  updateHint();
}
function localStorageGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function localStorageSet(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } }

function setModel(id) {
  state.model = id;
  const m = state.models.find((x) => x.id === id);
  $("#model-label").textContent = m ? m.model : "还没有可用的模型";
  if (id) localStorageSet("mao.model", id);
  renderModelMenu(); updateSend(); updateHint();
}
function renderModelMenu() {
  const menu = $("#model-menu"); menu.innerHTML = "";
  let group = "";
  for (const m of state.models) {
    if (m.provider !== group) { group = m.provider; menu.insertAdjacentHTML("beforeend", `<div class="group">${esc(group)}</div>`); }
    const b = document.createElement("button");
    b.type = "button"; b.setAttribute("role", "option"); b.setAttribute("aria-selected", String(m.id === state.model));
    b.innerHTML = `<span>${esc(m.model)}</span>${m.id === state.model ? '<span class="check">✓</span>' : ""}`;
    b.onclick = () => { setModel(m.id); menu.hidden = true; $("#input").focus(); };
    menu.appendChild(b);
  }
  if (!state.models.length) menu.innerHTML = `<div class="group">还没有可用的模型</div>`;
}
function updateHint() {
  const hint = $("#hint");
  if (state.models.length) { hint.textContent = state.model?.startsWith("claude_subscription/")
    ? "使用你的 Claude 订阅额度。回答可能有错，重要的事请再核实。" : "回答可能有错，重要的事请再核实。"; return; }
  hint.innerHTML = state.user.admin ? '还没有可用的模型：请在 <a href="#" id="hint-settings">设置</a> 里检查 Claude 订阅登录，或填一个 API key。'
                                    : "还没有可用的模型，请让管理员在设置里填 API key。";
  const link = $("#hint-settings");
  if (link) link.onclick = (e) => { e.preventDefault(); openSettings(); };
}
$("#model-btn").onclick = (e) => { e.stopPropagation(); $("#model-menu").hidden = !$("#model-menu").hidden; };

// ---- conversations -------------------------------------------------------------
async function loadConvs() {
  state.convs = (await api("/api/conversations")).conversations;
  renderConvs();
}
function renderConvs() {
  const list = $("#conv-list"); list.innerHTML = "";
  if (!state.convs.length) { list.innerHTML = '<div class="empty-list">还没有对话</div>'; return; }
  for (const c of state.convs) {
    const row = document.createElement("div");
    row.className = "conv" + (c.id === state.convId ? " active" : "");
    row.innerHTML = `<a href="#${c.id}">${esc(c.title || "新对话")}</a>
      <button class="del" title="删除" aria-label="删除"><svg viewBox="0 0 20 20"><path d="M5 6h10M8 6V4h4v2M7 6l1 10h4l1-10"/></svg></button>`;
    row.querySelector("a").onclick = (e) => { e.preventDefault(); openConv(c.id); closeSidebar(); };
    row.querySelector(".del").onclick = async () => {
      if (!confirm(`删除「${c.title || "新对话"}」？删除后找不回来。`)) return;
      await api(`/api/conversations/${c.id}`, { method: "DELETE" });
      if (state.convId === c.id) newChat();
      loadConvs();
    };
    list.appendChild(row);
  }
}

function newChat() {
  stopStreaming(); showView("chat");
  state.convId = null; history.replaceState(null, "", location.pathname);
  $("#messages").innerHTML = ""; $("#conv-title").textContent = "";
  $("#app").classList.remove("has-thread");
  renderConvs(); MAOProjects.chat(); $("#input").focus();
}
$("#new-chat").onclick = () => { newChat(); closeSidebar(); };

async function openConv(id) {
  stopStreaming();
  try {
    const d = await api(`/api/conversations/${id}`);
    state.convId = id; history.replaceState(null, "", `#${id}`); showView("chat");
    $("#conv-title").textContent = d.conversation.title || "";
    const box = $("#messages"); box.innerHTML = "";
    for (const m of d.messages) addMessage(m.role, m.content, m.model);
    $("#app").classList.toggle("has-thread", d.messages.length > 0);
    if (d.conversation.model && state.models.some((x) => x.id === d.conversation.model)) setModel(d.conversation.model);
    renderConvs(); MAOProjects.chat(); scrollDown(true);
  } catch (err) { toast(err.message); newChat(); }
}

function addMessage(role, content, model) {
  const el = document.createElement("div");
  el.className = `msg ${role}`;
  if (role === "assistant") {
    el.innerHTML = markdown(content);
    el.dataset.raw = content;
    appendMeta(el, model);
  } else { el.textContent = content; }
  $("#messages").appendChild(el);
  return el;
}
function appendMeta(el, model) {
  const meta = document.createElement("div");
  meta.className = "msg-meta";
  const short = model ? model.split("/").pop() : "";
  meta.innerHTML = `<button type="button">复制</button>${short ? `<span>${esc(short)}</span>` : ""}`;
  meta.querySelector("button").onclick = async () => {
    try { await navigator.clipboard.writeText(el.dataset.raw || ""); toast("已复制"); } catch { toast("复制失败，请手动选择文字"); }
  };
  el.appendChild(meta);
}
function scrollDown(force) {
  const t = $("#thread");
  if (force || t.scrollHeight - t.scrollTop - t.clientHeight < 160) t.scrollTop = t.scrollHeight;
}

// ---- sending -------------------------------------------------------------------
const input = $("#input");
function updateSend() {
  const btn = $("#send");
  if (state.streaming) { btn.disabled = false; btn.classList.add("stop"); btn.setAttribute("aria-label", "停止");
    btn.innerHTML = '<svg viewBox="0 0 20 20"><rect x="6" y="6" width="8" height="8" rx="1.5" fill="currentColor" stroke="none"/></svg>'; return; }
  btn.classList.remove("stop"); btn.setAttribute("aria-label", "发送");
  btn.innerHTML = '<svg viewBox="0 0 20 20"><path d="M10 16V4M5 9l5-5 5 5"/></svg>';
  btn.disabled = !input.value.trim() || !state.model;
}
input.addEventListener("input", () => {
  input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 260) + "px"; updateSend();
});
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing && !matchMedia("(pointer: coarse)").matches) {
    e.preventDefault(); $("#composer").requestSubmit();
  }
});
$("#composer").addEventListener("submit", (e) => {
  e.preventDefault();
  if (state.streaming) return stopStreaming();
  const text = input.value.trim();
  if (text && state.model) send(text);
});

function stopStreaming() { if (state.streaming) { state.streaming.abort(); state.streaming = null; updateSend(); } }

async function send(text) {
  input.value = ""; input.style.height = "auto";
  $("#app").classList.add("has-thread");
  addMessage("user", text);
  const el = document.createElement("div");
  el.className = "msg assistant cursor"; $("#messages").appendChild(el);
  scrollDown(true);
  const ctrl = new AbortController(); state.streaming = ctrl; updateSend();
  let raw = "", isNew = !state.convId;
  try {
    const res = await fetch("/api/chat", {
      method: "POST", signal: ctrl.signal,
      headers: { "Content-Type": "application/json", "X-MAO": "1" },
      body: JSON.stringify({ conversation_id: state.convId, model: state.model, content: text }),
    });
    if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.error || `出错了（${res.status}）`); }
    const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const block = buf.slice(0, i); buf = buf.slice(i + 2);
        const ev = (block.match(/^event: (.*)$/m) || [])[1];
        const data = JSON.parse((block.match(/^data: (.*)$/m) || [, "{}"])[1]);
        if (ev === "start") { state.convId = data.conversation_id; history.replaceState(null, "", `#${state.convId}`); }
        else if (ev === "delta") { raw += data.text; el.innerHTML = markdown(raw); scrollDown(); }
        else if (ev === "error") { throw new Error(data.error); }
      }
    }
  } catch (err) {
    if (err.name !== "AbortError") {
      const e = document.createElement("div"); e.className = "msg error"; e.textContent = err instanceof TypeError ? OFFLINE : err.message;
      $("#messages").appendChild(e);
    }
  } finally {
    el.classList.remove("cursor"); el.dataset.raw = raw;
    if (raw) appendMeta(el, state.model); else el.remove();
    state.streaming = null; updateSend(); scrollDown(); MAOProjects.chat();
    if (isNew) { await loadConvs(); const c = state.convs.find((x) => x.id === state.convId); if (c) $("#conv-title").textContent = c.title; }
    input.focus();
  }
}

// ---- sidebar / menus -------------------------------------------------------------
function closeSidebar() { $("#app").classList.remove("sidebar-open"); $("#scrim").hidden = true; }
$("#open-sidebar").onclick = () => { $("#app").classList.add("sidebar-open"); $("#scrim").hidden = false; };
$("#close-sidebar").onclick = closeSidebar; $("#scrim").onclick = closeSidebar;
$("#user-chip").onclick = (e) => { e.stopPropagation(); $("#user-menu").hidden = !$("#user-menu").hidden; };
document.addEventListener("click", () => { $("#user-menu").hidden = true; $("#model-menu").hidden = true; });
$("#user-menu").onclick = async (e) => {
  const act = e.target.closest("button") && e.target.closest("button").dataset.act;
  if (act === "logout") { await api("/api/logout", { method: "POST" }).catch(() => {}); state.user = null; showAuth(false); }
  if (act === "settings") openSettings();
  if (act === "lang") I18N.set(I18N.lang === "en" ? "zh" : "en");
  if (act === "password") { $("#pw-error").textContent = ""; $("#pw-form").reset(); $("#password-dialog").showModal(); }
};
$("#pw-cancel").onclick = () => $("#password-dialog").close();
// language: Chinese or English (i18n.js); the button shows the other one
for (const id of ["#menu-lang", "#auth-lang"]) $(id).textContent = I18N.lang === "en" ? "中文" : "English";
$("#auth-lang").onclick = () => I18N.set(I18N.lang === "en" ? "zh" : "en");
$("#pw-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try { await api("/api/password", { method: "POST", body: Object.fromEntries(new FormData(e.target).entries()) });
    $("#password-dialog").close(); toast("密码已修改"); }
  catch (err) { $("#pw-error").textContent = err.message; }
});

// ---- settings (admin) ------------------------------------------------------------
function showTab(name) {
  document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x.dataset.tab === name));
  document.querySelectorAll("#settings .tab-body").forEach((b) => (b.hidden = b.id !== `tab-${name}`));
  if (name === "remote") renderRemote();
  if (name === "notify") renderNotify();
  if (name === "legacy") renderLegacy();
}
document.querySelectorAll(".tab").forEach((t) => t.onclick = () => showTab(t.dataset.tab));

async function openSettings(tab = "models") {
  closeSidebar();
  try {
    const d = await api("/api/admin/settings");
    renderProviders(d.providers); renderPeople(d.users);
    renderMediaModels(d.image_models, d.video_models);
  } catch (err) { return toast(err.message); }
  showTab(tab);
  $("#settings").showModal();
}
$("#settings").addEventListener("close", () => { loadModels(); if (state.view !== "chat") showView(state.view, true); });

const SOURCE = { env: "已设置（来自环境变量）", saved: "已设置", none: "未设置", subscription: "订阅已连接" };
function renderProviders(providers) {
  const box = $("#tab-models"); box.innerHTML =
    '<p class="small-note">API key 只保存在这台电脑上（Windows 加密），不会显示出来，也不会上传。填好一个服务商，就可以在聊天里选它的模型。</p>';
  for (const p of providers) {
    const card = document.createElement("div"); card.className = "card";
    if (p.transport === "cli") {
      card.innerHTML = `<div class="card-head"><h3>${esc(p.name)}</h3><span class="badge ${p.status.ready ? "on" : ""}">${p.status.ready ? "订阅已连接" : "尚未连接"}</span></div>
        <p class="small-note">${esc(p.note)}</p><p class="small-note">${esc(p.status.message)}</p>
        <div class="row"><button class="ghost" data-a="check">检查登录</button>
        <button class="primary" data-a="default" ${p.status.ready ? "" : "disabled"}>设为默认聊天模型</button></div>
        <p class="small-note test-result"></p>`;
      card.querySelector('[data-a="check"]').onclick = async () => {
        const result = card.querySelector(".test-result"); result.textContent = "正在检查…";
        try {
          const d = await api("/api/admin/test", { method: "POST", body: { provider: p.id } });
          result.textContent = d.message || d.error;
          card.querySelector('[data-a="default"]').disabled = !d.ok;
          card.querySelector(".badge").textContent = d.ok ? "订阅已连接" : "尚未连接";
          card.querySelector(".badge").classList.toggle("on", d.ok);
          await loadModels();
        } catch (err) { result.textContent = err.message; }
      };
      card.querySelector('[data-a="default"]').onclick = async () => {
        try {
          await api("/api/admin/models", { method: "POST", body: { default_model: `${p.id}/sonnet` } });
          await loadModels(); setModel(`${p.id}/sonnet`); toast("已设为默认聊天模型");
        } catch (err) { toast(err.message); }
      };
      box.appendChild(card);
      continue;
    }
    card.innerHTML = `
      <div class="card-head"><h3>${esc(p.name)}</h3><span class="badge ${p.key_source !== "none" ? "on" : ""}">${SOURCE[p.key_source]}</span></div>
      <div class="row"><input type="password" autocomplete="off" placeholder="${p.key_source === "none" ? "粘贴 API key" : "填新的 key 可以替换"}" aria-label="${esc(p.name)} API key">
        <button class="primary" data-a="save">保存</button>
        ${p.key_source === "saved" ? '<button class="ghost" data-a="clear">删除</button>' : ""}
        <button class="ghost" data-a="test">测试连接</button></div>
      <p class="small-note">${esc(p.note)}${p.key_source === "env" ? `（环境变量 ${esc(p.env)} 优先）` : ""}</p>
      <div><div class="small-note" style="margin-bottom:6px">可选的模型</div><div class="chips"></div></div>
      <div class="row"><input placeholder="添加模型名，例如 ${p.id === "qwen" ? "qwen-plus" : p.id === "doubao" ? "doubao-seed-1-6" : "deepseek-flash"}" aria-label="添加模型">
        <button class="ghost" data-a="add">添加</button></div>
      <p class="small-note test-result"></p>`;
    const chips = card.querySelector(".chips");
    const models = [...p.models];
    const draw = () => {
      chips.innerHTML = models.length ? "" : '<span class="small-note">还没有模型，点「测试连接」可以看到这个 key 能用哪些</span>';
      models.forEach((m, i) => {
        const c = document.createElement("span"); c.className = "chip"; c.innerHTML = `${esc(m)}<button aria-label="移除">×</button>`;
        c.querySelector("button").onclick = () => { models.splice(i, 1); saveModels(); };
        chips.appendChild(c);
      });
    };
    const saveModels = async () => { await api("/api/admin/models", { method: "POST", body: { provider: p.id, models } }); draw(); };
    draw();
    const [keyInput, modelInput] = card.querySelectorAll("input");
    card.addEventListener("click", async (e) => {
      const a = e.target.dataset.a; if (!a) return;
      const result = card.querySelector(".test-result");
      try {
        if (a === "save") { if (!keyInput.value.trim()) return; const d = await api("/api/admin/key", { method: "POST", body: { provider: p.id, key: keyInput.value } }); renderProviders(d.providers); toast("已保存"); }
        if (a === "clear") { if (!confirm("删除这个 API key？")) return; const d = await api("/api/admin/key", { method: "POST", body: { provider: p.id, key: "" } }); renderProviders(d.providers); }
        if (a === "add") { const v = modelInput.value.trim(); if (v && !models.includes(v)) { models.push(v); modelInput.value = ""; saveModels(); } }
        if (a === "test") {
          result.textContent = "正在连接…";
          const d = await api("/api/admin/test", { method: "POST", body: { provider: p.id } });
          if (!d.ok) { result.textContent = d.error; return; }
          result.innerHTML = d.models.length ? `连接成功。这个 key 能用的模型：${d.models.slice(0, 40).map((m) => `<button class="link" data-m="${esc(m)}">${esc(m)}</button>`).join("、")}（点一下加入）` : "连接成功。";
          result.querySelectorAll("[data-m]").forEach((b) => b.onclick = () => { if (!models.includes(b.dataset.m)) { models.push(b.dataset.m); saveModels(); } });
        }
      } catch (err) { result.textContent = err.message; }
    });
    box.appendChild(card);
  }
}

function renderPeople(users) {
  const box = $("#tab-people");
  box.innerHTML = `<p class="small-note">每个人有自己的账号和对话记录，互相看不到。只有管理员能改设置。</p>
    <div class="card"><div class="people"></div></div>
    <div class="card"><h3 style="margin:0;font-size:15px">添加家庭成员</h3>
      <div class="row"><input name="name" placeholder="登录用的名字，例如 mama"><input name="display" placeholder="称呼，例如 妈妈"></div>
      <div class="row"><input name="password" type="password" autocomplete="new-password" placeholder="初始密码（至少 8 位）">
        <button class="primary" data-a="add">添加</button></div>
      <p class="error"></p></div>`;
  const list = box.querySelector(".people");
  for (const u of users) {
    const row = document.createElement("div"); row.className = "person";
    row.innerHTML = `<span class="avatar">${esc((u.display || u.name).slice(0, 1).toUpperCase())}</span>
      <span class="name"><span class="p-display">${esc(u.display || u.name)}</span><small>${esc(u.name)}${u.is_admin ? " · 管理员" : ""}</small></span>
      ${u.is_admin || !state.user.dispatch ? "" : `<label class="switch" title="可以让 AI 在项目里写程序"><input type="checkbox" data-a="code" ${u.can_code ? "checked" : ""}><span>项目</span></label>`}
      <button class="ghost" data-a="reset">重设密码</button>${u.name === state.user.name ? "" : '<button class="ghost" data-a="delete">删除</button>'}`;
    row.onclick = async (e) => {
      const a = e.target.dataset.a; if (!a) return;
      try {
        if (a === "reset") { const pw = prompt(`给 ${u.display || u.name} 设一个新密码（至少 8 位）`); if (!pw) return;
          await api("/api/admin/users", { method: "POST", body: { action: "reset", id: u.id, password: pw } }); toast("密码已重设"); }
        if (a === "code") { await api("/api/admin/users", { method: "POST", body: { action: "can_code", id: u.id, value: e.target.checked } });
          toast(e.target.checked ? `${u.display || u.name} 现在可以用项目了` : "已关闭项目权限"); }
        if (a === "delete") { if (!confirm(`删除 ${u.display || u.name} 的账号和全部对话？`)) return;
          const d = await api("/api/admin/users", { method: "POST", body: { action: "delete", id: u.id } }); renderPeople(d.users); }
      } catch (err) { toast(err.message); }
    };
    list.appendChild(row);
  }
  box.querySelector("[data-a=add]").onclick = async () => {
    const get = (n) => box.querySelector(`[name=${n}]`).value;
    try { const d = await api("/api/admin/users", { method: "POST", body: { action: "add", name: get("name"), display: get("display"), password: get("password") } });
      renderPeople(d.users); toast("已添加"); }
    catch (err) { box.querySelector(".error").textContent = err.message; }
  };
}

window.addEventListener("hashchange", () => { if (state.user) route(); });

// ---- views: chat · pictures · video · documents · projects · agents ----------------------
const VIEW_NAMES = { chat: "", image: "画图", video: "视频", docs: "文档", projects: "项目", discuss: "讨论", agents: "Agent" };
state.view = "chat";
state.creative = null;     // models, sizes, ratios, whether the Ark key is set
state.source = { image: null, video: null };   // the photo a picture edit / video starts from
let pollTimer = null, pollLater = null;

function route() {
  const h = location.hash.slice(1), base = h.split("/")[0];
  if (base === "projects") return showView("projects", true);   // #projects, #projects/<id>, #projects/<id>/<job>
  if (base === "discuss") return showView("discuss", true);     // #discuss, #discuss/new, #discuss/DS-0003
  if (VIEW_NAMES[h] !== undefined && h !== "chat") return showView(h);
  const id = Number(h);
  if (id) { if (id !== state.convId || state.view !== "chat") openConv(id); }
  else newChat();
}

function showView(name, refresh) {
  if (name === "projects" && !state.user.can_code) name = "chat";
  if (name === "agents" && !state.user.admin) name = "chat";
  if (name === "discuss" && !state.user.admin) name = "chat";
  const changed = state.view !== name;
  if (name !== "projects") MAOProjects.leave();
  if (name !== "discuss") MAODiscuss.leave();
  state.view = name;
  for (const v of Object.keys(VIEW_NAMES)) $(`#view-${v}`).hidden = v !== name;
  document.querySelectorAll("#views-nav button").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
  $("#chat-side").hidden = name !== "chat";
  $("#side-fill").hidden = name === "chat";
  if (name !== "chat") {
    $("#conv-title").textContent = VIEW_NAMES[name];
    if (!location.hash.startsWith(`#${name}/`) && location.hash !== `#${name}`) history.replaceState(null, "", `#${name}`);
  }
  $("#app").dataset.view = name;
  if (name !== "chat" && (changed || refresh)) loadView(name);
  if (name === "chat") clearTimeout(pollTimer);
}
document.querySelectorAll("#views-nav button").forEach((b) => b.onclick = () => {
  closeSidebar();
  if (b.dataset.view === "chat") return state.convId ? openConv(state.convId) : newChat();
  if (b.dataset.view === "projects" && location.hash !== "#projects") { location.hash = "#projects"; return; }   // the list (back works)
  if (b.dataset.view === "discuss" && location.hash !== "#discuss") { location.hash = "#discuss"; return; }
  showView(b.dataset.view);
});

async function loadView(name) {
  try {
    if (name === "image" || name === "video") { await loadCreative(); await loadMedia(name); }
    if (name === "projects") await MAOProjects.load();
    if (name === "discuss") await MAODiscuss.load();
    if (name === "docs") await loadDocs(true);
    if (name === "agents") await loadAgents();
  } catch (err) { toast(err.message); }
}

const POLL = { projects: () => MAOProjects.poll(), discuss: () => MAODiscuss.poll(), docs: () => loadDocs(false), agents: () => loadAgents() };
// One view polls at a time. A hidden tab does not poll; it polls once when it is shown again.
function schedulePoll(name, needed, ms = 4000) {
  clearTimeout(pollTimer); pollLater = null;
  if (needed && state.view === name)
    pollTimer = setTimeout(() => { if (document.hidden) pollLater = [name, ms]; else runPoll(name, ms); }, ms);
}
function runPoll(name, ms) { (POLL[name] ? POLL[name]() : loadMedia(name)).catch(() => schedulePoll(name, true, ms)); }
document.addEventListener("visibilitychange", () => {
  if (document.hidden || !pollLater) return;
  const [name, ms] = pollLater; pollLater = null;
  if (state.view === name) runPoll(name, ms);
});

// what waits for the user's decision: jobs on 项目, discussions on 讨论 (admin), their sum on the phone's menu button
// and in the tab title
function setBadge(jobs, discuss = 0) {
  jobs = Number(jobs) || 0; discuss = Number(discuss) || 0;
  const show = (id, n) => { const el = $(id); el.textContent = n > 9 ? "9+" : String(n); el.hidden = !n; el.setAttribute("aria-label", `${n} 件等你决定`); };
  show("#nav-badge", jobs); show("#nav-discuss-badge", discuss); show("#menu-badge", jobs + discuss);
  document.title = (jobs + discuss ? `(${jobs + discuss}) ` : "") + "MAO";
}
async function refreshBadge() {
  if (!state.user) return;
  try {
    const s = await api("/api/state", { allow401: true });
    if (!s.user || !state.user) return;
    const changed = s.user.dispatch !== state.user.dispatch || s.user.can_code !== state.user.can_code;   // e.g. orch legacy on
    Object.assign(state.user, { waiting: s.user.waiting, discuss_waiting: s.user.discuss_waiting,
                                dispatch: s.user.dispatch, can_code: s.user.can_code });
    if (changed) roleChanged(); else setBadge(s.user.waiting, s.user.discuss_waiting);
  } catch { /* offline: keep the old number */ }
}
setInterval(() => { if (!document.hidden && !(state.view === "projects" && MAOProjects.polling())) refreshBadge(); }, 30000);

$("#starters").onclick = (e) => {
  const b = e.target.closest("button"); if (!b) return;
  if (b.dataset.go) return showView(b.dataset.go);
  input.value = b.dataset.fill; input.dispatchEvent(new Event("input"));
  input.focus(); input.setSelectionRange(input.value.length, input.value.length);
};

// ---- pictures and video ---------------------------------------------------------------
const SIZE_LABEL = { "1024x1024": "方形", "864x1152": "竖版 3:4", "1152x864": "横版 4:3", "720x1280": "手机竖屏 9:16",
                     "1280x720": "横屏 16:9", "2K": "高清 2K" };
const RATIO_LABEL = { "16:9": "横屏 16:9", "9:16": "竖屏 9:16", "1:1": "方形 1:1", "4:3": "横版 4:3", "3:4": "竖版 3:4" };

function fillSelect(sel, values, labels, keep) {
  const old = sel.value;
  sel.innerHTML = values.map((v) => `<option value="${esc(v)}">${esc((labels && labels[v]) || v)}</option>`).join("");
  if (keep && values.includes(old)) sel.value = old;
  sel.hidden = !values.length;
}

async function loadCreative() {
  const c = state.creative = await api("/api/creative");
  const imgForm = $("#image-form"), vidForm = $("#video-form");
  fillSelect(imgForm.elements.size, c.sizes, SIZE_LABEL, true);
  fillSelect(imgForm.elements.model, c.image_models, null, true);
  fillSelect(vidForm.elements.ratio, c.ratios, RATIO_LABEL, true);
  fillSelect(vidForm.elements.model, c.video_models, null, true);
  // one model is the usual case: no need to show a choice
  imgForm.elements.model.hidden = c.image_models.length < 2;
  vidForm.elements.model.hidden = c.video_models.length < 2;
  for (const kind of ["image", "video"]) {
    const note = $(`#${kind}-notice`);
    const models = kind === "image" ? c.image_models : c.video_models;
    let msg = "";
    if (!c.ready) msg = state.user.admin ? '画图和视频要用豆包（火山方舟）的 API key。请在 <a href="#" data-settings="models">设置 · 聊天</a> 里填写「豆包」那一栏。'
                                         : "画图和视频还没有开通，请让管理员在设置里填写豆包的 API key。";
    else if (!models.length) msg = state.user.admin ? `还没有启用${kind === "image" ? "画图" : "视频"}模型，请在 <a href="#" data-settings="media">设置 · 画图视频</a> 里添加。`
                                                   : "还没有可用的模型，请让管理员在设置里添加。";
    note.innerHTML = msg; note.hidden = !msg || !!DOC_KIND[docKind()].local;
    note.querySelectorAll("[data-settings]").forEach((a) => a.onclick = (e) => { e.preventDefault(); openSettings(a.dataset.settings); });
    $(`#${kind}-form .go`).disabled = !!msg;
  }
}

async function loadMedia(kind) {
  const items = (await api(`/api/media?kind=${kind}`)).items;
  state[`${kind}Items`] = items;
  renderGallery(kind, items);
  schedulePoll(kind, items.some((i) => i.status === "running"));
}

function ago(ts) {
  const s = Date.now() / 1000 - ts;
  if (s < 60) return "刚刚";
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`;
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`;
  const d = new Date(ts * 1000);
  return `${d.getMonth() + 1} 月 ${d.getDate()} 日`;
}

function renderGallery(kind, items) {
  const box = $(`#${kind}-gallery`); box.innerHTML = "";
  if (!items.length) {
    box.innerHTML = `<div class="empty">${kind === "image" ? "还没有作品。写一句话，画第一张吧。" : "还没有视频。"}</div>`;
    return;
  }
  for (const it of items) box.appendChild(tile(kind, it));
}

function tile(kind, it) {
  const el = document.createElement("figure");
  el.className = `tile ${it.status}`;
  if (it.status === "done" && it.url) {
    el.innerHTML = kind === "video"
      ? `<video src="${it.url}" controls playsinline preload="metadata"></video>`
      : `<button class="tile-open" aria-label="查看大图"><img src="${it.url}" alt="${esc(it.prompt || "")}" loading="lazy"></button>`;
  } else if (it.status === "running") {
    el.innerHTML = `<div class="tile-wait"><span class="spinner"></span><span>${kind === "video" ? "正在制作，做好了会自动出现" : "正在画…"}</span></div>`;
  } else {
    el.innerHTML = `<div class="tile-wait failed"><strong>没有成功</strong><span>${esc(it.error || "生成失败")}</span></div>`;
  }
  const cap = document.createElement("figcaption");
  cap.innerHTML = `<p>${it.source_id ? `<span class="tag">${kind === "video" ? "从图片" : "改图"}</span>` : ""}${esc(it.prompt || "")}</p>
    <span class="when">${ago(it.created_at)}</span><div class="tile-actions"></div>`;
  const actions = cap.querySelector(".tile-actions");
  const add = (label, fn, cls = "") => { const b = document.createElement("button"); b.type = "button"; b.className = `mini ${cls}`; b.textContent = label; b.onclick = fn; actions.appendChild(b); };
  if (it.status === "done" && it.url) {
    if (kind === "image") el.classList.add("tap-view");   // on phones the buttons live in the big view
    const a = document.createElement("a"); a.className = "mini"; a.href = it.url; a.textContent = "保存";
    a.download = `mao-${it.id}.${kind === "video" ? "mp4" : "png"}`; actions.appendChild(a);
    if (kind === "image") { add("修改", () => useSource("image", it)); add("做成视频", () => { showView("video"); useSource("video", it); }); }
  }
  if (it.status === "failed") add("再试一次", () => retry(kind, it));
  if (it.status !== "running") add("删除", () => removeMedia(kind, it), "danger");
  el.appendChild(cap);
  const open = el.querySelector(".tile-open");
  if (open) open.onclick = () => openViewer(kind, it);
  return el;
}

function openViewer(kind, it) {
  $("#viewer-media").innerHTML = `<img src="${it.url}" alt="${esc(it.prompt || "")}">`;
  $("#viewer-prompt").textContent = it.prompt || "";
  const bar = $("#viewer-actions"); bar.innerHTML = "";
  const a = document.createElement("a"); a.className = "ghost"; a.href = it.url; a.download = `mao-${it.id}.png`; a.textContent = "保存到手机 / 电脑"; bar.appendChild(a);
  const add = (label, fn) => { const b = document.createElement("button"); b.className = "ghost"; b.textContent = label; b.onclick = () => { $("#viewer").close(); fn(); }; bar.appendChild(b); };
  add("修改这张", () => useSource("image", it));
  add("做成视频", () => { showView("video"); useSource("video", it); });
  add("删除", () => removeMedia(kind, it));
  $("#viewer").showModal();
}
$("#viewer").addEventListener("click", (e) => { if (e.target.id === "viewer" || e.target.id === "viewer-media") $("#viewer").close(); });

function goLabel(kind) { return kind === "video" ? "开始制作" : state.source.image ? "开始修改" : "开始画"; }

function useSource(kind, it) {
  state.source[kind] = it ? { id: it.id, url: it.url } : null;
  const box = $(`#${kind}-source`);
  box.hidden = !it;
  if (it) box.querySelector("img").src = it.url;
  $(`#${kind}-form .go`).textContent = goLabel(kind);
  if (it) { $(`#view-${kind}`).scrollTo({ top: 0, behavior: "smooth" }); $(`#${kind}-form textarea`).focus({ preventScroll: true }); }
}
for (const kind of ["image", "video"]) {
  $(`#${kind}-source [data-clear]`).onclick = () => useSource(kind, null);
  $(`#${kind}-form [data-file]`).onchange = async (e) => {
    const file = e.target.files[0]; e.target.value = "";
    if (file) await uploadPhoto(kind, file);
  };
}

// Phone photos are big: shrink to at most 2048 px on the long side before sending.
// (Read as a data: URL; the page's security policy does not allow blob: images.)
function shrinkPhoto(file) {
  const bad = () => new Error("这张照片打不开，请换一张 JPG 或 PNG 照片");
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(bad());
    reader.onload = () => {
      const img = new Image();
      img.onload = () => {
        const scale = Math.min(1, 2048 / Math.max(img.naturalWidth, img.naturalHeight));
        const c = document.createElement("canvas");
        c.width = Math.round(img.naturalWidth * scale); c.height = Math.round(img.naturalHeight * scale);
        c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
        resolve(c.toDataURL("image/jpeg", 0.9));
      };
      img.onerror = () => reject(bad());
      img.src = reader.result;
    };
    reader.readAsDataURL(file);
  });
}

async function uploadPhoto(kind, file) {
  const label = $(`#${kind}-form [data-file]`).closest("label");
  label.classList.add("busy");
  try {
    const data = await shrinkPhoto(file);
    const d = await api("/api/uploads", { method: "POST", body: { data } });
    useSource(kind, d.item);
  } catch (err) { toast(err.message); }
  finally { label.classList.remove("busy"); }
}

async function createMedia(kind, body) {
  const form = $(`#${kind}-form`), go = form.querySelector(".go");
  const box = $(`#${kind}-gallery`);
  go.disabled = true;
  go.textContent = kind === "image" ? "正在画…" : "正在提交…";
  let placeholder = null;
  if (kind === "image") {   // the picture comes back in the same request; show where it will appear
    placeholder = tile("image", { id: 0, status: "running", prompt: body.prompt, created_at: Date.now() / 1000, source_id: body.source_id });
    box.querySelector(".empty")?.remove();
    box.prepend(placeholder);
  }
  try {
    const d = await api(kind === "image" ? "/api/images" : "/api/videos", { method: "POST", body });
    if (d.item.status === "failed") toast(d.item.error || "没有成功");
    else { form.elements.prompt.value = ""; useSource(kind, null); }
    await loadMedia(kind);
  } catch (err) { placeholder?.remove(); toast(err.message); }
  finally { go.disabled = false; go.textContent = goLabel(kind); }
}

$("#image-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const f = e.target.elements, prompt = f.prompt.value.trim();
  if (!prompt) return f.prompt.focus();
  createMedia("image", { prompt, size: f.size.value, model: f.model.value, source_id: state.source.image && state.source.image.id });
});
$("#video-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const f = e.target.elements, prompt = f.prompt.value.trim();
  if (!prompt) return f.prompt.focus();
  createMedia("video", { prompt, ratio: f.ratio.value, duration: Number(f.duration.value), model: f.model.value,
                         source_id: state.source.video && state.source.video.id });
});

function retry(kind, it) {
  const form = $(`#${kind}-form`);
  form.elements.prompt.value = it.prompt || "";
  if (it.source_id) useSource(kind, { id: it.source_id, url: `/media/${it.source_id}` });
  $(`#view-${kind}`).scrollTo({ top: 0, behavior: "smooth" });
  form.elements.prompt.focus({ preventScroll: true });
}

async function removeMedia(kind, it) {
  if (!confirm(kind === "video" ? "删除这段视频？删除后找不回来。" : "删除这张图？删除后找不回来。")) return;
  try { await api(`/api/media/${it.id}`, { method: "DELETE" }); await loadMedia(kind); } catch (err) { toast(err.message); }
}

$("#video-pick").onclick = async () => {
  const grid = $("#picker-grid");
  grid.innerHTML = '<div class="empty">正在加载…</div>';
  $("#picker").showModal();
  try {
    const items = (await api("/api/media")).items.filter((i) => i.kind !== "video" && i.status === "done" && i.url);
    grid.innerHTML = items.length ? "" : '<div class="empty">还没有图片。可以先去「画图」画一张，或者点「开头画面」上传照片。</div>';
    for (const it of items) {
      const b = document.createElement("button"); b.type = "button";
      b.innerHTML = `<img src="${it.url}" alt="${esc(it.prompt || "上传的照片")}" loading="lazy">`;
      b.onclick = () => { $("#picker").close(); useSource("video", it); };
      grid.appendChild(b);
    }
  } catch (err) { grid.innerHTML = `<div class="empty">${esc(err.message)}</div>`; }
};

// ---- settings: pictures / video models, remote access ---------------------------------------
function renderMediaModels(imageModels, videoModels) {
  const box = $("#tab-media");
  box.innerHTML = `<p class="small-note">画图和视频都用豆包（火山方舟）：先在「聊天」标签里填好豆包的 API key，再在这里列出要用的模型。
    模型名在方舟控制台的「模型广场」里查，需要先在控制台开通。</p>`;
  for (const [kind, title, models, eg] of [["image", "画图模型（Seedream）", imageModels, "doubao-seedream-4-0-250828"],
                                         ["video", "视频模型（Seedance）", videoModels, "doubao-seedance-1-0-pro-250528"]]) {
    const card = document.createElement("div"); card.className = "card";
    card.innerHTML = `<div class="card-head"><h3>${title}</h3></div><div class="chips"></div>
      <div class="row"><input placeholder="添加模型名，例如 ${eg}" aria-label="添加模型"><button class="ghost" data-a="add">添加</button></div>`;
    const list = [...models], chips = card.querySelector(".chips"), input = card.querySelector("input");
    const draw = () => {
      chips.innerHTML = list.length ? "" : '<span class="small-note">还没有模型</span>';
      list.forEach((m, i) => {
        const c = document.createElement("span"); c.className = "chip"; c.innerHTML = `${esc(m)}<button aria-label="移除">×</button>`;
        c.querySelector("button").onclick = () => { list.splice(i, 1); save(); };
        chips.appendChild(c);
      });
    };
    const save = async () => { try { await api("/api/admin/media-models", { method: "POST", body: { kind, models: list } }); draw(); } catch (err) { toast(err.message); } };
    card.querySelector("[data-a=add]").onclick = () => { const v = input.value.trim(); if (v && !list.includes(v)) { list.push(v); input.value = ""; save(); } };
    draw(); box.appendChild(card);
  }
}

async function renderRemote() {
  const box = $("#tab-remote");
  box.innerHTML = '<p class="small-note">正在检查…</p>';
  let d;
  try { d = await api("/api/admin/remote"); } catch (err) { box.innerHTML = `<p class="error">${esc(err.message)}</p>`; return; }
  const steps = `<ol class="steps">
      <li>在这台电脑上安装 Tailscale（<a href="https://tailscale.com/download" target="_blank" rel="noopener noreferrer">tailscale.com/download</a>），用一个账号登录。</li>
      <li>在你的手机上也安装 Tailscale（App Store / 应用商店搜 Tailscale），<strong>用同一个账号</strong>登录，打开连接开关。</li>
      <li>关掉 MAO 的黑色窗口，双击 <code>run_web.cmd</code> 重新打开。</li>
      <li>回到这一页，就能看到手机上要打开的地址。</li></ol>`;
  const head = `<p class="small-note">外出时用你自己的手机或平板打开 MAO。走的是 Tailscale（只有你登录过的设备能连上的私人网络），不对外开放端口，别人打不开。</p>`;
  if (d.urls.length) {
    box.innerHTML = head + `<div class="card"><div class="card-head"><h3>已经可以在外面打开</h3><span class="badge on">已开启</span></div>
      <p class="small-note">手机连上 Tailscale 以后，在浏览器里打开：</p>
      ${d.urls.map((u) => `<div class="url-row"><code>${esc(u)}</code><button class="ghost" data-url="${esc(u)}">复制</button></div>`).join("")}
      <p class="small-note">建议在手机浏览器里「添加到主屏幕」，以后点图标就能打开。家里人要用，也需要在他们的手机上装 Tailscale 并登录你的账号（或者你在 Tailscale 里邀请他们）。</p></div>`;
    box.querySelectorAll("[data-url]").forEach((b) => b.onclick = async () => {
      try { await navigator.clipboard.writeText(b.dataset.url); toast("已复制"); } catch { toast("复制失败，请手动抄下地址"); }
    });
  } else if (d.tailscale.length) {
    box.innerHTML = head + `<div class="card"><div class="card-head"><h3>还差一步</h3><span class="badge">未开启</span></div>
      <p class="small-note">这台电脑已经装好 Tailscale（地址 ${d.tailscale.map(esc).join("、")}），但 MAO 这次没有在这个地址上打开。
      请关掉 MAO 的黑色窗口，双击 <code>run_web.cmd</code> 重新打开，再回到这一页。</p></div>`;
  } else {
    box.innerHTML = head + `<div class="card"><div class="card-head"><h3>还没有开启</h3><span class="badge">未开启</span></div>${steps}</div>`;
  }
}

// ---- settings: phone alerts (ntfy; orch/notify.py through /api/admin/notify) --------------------------------
// what happened, by the kinds the server records (orch/notify.py KINDS)
const KIND_LABEL = {
  discuss_waiting: "讨论等你决定", discuss_failed: "讨论出错或中断", discuss_stuck: "讨论可能卡住",
  cowork_blocker: "Cowork 遇到阻碍", cowork_question: "Cowork 有问题", cowork_done: "Cowork 做完了",
  cowork_silent: "Cowork 很久没回报", gpt_waiting: "ChatGPT 等你按发送", cc_permission: "Claude Code 等你批准或回答",
  cc_input: "Claude Code 停下来等你", test: "测试提醒",
};
// the event groups (orch/notify.py GROUPS) and the number each one is tuned by: [threshold, text before, unit]
const NOTIFY_EVENTS = [
  { key: "discussion", label: "讨论等你决定、出错或卡住", num: ["stuck_minutes", "多久没有动静算卡住：", "分钟"] },
  { key: "cowork", label: "Cowork 遇到阻碍、有问题、做完了" },
  { key: "cowork_silent", label: "Cowork 领走方案后很久没回报（弱信号）", num: ["cowork_silent_hours", "多久没有回报算很久：", "小时"] },
  { key: "gpt", label: "ChatGPT 里有任务等你按发送", num: ["gpt_wait_minutes", "等了多久才提醒：", "分钟"] },
  { key: "cc_permission", label: "Claude Code 等你批准或回答（权限、表单）" },
  { key: "cc_input", label: "Claude Code 做完一轮、停下来等你（每次回答后都会有，容易多）" },
];
const AWAY = ["cc_away_minutes", "你在这台电脑前时不发 Claude Code 的提醒：最近", "分钟内动过键盘或鼠标就不发（0 = 总是发）"];
const HOOK_STATE = { installed: "已安装", outdated: "需要重新安装（MAO 或 Python 换了位置）", missing: "还没有安装",
                     no_claude: "这台电脑上没有找到 Claude Code", broken: "Claude Code 的 settings.json 读不懂，先修好它" };
const SEND_ERROR = { rate_limited: "太多了，稍后再发", busy: "没有发出（文件被占用）" };

async function renderNotify() {
  const box = $("#tab-notify");
  box.innerHTML = '<p class="small-note">正在检查…</p>';
  let d;
  try { d = await api("/api/admin/notify"); } catch (err) { box.innerHTML = `<p class="error">${esc(err.message)}</p>`; return; }
  box.innerHTML = `<p class="small-note">有事等你的时候（讨论要你决定、Cowork 卡住了、Claude Code 等你批准……），MAO 往你的手机发一条短提醒。用的是 ntfy App，免费，不用注册。</p>
    <div class="card" id="nt-main"></div><div class="card" id="nt-events"></div><div class="card" id="nt-recent"></div>`;
  notifyMain(d); notifyEvents(d); notifyRecent(d);
}
const notifyPost = (body) => api("/api/admin/notify", { method: "POST", body });   // answers with the whole state again

// on/off, where the pushes go (server, topic, token) and how to get them on the phone
function notifyMain(d) {
  const card = $("#nt-main");
  card.innerHTML = `<div class="card-head"><h3>手机提醒</h3><span class="badge ${d.enabled ? "on" : ""}">${d.enabled ? "已开启" : "未开启"}</span></div>
    <label class="switch"><input type="checkbox" data-a="enable" ${d.enabled ? "checked" : ""}><span>开启手机提醒</span></label>
    <div class="nt-field"><span class="nt-label">服务器</span>
      <div class="row"><input name="server" value="${esc(d.server)}" autocomplete="off" spellcheck="false" aria-label="服务器">
        <button class="ghost" data-a="server">保存</button></div>
      <p class="small-note">自建服务器才需要改</p></div>
    <div class="nt-field"><span class="nt-label">主题</span>
      <div class="url-row"><code>${esc(d.topic)}</code><button class="ghost" data-a="copy">复制</button><button class="ghost" data-a="topic">换一个</button></div>
      <p class="small-note">订阅地址：<a class="nt-sub" href="${esc(d.subscribe_url)}" target="_blank" rel="noopener noreferrer">${esc(d.subscribe_url)}</a></p></div>
    <div class="nt-field"><span class="nt-label">访问令牌</span>
      <div class="row"><input type="password" name="token" autocomplete="off" aria-label="访问令牌"
          placeholder="${d.token_set ? "已设置（填新的可以替换）" : "没有设置"}">
        <button class="ghost" data-a="token">保存</button>${d.token_set ? '<button class="ghost" data-a="token-clear">删除</button>' : ""}</div>
      <p class="small-note">只有自建并设了权限的服务器需要</p></div>
    <p class="small-note">提醒里只写发生了什么和编号，不会有需求、回答、文件内容或密钥。ntfy.sh 是别人的服务器：知道主题名的人都能收到，别把主题告诉别人。</p>
    <ol class="steps"><li>在手机上安装 ntfy（App Store 或 Google Play 搜 ntfy）</li>
      <li>打开 ntfy，点 +，主题填上面这一串；用自己的服务器时打开「使用其他服务器」，填服务器地址</li>
      <li>回到这里点「发一条测试提醒」</li></ol>
    <div class="row"><button class="primary" data-a="test">发一条测试提醒</button></div>
    <p class="error" role="alert"></p>`;
  const err = card.querySelector(".error"), sw = card.querySelector('[data-a="enable"]');
  sw.onchange = async () => {
    sw.disabled = true; err.textContent = "";
    try { const r = await notifyPost({ action: "save", enabled: sw.checked }); notifyMain(r); toast(r.enabled ? "已开启手机提醒" : "已关闭手机提醒"); }
    catch (e) { sw.checked = !sw.checked; sw.disabled = false; err.textContent = e.message; }
  };
  card.onclick = async (e) => {
    const b = e.target.closest("button[data-a]"); if (!b) return;
    const a = b.dataset.a, token = card.querySelector("[name=token]");
    err.textContent = "";
    if (a === "copy") {
      try { await navigator.clipboard.writeText(d.topic); toast("已复制"); } catch { toast("复制失败，请手动选择文字"); }
      return;
    }
    if (a === "token" && !token.value.trim()) { token.focus(); return; }
    if (a === "topic" && !confirm("换一个主题？换了以后，手机上要重新订阅新的主题，旧的主题不会再收到提醒。")) return;
    if (a === "token-clear" && !confirm("删除访问令牌？")) return;
    const body = { server: { action: "save", server: card.querySelector("[name=server]").value },
                   topic: { action: "new_topic" }, token: { action: "token", token: token.value.trim() },
                   "token-clear": { action: "token", token: "" }, test: { action: "test" } }[a];
    b.disabled = true;
    if (a === "test") b.textContent = "正在发送…";
    try {
      const r = await notifyPost(body);
      if (a !== "test") {
        notifyMain(r);
        toast({ server: "已保存", topic: "已换成新的主题，请在手机上重新订阅", token: "已保存访问令牌", "token-clear": "已删除访问令牌" }[a]);
        return;
      }
      notifyRecent(r);
      if (r.test.ok) toast("测试提醒已发出，看看手机");
      else err.textContent = `测试提醒没有发出：${r.test.error}`;
    } catch (e2) { err.textContent = e2.message; }
    b.disabled = false;
    if (a === "test") b.textContent = "发一条测试提醒";
  };
}

function notifyNumber(d, [key, before, unit], cls = "") {
  const [lo, hi] = d.limits[key] || [0, 999];
  return `<label class="nt-num ${cls}"><span>${before}</span><input type="number" inputmode="numeric" name="${key}" min="${lo}" max="${hi}" step="1"
    value="${esc(String(d.thresholds[key]))}"><span>${unit}</span></label>`;
}

// which events push, their thresholds, the language of the pushes; the Claude Code hook
function notifyEvents(d) {
  const card = $("#nt-events");
  const row = (ev) => `<div class="nt-event"><label class="nt-check"><input type="checkbox" name="${ev.key}" ${d.events[ev.key] ? "checked" : ""}>
    <span>${ev.label}</span></label>${ev.num ? notifyNumber(d, ev.num) : ""}</div>`;
  const shown = NOTIFY_EVENTS.filter((ev) => ev.key !== "gpt" || d.gpt_available);   // the GPT bridge is not in every copy
  card.innerHTML = `<div class="card-head"><h3>提醒哪些事</h3></div>
    ${shown.filter((ev) => !ev.key.startsWith("cc_")).map(row).join("")}
    <div class="nt-group"><h4>Claude Code</h4>
      ${shown.filter((ev) => ev.key.startsWith("cc_")).map(row).join("")}
      ${notifyNumber(d, AWAY, "away")}
      <div class="nt-hook"></div></div>
    <label class="nt-lang"><span>提醒用的语言</span>
      <select name="lang"><option value="zh" data-no-tr>中文</option><option value="en" data-no-tr>English</option></select></label>
    <p class="error" role="alert"></p>
    <div class="row-end"><button class="primary" data-a="save">保存</button></div>`;
  card.querySelector("[name=lang]").value = d.lang;
  notifyHook(d);
  const save = card.querySelector('[data-a="save"]'), err = card.querySelector(":scope > .error");
  save.onclick = async () => {
    const events = {}, thresholds = {};
    card.querySelectorAll(".nt-check input").forEach((x) => (events[x.name] = x.checked));
    card.querySelectorAll(".nt-num input").forEach((x) => (thresholds[x.name] = x.value.trim()));
    err.textContent = ""; save.disabled = true;
    try {
      const r = await notifyPost({ action: "save", events, thresholds, lang: card.querySelector("[name=lang]").value });
      notifyEvents(r); toast("已保存");
    } catch (e) { err.textContent = e.message; save.disabled = false; }
  };
}

function notifyHook(d, result) {
  const box = $("#nt-events .nt-hook"), st = d.hook.state;
  const act = st === "installed" ? "hook_uninstall" : st === "missing" || st === "outdated" ? "hook_install" : "";
  const badge = st === "installed" ? "on" : st === "missing" || st === "no_claude" ? "" : "bad";
  box.innerHTML = `<div class="nt-hookrow"><span>Claude Code 钩子：</span><span class="badge ${badge}">${HOOK_STATE[st] || esc(st)}</span>
      ${act ? `<button class="ghost" data-a="${act}">${act === "hook_install" ? "安装" : "移除"}</button>` : ""}</div>
    <p class="small-note">上面两项 Claude Code 提醒要靠这个钩子。会在这台电脑的 Claude Code 设置里加一个钩子，改之前先备份 settings.json</p>
    ${result && result.backup ? `<p class="small-note">原来的 settings.json 已备份为 <code>${esc(result.backup)}</code></p>` : ""}
    <p class="error" role="alert"></p>`;
  const b = box.querySelector("button[data-a]");
  if (!b) return;
  b.onclick = async () => {
    const install = b.dataset.a === "hook_install";
    if (!install && !confirm("从 Claude Code 的设置里移除 MAO 的钩子？")) return;
    b.disabled = true;
    try {
      const r = await notifyPost({ action: b.dataset.a });
      notifyHook(r, r.hook_result);
      toast(install ? (r.hook_result.changed ? "已安装 Claude Code 钩子" : "钩子已经装好，没有改动")
                    : (r.hook_result.changed ? "已移除 Claude Code 钩子" : "没有找到 MAO 的钩子，没有改动"));
    } catch (e) { box.querySelector(".error").textContent = e.message; b.disabled = false; }
  };
}

function notifyResult(r) {
  if (r.ok) return '<span class="nt-ok" aria-label="已发出">✓</span>';
  if (SEND_ERROR[r.error]) return `<span class="nt-bad">${SEND_ERROR[r.error]}</span>`;
  return '<span class="nt-bad">没有发出：<span data-no-tr>' + esc(r.error) + "</span></span>";   // HTTP 403, network: …
}

// what was pushed lately, and whether the web app's background check runs (orch/watch.py)
function notifyRecent(d) {
  const card = $("#nt-recent"), w = d.watcher || {};
  const line = !w.running ? "后台检查：没有运行（网页版启动时会自动开始）"
    : w.last_tick ? `后台检查：正在运行，上次 ${ago(w.last_tick)}` : "后台检查：正在运行";
  const rows = d.recent.map((r) => `<li><span class="nt-when">${r.at ? ago(r.at) : ""}</span>
    <span class="nt-what">${KIND_LABEL[r.kind] || esc(r.kind)}</span>${r.ref ? `<span class="nt-ref" data-no-tr>${esc(r.ref)}</span>` : ""}
    ${notifyResult(r)}</li>`).join("");
  card.innerHTML = `<div class="card-head"><h3>最近的提醒</h3><button class="ghost" data-a="refresh">刷新</button></div>
    <p class="small-note">${line}</p>${w.error ? `<p class="small-note">最近一次出错：${esc(w.error)}</p>` : ""}
    ${rows ? `<ul class="nt-recent">${rows}</ul>` : '<p class="small-note">还没有发过提醒。</p>'}`;
  card.querySelector('[data-a="refresh"]').onclick = async () => {
    try { notifyRecent(await api("/api/admin/notify")); } catch (e) { toast(e.message); }
  };
}

// ---- settings: the old task dispatch (legacy switch) ---------------------------------------------
async function renderLegacy() {
  const box = $("#tab-legacy");
  let d;
  try { d = await api("/api/admin/legacy"); } catch (err) { box.innerHTML = `<p class="error">${esc(err.message)}</p>`; return; }
  const locked = d.source === "env";
  box.innerHTML = `<div class="card"><div class="card-head"><h3>旧的派活功能</h3><span class="badge ${d.enabled ? "on" : ""}">${d.enabled ? "开" : "关"}</span></div>
    <p class="small-note">MAO 早期自己拆任务、派给 AI、合并代码：项目页、聊天里的「交给项目」、Agent 页的控制面板、命令行的 task / run / plan 等。现在开发交给 Cowork，这些默认关闭；数据都还在，随时可以打开。</p>
    <label class="switch"><input type="checkbox" id="legacy-on" ${d.enabled ? "checked" : ""}${locked ? " disabled" : ""}><span>使用旧的派活功能</span></label>
    ${locked ? '<p class="small-note">现在由环境变量 MAO_LEGACY_DISPATCH 决定，这里改不了</p>' : ""}</div>`;
  const input = $("#legacy-on");
  input.onchange = async () => {
    const on = input.checked;
    if (!on && d.active_jobs > 0
        && !confirm(`还有 ${d.active_jobs} 个项目任务在运行。关闭后它们会自己跑完，但在网页上看不到，直到重新打开。确定关闭？`)) {
      input.checked = true; return;
    }
    input.disabled = true;
    try {
      await api("/api/admin/legacy", { method: "POST", body: { enabled: on } });
      state.user = (await api("/api/state")).user;
      roleChanged();
      toast(on ? "已打开旧的派活功能" : "已关闭旧的派活功能");
    } catch (err) { toast(err.message); }
    renderLegacy();
  };
}

// ---- documents ---------------------------------------------------------------------------
const DOC_KIND = {
  ask: { label: "问表格", file: "选择表格", accept: ".xlsx,.xlsm,.xls,.et,.csv,.tsv",
         ph: "想知道什么？例如：每个部门这个季度花了多少，从多到少，画个柱状图" },
  draft: { label: "写文档", ph: "要写什么？例如：写一份国庆值班安排的通知，10 月 1 日到 7 日，每天两人" },
  polish: { label: "改文档", file: "选择要改的文档", accept: ".docx,.doc,.wps,.rtf,.txt,.md",
            ph: "怎么改？不写就是润色。例如：压缩到 800 字，语气正式一些" },
  fill: { label: "填模板", file: "选择 Word 模板", accept: ".docx,.doc,.wps",
          ph: "要填什么？例如：十月月报，报告人王五，本月完成了三项工作……（模板里要填的地方写成 {{标题}}、{{日期}}）" },
  trace: { label: "资金追踪", files: "选择银行流水（可以多选）", accept: ".xlsx,.xlsm,.xls,.et,.csv,.tsv", local: true,
           ph: "（可以不填）已经知道的人或账号，用逗号隔开，就不算「未知去向」。例如：王五，6217001234123412345" },
  find: { label: "找信息", files: "选择要查的文件（可以多选）", accept: ".xlsx,.xlsm,.xls,.et,.csv,.tsv,.docx,.doc,.wps,.rtf,.txt,.md,.pdf", local: true,
           ph: "要找什么？名字、号码或词，用空格或逗号隔开。例如：张三 6222021234567890123" },
};
const DOC_STATUS = { running: ["正在做", "run"], done: ["完成", "on"], failed: ["没有完成", "bad"] };
state.docs = { file: null, materials: [], inputs: [], templates: [], template: null, ready: false };

function docKind() { return $("#docs-form").elements.kind.value; }

function setDocKind() {
  const kind = docKind(), k = DOC_KIND[kind], form = $("#docs-form");
  form.querySelectorAll("[data-for]").forEach((el) => (el.hidden = !el.dataset.for.split(" ").includes(kind)));
  form.elements.text.placeholder = k.ph;
  if (k.file) {
    $("#docs-file-label").textContent = k.file;
    form.querySelector("[data-file=main]").accept = k.accept;
    const f = state.docs.file;
    if (f && !k.accept.split(",").some((ext) => f.name.toLowerCase().endsWith(ext))) state.docs.file = null;
  }
  $("#docs-file-name").textContent = state.docs.file ? state.docs.file.name : "还没有选文件";
  if (k.files) {
    $("#docs-inputs-label").textContent = k.files;
    form.querySelector("[data-file=inputs]").accept = k.accept;
    if (state.docs.inputsFor !== kind) state.docs.inputs = [];       // statements and files to search are separate picks
    state.docs.inputsFor = kind;
    drawInputs();
  }
  $("#docs-notice").hidden = !!k.local || !$("#docs-notice").innerHTML;
  if (kind === "polish" || kind === "draft") form.elements.style.hidden = false;
  $("#docs-form .go").disabled = !state.docs.ready && !k.local;
  drawTemplate();
}

// ---- writing templates (写文档): pick one, fill a few boxes ----
function drawTemplates() {
  const box = $("#docs-templates"); box.innerHTML = "";
  for (const t of state.docs.templates) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "pill tpl" + (state.docs.template === t.id ? " on" : "");
    b.title = t.hint; b.textContent = t.name;
    b.onclick = () => { state.docs.template = state.docs.template === t.id ? null : t.id; drawTemplates(); drawTemplate(true); };
    box.appendChild(b);
  }
}
function drawTemplate(focus) {
  const box = $("#docs-tpl-fields"), form = $("#docs-form");
  const t = docKind() === "draft" && state.docs.templates.find((x) => x.id === state.docs.template);
  box.hidden = !t; box.innerHTML = "";
  if (!t) { if (docKind() === "draft") form.elements.text.placeholder = DOC_KIND.draft.ph; return; }
  const long = /work|topics|process|why|results|require/;
  for (const f of t.fields) {
    const label = document.createElement("label");
    label.className = long.test(f.key) ? "wide" : "";
    label.innerHTML = `${esc(f.label)}${long.test(f.key) ? `<textarea rows="2" data-key="${f.key}"></textarea>` : `<input data-key="${f.key}">`}`;
    label.querySelector("[data-key]").placeholder = f.placeholder || "";
    box.appendChild(label);
  }
  form.elements.text.placeholder = "其他要求（可以不填），例如：语气亲切一点、控制在 300 字以内";
  form.elements.style.value = t.style;
  if (focus) box.querySelector("[data-key]").focus();
}
$("#docs-form").querySelectorAll("[name=kind]").forEach((r) => (r.onchange = setDocKind));
$("#docs-form").querySelectorAll("[data-quick]").forEach((b) => (b.onclick = () => {
  const t = $("#docs-form").elements.text; t.value = b.dataset.quick; t.focus();
}));
try { $("#docs-form").elements.sensitive.checked = localStorageGet("mao.sensitive") === "1"; } catch { /* ignore */ }
$("#docs-form").elements.sensitive.onchange = (e) => localStorageSet("mao.sensitive", e.target.checked ? "1" : "0");

function readFile(file) {
  return new Promise((resolve, reject) => {
    if (file.size > 10_000_000) return reject(new Error("文件太大了（最多 10 MB）"));
    const r = new FileReader();
    r.onerror = () => reject(new Error("这个文件打不开"));
    r.onload = () => resolve(r.result);
    r.readAsDataURL(file);
  });
}
async function uploadDoc(file) { return api("/api/docs/upload", { method: "POST", body: { name: file.name, data: await readFile(file) } }); }

$("#docs-form [data-file=main]").onchange = async (e) => {
  const file = e.target.files[0]; e.target.value = ""; if (!file) return;
  const label = e.target.closest("label"); label.classList.add("busy");
  try { state.docs.file = await uploadDoc(file); $("#docs-file-name").textContent = state.docs.file.name; }
  catch (err) { toast(err.message); }
  finally { label.classList.remove("busy"); }
};
$("#docs-form [data-file=materials]").onchange = async (e) => {
  const files = [...e.target.files]; e.target.value = "";
  const label = e.target.closest("label"); label.classList.add("busy");
  try { for (const f of files) { if (state.docs.materials.length < 5) state.docs.materials.push(await uploadDoc(f)); } }
  catch (err) { toast(err.message); }
  finally { label.classList.remove("busy"); drawMaterials(); }
};
$("#docs-form [data-file=inputs]").onchange = async (e) => {
  const files = [...e.target.files]; e.target.value = "";
  const label = e.target.closest("label"); label.classList.add("busy");
  try { for (const f of files) { if (state.docs.inputs.length < 30) state.docs.inputs.push(await uploadDoc(f)); } }
  catch (err) { toast(err.message); }
  finally { label.classList.remove("busy"); drawInputs(); }
};
function drawInputs() {
  const box = $("#docs-inputs"); box.innerHTML = "";
  state.docs.inputs.forEach((m, i) => {
    const c = document.createElement("span"); c.className = "chip"; c.innerHTML = `${esc(m.name)}<button type="button" aria-label="移除">×</button>`;
    c.querySelector("button").onclick = () => { state.docs.inputs.splice(i, 1); drawInputs(); };
    box.appendChild(c);
  });
  $("#docs-inputs-count").textContent = state.docs.inputs.length ? `已选 ${state.docs.inputs.length} 个文件` : "还没有选文件";
}
function drawMaterials() {
  const box = $("#docs-materials"); box.innerHTML = "";
  state.docs.materials.forEach((m, i) => {
    const c = document.createElement("span"); c.className = "chip"; c.innerHTML = `${esc(m.name)}<button type="button" aria-label="移除">×</button>`;
    c.querySelector("button").onclick = () => { state.docs.materials.splice(i, 1); drawMaterials(); };
    box.appendChild(c);
  });
}

async function loadDocs(first) {
  const d = await api("/api/docs");
  const note = $("#docs-notice");
  const msg = d.ready ? "" : state.user.admin ? '还没有可用的模型：请在 <a href="#" data-settings="models">设置 · 聊天</a> 里填一个 API key（DeepSeek 或千问就行）。'
                                             : "还没有可用的模型，请让管理员在设置里填 API key。";
  note.innerHTML = msg; note.hidden = !msg;
  note.querySelectorAll("[data-settings]").forEach((a) => a.onclick = (e) => { e.preventDefault(); openSettings(a.dataset.settings); });
  state.docs.ready = d.ready;
  $("#docs-form .go").disabled = !d.ready && !DOC_KIND[docKind()].local;
  if (first) { state.docs.templates = d.templates || []; drawTemplates(); setDocKind(); }
  renderDocJobs(d.jobs);
  schedulePoll("docs", d.jobs.some((j) => j.status === "running"));
}

function fileLink(j, name, label) { return `<a class="mini" href="/docs/${j.id}/${encodeURIComponent(name)}" download="${esc(name)}">${label}</a>`; }

function renderDocJobs(jobs) {
  const box = $("#docs-jobs");
  const open = new Set([...box.querySelectorAll("details[open]")].map((x) => x.dataset.id));
  box.innerHTML = jobs.length ? "" : '<div class="empty">还没有记录。上传一个表格问一句，或者让它写一份通知试试。</div>';
  for (const j of jobs) {
    const [label, cls] = DOC_STATUS[j.status] || [j.status, ""];
    const r = j.result || {}, files = r.files || {}, inp = j.input || {};
    const card = document.createElement("div"); card.className = "job doc-job";
    const many = inp.files || [];
    const tags = [inp.file, many.length > 3 ? `${many.length} 个文件` : many.join("、"), ...(inp.materials || []).map((m) => `材料：${m}`),
                  inp.style === "gongwen" ? "公文版式" : "", inp.sensitive ? "敏感模式" : "", j.kind === "trace" && inp.days !== undefined ? `${inp.days} 天内到账` : ""].filter(Boolean);
    let body = "";
    if (j.status === "done" && j.kind === "ask") {
      body += `<p class="doc-title">${esc(r.title || "")}</p>`;
      if (r.rows && r.rows.length === 1 && r.headers.length <= 3) {
        body += `<p class="doc-answer">${r.headers.map((h, i) => `${esc(h)} <strong>${esc(r.rows[0][i])}</strong>`).join("　")}</p>`;
      } else if (r.rows) {
        body += `<div class="table-wrap"><table><thead><tr>${r.headers.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>`
          + r.rows.map((row) => `<tr>${row.map((v) => `<td>${esc(v)}</td>`).join("")}</tr>`).join("") + "</tbody></table></div>";
        if (r.total > r.rows.length) body += `<p class="job-meta">共 ${r.total} 行，完整结果在 Excel 里</p>`;
      }
      if (r.note) body += `<p class="job-note">${esc(r.note)}</p>`;
      if (files.chart) body += `<img class="doc-chart" src="/docs/${j.id}/${encodeURIComponent(files.chart)}" alt="${esc(r.title || "图表")}" loading="lazy">`;
    } else if (j.status === "done" && (j.kind === "trace" || j.kind === "find")) {
      body += `<ul class="doc-summary">${(r.summary || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`;
      if (r.notes && r.notes.length) body += `<p class="job-note">${r.notes.map(esc).join("<br>")}</p>`;
      for (const t of r.tables || (r.table && r.table.rows.length ? [r.table] : [])) {
        if (r.tables) body += `<p class="doc-table-title">${esc(t.title)}</p>`;
        body += `<div class="table-wrap"><table><thead><tr>${t.headers.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>`
          + t.rows.map((row) => `<tr>${row.map((v) => `<td>${esc(v)}</td>`).join("")}</tr>`).join("") + "</tbody></table></div>";
        if (t.total > t.rows.length) body += `<p class="job-meta">共 ${t.total} 行，完整结果在 Excel 里</p>`;
      }
      if (files.chart) body += `<img class="doc-chart" src="/docs/${j.id}/${encodeURIComponent(files.chart)}" alt="资金流向图" loading="lazy">`;
    } else if (j.status === "done" && j.kind === "fill") {
      body += `<dl class="doc-fields">${Object.entries(r.fields || {}).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v ? esc(v) : '<span class="muted">（没填）</span>'}</dd>`).join("")}</dl>`;
      if (r.empty && r.empty.length) body += `<p class="job-note">这些没有填：${r.empty.map(esc).join("、")}，下载后在 Word 里补上。</p>`;
    } else if (j.status === "done") {
      body += `<details data-id="${j.id}"${open.has(String(j.id)) ? " open" : ""}><summary>看内容（约 ${r.count || 0} 字）</summary><div class="doc-preview">${markdown(r.text || "")}</div></details>`;
    } else if (j.status === "failed") {
      body += `<p class="job-note">${esc(j.error || "没有成功")}</p>`;
    }
    const downloads = [files.docx ? fileLink(j, files.docx, "下载 Word") : "", files.xlsx ? fileLink(j, files.xlsx, "下载 Excel") : "",
                       files.chart ? fileLink(j, files.chart, "下载图表") : ""].join("");
    card.innerHTML = `
      <div class="job-head"><span class="badge ${cls}">${cls === "run" ? '<span class="spinner small"></span>' : ""}${label}</span>
        <h3>${esc(DOC_KIND[j.kind] ? DOC_KIND[j.kind].label : j.kind)}${j.request ? "：" + esc(j.request.slice(0, 60)) : ""}</h3></div>
      <p class="job-meta">${tags.map(esc).join(" · ")}${tags.length ? " · " : ""}${ago(j.created_at)}</p>
      ${body}
      <div class="tile-actions">${downloads}${j.status !== "running" ? '<button type="button" class="mini danger" data-del>删除</button>' : ""}</div>
      ${j.status === "done" && DOC_KIND[j.kind] && DOC_KIND[j.kind].local ? '<p class="job-meta">全部在这台电脑上算，没有发给任何 AI</p>' : ""}
      ${j.status === "done" && !(DOC_KIND[j.kind] && DOC_KIND[j.kind].local) ? `<p class="job-meta">${esc(r.masked || "")}${r.model ? ` · ${esc(r.model)}` : ""}</p>` : ""}`;
    const del = card.querySelector("[data-del]");
    if (del) del.onclick = async () => {
      if (!confirm("删除这条记录和它生成的文件？")) return;
      try { await api(`/api/docs/${j.id}`, { method: "DELETE" }); await loadDocs(false); } catch (err) { toast(err.message); }
    };
    box.appendChild(card);
  }
}

$("#docs-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target, kind = docKind(), go = f.querySelector(".go");
  if (DOC_KIND[kind].file && !state.docs.file) return toast("请先" + DOC_KIND[kind].file);
  if (DOC_KIND[kind].local) return submitLocal(f, kind, go);
  const text = f.elements.text.value.trim();
  const tpl = kind === "draft" && state.docs.template;
  const fields = {};
  if (tpl) $("#docs-tpl-fields").querySelectorAll("[data-key]").forEach((x) => { if (x.value.trim()) fields[x.dataset.key] = x.value.trim(); });
  if (tpl && !Object.keys(fields).length) return toast("请至少填一项");
  if (!text && !tpl && kind !== "polish") return f.elements.text.focus();
  const body = { kind, text, template: tpl || undefined, fields, file: state.docs.file && state.docs.file.file, style: f.elements.style.value,
                 sensitive: f.elements.sensitive.checked, mask: f.elements.mask.value,
                 materials: kind === "draft" || kind === "fill" ? state.docs.materials.map((m) => m.file) : [] };
  go.disabled = true; go.textContent = "正在提交…";
  try {
    await api("/api/docs", { method: "POST", body });
    f.elements.text.value = "";
    $("#docs-tpl-fields").querySelectorAll("[data-key]").forEach((x) => (x.value = ""));
    toast("已经开始，做好了会出现在下面");
    await loadDocs(false);
  } catch (err) { toast(err.message); }
  finally { go.disabled = false; go.textContent = "开始"; }
});

async function submitLocal(f, kind, go) {
  if (!state.docs.inputs.length) return toast(kind === "trace" ? "请先选择银行流水" : "请先选择要查的文件");
  const text = f.elements.text.value.trim();
  const types = [...f.querySelectorAll("[name=ftype]:checked")].map((x) => x.value);
  if (kind === "find" && !text && !types.length) return f.elements.text.focus();
  const body = { kind, text, files: state.docs.inputs.map((m) => m.file), days: Number(f.elements.days.value),
                 types, all: f.elements.allwords.checked, lang: I18N.lang };
  go.disabled = true; go.textContent = "正在提交…";
  try {
    await api("/api/docs", { method: "POST", body });
    toast("已经开始，做好了会出现在下面");
    await loadDocs(false);
  } catch (err) { toast(err.message); }
  finally { go.disabled = false; go.textContent = "开始"; }
}

// ---- agents (admin) -----------------------------------------------------------------------
const AGENT_STATE = { idle: ["空闲", ""], working: ["工作中", "run"], paused: ["已暂停", "bad"], cooling: ["暂时停用", "bad"] };
const TASK_STATUS = { queued: "等待开始", running: "AI 正在写", verified: "检查通过", merged: "已合并进项目", failed: "失败",
                      blocked: "卡住了，需要人看一下", cancelled: "已取消" };
const TOOL_NAME = { claude_code: "Claude Code", codex: "Codex" };

function money(x) { return x ? `$${x.toFixed(x < 1 ? 3 : 2)}` : "$0"; }
function tokens(n) { return n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${Math.round(n / 1e3)}k` : String(n || 0); }

async function loadAgents() {
  let d;
  try { d = await api("/api/admin/agents"); } catch (err) { $("#agents-body").innerHTML = `<p class="error">${esc(err.message)}</p>`; return; }
  const note = $("#agents-notice");
  note.innerHTML = d.projects.length || !d.dispatch ? "" : '还没有项目。请在 <a href="#projects">项目</a> 页添加一个，这里就能看到它的 agent。';
  note.hidden = !!d.projects.length || !d.dispatch;   // the projects' panels belong to the old dispatch
  renderAgents(d);
  schedulePoll("agents", true, 10000);
}

function renderAgents(d) {
  const box = $("#agents-body"); box.innerHTML = "";
  // sessions opened by hand on this PC
  const s = document.createElement("div"); s.className = "card";
  const sessions = d.sessions || [];
  s.innerHTML = `<div class="card-head"><h3>你在这台电脑上自己开的会话</h3><span class="badge">${sessions.filter((x) => x.active).length} 个正在用</span></div>
    ${sessions.length ? `<div class="sessions">${sessions.map((x) => `
      <div class="session"><span class="dot ${x.active ? "on" : ""}"></span>
        <span class="s-main">${esc(TOOL_NAME[x.tool] || x.tool)} · ${esc((x.project || "").split(/[\\/]/).pop() || "?")}${x.model ? ` · ${esc(x.model)}` : ""}</span>
        <span class="s-meta">${x.active ? "正在用" : ago(x.last_at)} · 今天 ${tokens(x.tokens_today)} token</span></div>`).join("")}</div>`
      : '<p class="small-note">今天还没有。只读时间、文件夹和用量，不看对话内容。</p>'}`;
  box.appendChild(s);
  for (const p of d.projects) {
    const sec = document.createElement("section"); sec.className = "agent-project";
    if (p.error) { sec.innerHTML = `<div class="list-head"><h2>${esc(p.name)}</h2></div><p class="error">${esc(p.error)}</p>`; box.appendChild(sec); continue; }
    const pn = p.panel, counts = Object.entries(pn.tasks || {}).map(([k, v]) => `${TASK_STATUS[k] || k} ${v}`).join(" · ");
    sec.innerHTML = `<div class="list-head"><h2>${esc(p.name)}</h2><span class="job-meta">${esc(counts)}</span></div><div class="agent-grid"></div>`;
    const grid = sec.querySelector(".agent-grid");
    for (const a of pn.agents) {
      const [label, cls] = AGENT_STATE[a.state] || [a.state, ""];
      const c = document.createElement("div"); c.className = "card agent";
      const rate = a.recent.rate === null ? (a.week.runs ? "只做过审查" : "还没跑过") : `${Math.round(a.recent.rate * 100)}%（最近 ${a.recent.runs} 次）`;
      const until = a.until && !a.until_forever ? `，到 ${new Date(a.until * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : "";
      // orch reports {five_hour: {utilization: 0..1, resets_at}, seven_day: {...}}
      const quota = a.quota ? Object.entries(a.quota).filter(([, w]) => w && typeof w.utilization === "number")
        .map(([k, w]) => `${k === "five_hour" ? "5 小时" : k === "seven_day" ? "7 天" : esc(k)} 已用 ${Math.round(w.utilization * 100)}%`).join(" · ") : "";
      c.innerHTML = `
        <div class="card-head"><h3>${esc(a.pool)}</h3><span class="badge ${cls}">${cls === "run" ? '<span class="spinner small"></span>' : ""}${label}${until}</span></div>
        <p class="job-meta">${esc(a.executor)}${a.model ? ` · ${esc(a.model)}` : ""} · ${a.billing === "api" ? "按量付费" : "订阅"}</p>
        ${a.current.length ? `<p class="agent-now">正在做：${a.current.map((t) => `${esc(t.task)} ${esc(t.title)}`).join("；")}</p>` : ""}
        <dl class="agent-stats"><dt>今天</dt><dd>${a.today.runs} 次 · ${money(a.today.cost_usd)}</dd>
          <dt>7 天</dt><dd>${a.week.runs} 次 · ${money(a.week.cost_usd)}</dd>
          <dt>成功率</dt><dd>${rate}</dd>${quota ? `<dt>额度</dt><dd>${quota}</dd>` : ""}</dl>
        ${a.last_error ? `<p class="job-note">最近一次出错：${esc(a.last_error.task || "")} ${esc(a.last_error.text || a.last_error.status)}（${ago(a.last_error.at || pn.at)}）</p>` : ""}
        <div class="row-end">${a.state === "paused" || a.state === "cooling" ? '<button class="ghost" data-a="resume">恢复</button>' : '<button class="ghost" data-a="pause">暂停</button>'}</div>`;
      c.querySelector("[data-a]").onclick = (e) => agentAct({ project: p.name, action: e.target.dataset.a, pool: a.pool });
      grid.appendChild(c);
    }
    const pools = pn.agents.map((a) => a.pool);
    if (pn.attention.length || pn.queued.length) {
      const list = document.createElement("div"); list.className = "card";
      list.innerHTML = `<div class="card-head"><h3>需要看一下的任务</h3></div>`;
      for (const t of [...pn.attention, ...pn.queued]) {
        const row = document.createElement("div"); row.className = "task-row";
        const status = t.status ? (TASK_STATUS[t.status] || t.status) : `排队中，下一个：${t.pin || t.next || "?"}`;
        row.innerHTML = `<span class="t-main"><strong>${esc(t.task)}</strong> <span class="t-title">${esc(t.title)}</span><small>${esc(status)}${t.note ? " · " + esc(t.note) : ""}</small></span>
          <select class="pill" aria-label="换给">${pools.map((x) => `<option>${esc(x)}</option>`).join("")}</select>
          <button class="ghost">换给它</button>`;
        row.querySelector("button").onclick = () => agentAct({ project: p.name, action: "swap", task: t.task, pool: row.querySelector("select").value });
        list.appendChild(row);
      }
      sec.appendChild(list);
    }
    box.appendChild(sec);
  }
}

async function agentAct(body) {
  if (body.action === "pause" && !confirm(`暂停 ${body.pool}？正在做的不会被打断，只是不再派新任务，直到你恢复。`)) return;
  try { const d = await api("/api/admin/agents", { method: "POST", body }); toast(d.message); await loadAgents(); }
  catch (err) { toast(err.message); }
}

// start once the page's later scripts (projects.js, job.js, discuss.js) have run too: a quick /api/state answer must
// not reach showView() before MAOProjects / MAODiscuss exist
const startApp = () => boot().catch((err) => { document.body.textContent = err.message === OFFLINE ? OFFLINE : "打不开：" + err.message; });
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", startApp); else startApp();
