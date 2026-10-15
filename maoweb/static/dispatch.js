"use strict";

// One explicit handoff from a conversation; subsequent agent work is supervised by orch.
(() => {
  const trigger = $("#dispatch-open"), dialog = $("#dispatch-dialog"), form = $("#dispatch-form");
  const panel = $("#dispatch-jobs");
  const status = { queued: "排队中", planning: "AI 正在拆分任务", running: "Agent 正在执行", pausing: "正在暂停",
    paused: "已暂停", draft: "计划已生成，等待执行", done: "全部完成", needs_attention: "需要处理",
    interrupted: "执行已中断", failed: "未完成", verified: "已通过验收，等待合并", merged: "已合并", blocked: "遇到阻碍", cancelled: "已取消" };
  const categories = { frontend: "前端", backend: "后端", testing: "测试", documentation: "文档", research: "调研", code_change: "代码修改" };
  let busy = false, last = "", captured = null;

  trigger.onclick = async () => {
    if (!state.convId || state.streaming) return toast("请先完成当前讨论，再分派任务");
    try {
      const d = await api("/api/code");
      if (!d.projects.length) return toast("请先在设置的写程序页面登记一个项目");
      captured = state.convId;
      form.elements.project.innerHTML = d.projects.map(p => `<option value="${esc(p)}">${esc(p)}</option>`).join("");
      const conversation = await api(`/api/conversations/${captured}`);
      const userMessages = conversation.messages.filter(m => m.role === "user");
      form.elements.goal.value = userMessages.length ? userMessages[userMessages.length - 1].content : "";
      $("#dispatch-error").textContent = "";
      dialog.showModal(); form.elements.goal.focus();
    } catch (err) { toast(err.message); }
  };
  $("#dispatch-cancel").onclick = () => dialog.close();
  form.onsubmit = async e => {
    e.preventDefault();
    const buttons = [...form.querySelectorAll('button[type="submit"]')];
    buttons.forEach(b => b.disabled = true);
    try {
      await api("/api/dispatch", { method: "POST", body: {
        conversation_id: captured, project: form.elements.project.value, goal: form.elements.goal.value,
        jobs: Number(form.elements.jobs.value), mode: e.submitter?.value || "start"
      }});
      dialog.close(); toast("已交给 AI 分派，进度会显示在当前对话里"); last = ""; await refresh();
    } catch (err) { $("#dispatch-error").textContent = err.message; }
    finally { buttons.forEach(b => b.disabled = false); }
  };

  panel.onclick = async e => {
    const button = e.target.closest("button[data-action]");
    if (!button) return;
    button.disabled = true;
    try {
      await api("/api/dispatch/action", { method: "POST", body: {
        job_id: Number(button.dataset.job), action: button.dataset.action, task_id: button.dataset.task || ""
      }});
      last = ""; await refresh();
    } catch (err) { toast(err.message); }
    finally { button.disabled = false; }
  };

  function render(jobs) {
    const opened = new Set([...panel.querySelectorAll("details[open]")].map(d => d.dataset.key));
    panel.innerHTML = jobs.length ? '<p class="small-note">任务分派与监督</p>' : "";
    for (const job of jobs) {
      const active = ["queued", "planning", "running", "pausing"].includes(job.status);
      const tasks = job.tasks || [], planned = job.plan.tasks || [];
      const finished = tasks.filter(t => t.status === "merged").length;
      const card = document.createElement("div"); card.className = "job dispatch-card";
      const action = active ? `<button class="ghost" data-action="pause" data-job="${job.id}" ${job.status === "pausing" ? "disabled" : ""}>暂停</button>`
        : job.status !== "done" ? `<button class="primary" data-action="resume" data-job="${job.id}">${job.status === "draft" ? "执行全部任务" : "继续或重试"}</button>` : "";
      const rows = tasks.length ? tasks.map(task => {
        const runs = task.runs || [], pool = runs.length ? runs[runs.length - 1].pool : "";
        const checked = runs.some(r => r.verified === 1);
        const deps = task.depends_on ? JSON.parse(task.depends_on) : [];
        return `<div class="dispatch-task"><strong>${esc(task.title || task.id)}</strong>
          <span>${esc(categories[task.type] || task.type || "")} · ${esc(status[task.status] || task.status || "排队中")}${pool ? ` · ${esc(pool)}` : ""}${checked ? " · 验收已通过" : ""}</span>
          ${deps.length ? `<span>先完成：${esc(deps.join("、"))}</span>` : ""}
          ${task.note ? `<span>${esc(task.note)}</span>` : ""}
          ${task.status === "verified" && !active ? `<button class="ghost" data-action="merge" data-job="${job.id}" data-task="${esc(task.id)}">确认合并并继续</button>` : ""}</div>`;
      }).join("") : planned.map(task => `<div class="dispatch-task"><strong>${esc(task.title)}</strong>
        <span>${esc(categories[task.type] || task.type)} · ${esc(task.difficulty)}${task.depends_on.length ? ` · 先完成：${esc(task.depends_on.join("、"))}` : ""}</span>
        <span>候选执行者：${esc((job.routes[task.key] || []).join(" → "))}</span></div>`).join("");
      card.innerHTML = `<div class="job-head"><span class="badge ${active ? "run" : ""}">${esc(status[job.status] || job.status)}</span><h3>${esc(job.project)}</h3></div>
        <p class="job-meta">${esc(job.goal)} · 最多 ${job.jobs} 个任务同时执行${tasks.length ? ` · 已完成 ${finished}/${tasks.length}` : ""}</p>
        ${job.plan.summary ? `<p>${esc(job.plan.summary)}</p>` : ""}
        ${job.error ? `<p class="error">${esc(job.error)}</p>` : ""}${rows}
        ${(job.plan.warnings || []).map(w => `<p class="small-note">${esc(w)}</p>`).join("")}
        <div class="row-end">${action}</div>
        ${job.log ? `<details data-key="${job.id}"${opened.has(String(job.id)) ? " open" : ""}><summary>查看分派和检查过程</summary><pre>${esc(job.log)}</pre></details>` : ""}`;
      panel.appendChild(card);
    }
  }

  async function refresh() {
    trigger.hidden = !state.user?.can_code;
    trigger.disabled = !state.convId || Boolean(state.streaming);
    if (!state.user?.can_code || !state.convId) { panel.innerHTML = ""; last = ""; return; }
    if (busy || document.hidden) return;
    busy = true;
    const cid = state.convId;
    try {
      const data = await api(`/api/dispatch?conversation_id=${cid}`);
      if (cid !== state.convId) return;
      const signature = JSON.stringify([cid, data.jobs]);
      if (signature !== last) { render(data.jobs); last = signature; }
    } catch (err) { if (cid === state.convId) panel.textContent = err.message; }
    finally { busy = false; }
  }
  window.MAODispatch = { refresh };
  setInterval(refresh, 3000);
  document.addEventListener("visibilitychange", refresh);
  refresh();
})();
