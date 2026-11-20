/* @module static/js/tasks.js
 * @purpose Task Runner view, SSE live stream client, FIFO queue controls, dry-run toggle, and stop-all handler.
 * @inputs /api/tasks/* and /api/v1/jobs/stop-all REST endpoints plus /api/tasks/stream SSE events.
 * @outputs Live task banner, synchronized dry-run/queue controls, footer status updates, and Task Runner DOM. */

import { state, TOKEN_KEY, esc, fmtNum, isDomainEnabled, showToast, tableLabelStyle, refreshCurrentView } from "./state.js";
import { apiFetch, apiUrl, savePreferences } from "./api.js";
import { renderDrawerLogs, updateFooterStatusBar } from "./shell.js";
import { refreshTooltips } from "./tooltips.js";

export function syncQueueControlsUI() {
  const qBadge = document.getElementById("queueDepthBadge");
  const depth = Array.isArray(state.tasks.queue) ? state.tasks.queue.length : Number(state.tasks.queueDepth || 0);
  if (qBadge) {
    if (depth > 0 || state.tasks.paused || state.tasks.suspended) {
      qBadge.hidden = false;
      qBadge.textContent = state.tasks.suspended ? `SUSPENDED (${depth})` : state.tasks.paused ? `PAUSED (${depth})` : `QUEUE: ${depth}`;
    } else {
      qBadge.hidden = true;
    }
  }
  const pauseBtn = document.getElementById("topPauseQueueBtn");
  if (pauseBtn) {
    pauseBtn.classList.toggle("active", Boolean(state.tasks.paused));
    pauseBtn.textContent = state.tasks.paused ? "▶ Resume" : "⏸ Pause";
  }
  const suspBtn = document.getElementById("topSuspendQueueBtn");
  if (suspBtn) {
    suspBtn.classList.toggle("active", Boolean(state.tasks.suspended));
    suspBtn.textContent = state.tasks.suspended ? "🔥 Unsuspend" : "❄ Suspend";
  }
  const isDry = Boolean(state.tasks.dryRun);
  state.tasks.dry_run = isDry;
  const dryBtn = document.getElementById("topDryRunBtn");
  if (dryBtn) {
    dryBtn.classList.toggle("active", isDry);
    dryBtn.textContent = isDry ? "🧪 Dry Run: ON" : "🧪 Dry Run";
    dryBtn.setAttribute("aria-pressed", String(isDry));
  }
  const taskDryToggle = document.getElementById("taskDryRunToggle");
  if (taskDryToggle) {
    taskDryToggle.classList.toggle("active", isDry);
    taskDryToggle.textContent = isDry ? "🧪 Dry Run: ON" : "🧪 Dry Run: OFF";
    taskDryToggle.setAttribute("aria-pressed", String(isDry));
  }
  updateFooterStatusBar({ connected: true, taskLabel: state.tasks.activeTask?.label || state.tasks.activeTask?.task_id || "" });
}

export async function toggleDryRunMode() {
  const nextDry = !state.tasks.dryRun;
  try {
    const res = await apiFetch("/api/tasks/dry-run", { method: "POST", body: { dry_run: nextDry, dryRun: nextDry } });
    const resolved = Boolean(res.dryRun ?? res.dry_run ?? nextDry);
    state.tasks.dryRun = resolved;
    state.tasks.dry_run = resolved;
  } catch (err) {
    console.debug("Dry-run endpoint fallback:", err);
    state.tasks.dryRun = nextDry;
    state.tasks.dry_run = nextDry;
  }
  syncQueueControlsUI();
  showToast(state.tasks.dryRun ? "Dry-Run mode enabled" : "Dry-Run mode disabled");
  if (state.view === "tasks") {
    const root = document.getElementById("workspaceRoot");
    if (root) renderTasks(root);
  }
  return state.tasks.dryRun;
}

export async function stopAllRunningJobs(rootEl = document.getElementById("workspaceRoot")) {
  try {
    await apiFetch("/api/tasks/stop-all", { method: "POST" });
  } catch (outerErr) {
    console.debug("Primary stop-all fallback:", outerErr);
    try { await apiFetch("/api/v1/jobs/stop-all", { method: "POST" }); } catch (err) { console.debug("Stop-all fallback:", err); }
  }
  state.tasks.queue = [];
  state.tasks.queueDepth = 0;
  setActiveTaskBanner(null);
  syncQueueControlsUI();
  showToast("Cleared task queue and signaled stop");
  const targetRoot = rootEl || document.getElementById("workspaceRoot");
  if (state.view === "tasks" && targetRoot) renderTasks(targetRoot);
}

export function setActiveTaskBanner(active) {
  const validTask = active && (active.task_id || active.label) ? active : null;
  state.tasks.activeTask = validTask;
  const banner = document.getElementById("activeTaskBanner");
  const txt = document.getElementById("activeTaskText");
  if (banner && txt) {
    if (validTask) {
      txt.textContent = `${validTask.label || validTask.task_id}...`;
      banner.hidden = false;
    } else {
      banner.hidden = true;
      txt.textContent = "";
    }
  }
  updateFooterStatusBar({ connected: true, taskLabel: validTask ? (validTask.label || validTask.task_id) : "" });
}

export function applyTaskStateSnapshot(payload) {
  if (!payload) return;
  if (Array.isArray(payload.catalog) && payload.catalog.length) state.tasks.catalog = payload.catalog;
  if (Array.isArray(payload.recent_logs)) state.tasks.logs = payload.recent_logs;
  if (Array.isArray(payload.queue)) { state.tasks.queue = payload.queue; state.tasks.queueDepth = payload.queue.length; }
  else if (typeof payload.queue_depth === "number") state.tasks.queueDepth = payload.queue_depth;
  if (typeof payload.paused === "boolean") state.tasks.paused = payload.paused;
  if (typeof payload.suspended === "boolean") state.tasks.suspended = payload.suspended;
  if (typeof payload.dryRun === "boolean") { state.tasks.dryRun = payload.dryRun; state.tasks.dry_run = payload.dryRun; }
  else if (typeof payload.dry_run === "boolean") { state.tasks.dryRun = payload.dry_run; state.tasks.dry_run = payload.dry_run; }
  if ("active_task" in payload) setActiveTaskBanner(payload.active_task || null);
  syncQueueControlsUI();
  if (state.logDrawerOpen) renderDrawerLogs();
}

export function renderLogConsoleOnly() {
  const box = document.getElementById("liveLogConsole");
  if (!box) return;
  const lines = (state.tasks.logs || []).slice(-80);
  box.innerHTML = lines.length
    ? lines.map((l) => `<div><span style="color:#8b98ab">[${esc((l.timestamp || "").slice(11, 19))}]</span> ${esc(l.message || l.event)}</div>`).join("")
    : `<div style="color:#8b98ab">Ready. Trigger any Greenroom task below to watch live telemetry.</div>`;
  box.scrollTop = box.scrollHeight;
}

const MAX_CLIENT_TASK_LOGS = 300;
function pushTaskLog(entry) {
  state.tasks.logs.push(entry);
  if (state.tasks.logs.length > MAX_CLIENT_TASK_LOGS) state.tasks.logs = state.tasks.logs.slice(-MAX_CLIENT_TASK_LOGS);
}

export function connectTaskStream() {
  if (state.sse) state.sse.close();
  let streamUrl = "/api/tasks/stream";
  try { const tok = localStorage.getItem(TOKEN_KEY); if (tok) streamUrl = `/api/tasks/stream?token=${encodeURIComponent(tok)}`; } catch (err) { console.debug("SSE token skipped:", err); }
  const es = new EventSource(apiUrl(streamUrl));
  state.sse = es;
  es.addEventListener("init", (ev) => applyTaskStateSnapshot(JSON.parse(ev.data || "{}")));
  es.addEventListener("queue_updated", (ev) => {
    const payload = JSON.parse(ev.data || "{}");
    if (Array.isArray(payload.queue)) { state.tasks.queue = payload.queue; state.tasks.queueDepth = payload.queue.length; }
    if (payload.message) pushTaskLog({ timestamp: payload.timestamp, level: "info", message: payload.message });
    syncQueueControlsUI();
    if (state.view === "tasks") refreshCurrentView({ background: true });
    if (state.logDrawerOpen) renderDrawerLogs();
  });
  es.addEventListener("started", (ev) => {
    const payload = JSON.parse(ev.data || "{}");
    pushTaskLog(payload);
    setActiveTaskBanner({ task_id: payload.task_id, label: payload.label });
    if (state.view === "tasks") renderLogConsoleOnly();
    if (state.logDrawerOpen) renderDrawerLogs();
  });
  es.addEventListener("log", (ev) => {
    const payload = JSON.parse(ev.data || "{}");
    pushTaskLog(payload);
    const txt = document.getElementById("activeTaskText");
    if (state.tasks.activeTask && txt && payload.message) txt.textContent = payload.message;
    if (state.view === "tasks") renderLogConsoleOnly();
    if (state.logDrawerOpen) renderDrawerLogs();
  });
  es.addEventListener("error", (ev) => {
    if (!ev.data) { updateFooterStatusBar({ connected: false }); return; }
    try { pushTaskLog(JSON.parse(ev.data)); if (state.view === "tasks") renderLogConsoleOnly(); if (state.logDrawerOpen) renderDrawerLogs(); } catch (err) { console.debug("SSE error parse skipped:", err); }
  });
  es.addEventListener("finished", (ev) => {
    const payload = JSON.parse(ev.data || "{}");
    pushTaskLog(payload);
    setActiveTaskBanner(null);
    showToast(payload.message || "Task finished");
    if (state.logDrawerOpen) renderDrawerLogs();
    refreshCurrentView({ background: true }).catch((err) => console.debug("Post-task refresh skipped:", err));
  });
  es.addEventListener("sync", (ev) => {
    const payload = JSON.parse(ev.data || "{}");
    if (Array.isArray(payload.recent_logs) && payload.recent_logs.length) state.tasks.logs = payload.recent_logs.slice(-MAX_CLIENT_TASK_LOGS);
    setActiveTaskBanner(payload.active_task || null);
    const txt = document.getElementById("activeTaskText");
    if (payload.active_task && txt && Array.isArray(payload.recent_logs) && payload.recent_logs.length) {
      const latest = payload.recent_logs[payload.recent_logs.length - 1];
      if (latest?.message) txt.textContent = `${payload.active_task.label}: ${latest.message}`;
    }
    if (state.view === "tasks") renderLogConsoleOnly();
    if (state.logDrawerOpen) renderDrawerLogs();
    if (payload.external_finished) { showToast("External scheduler job completed"); refreshCurrentView({ background: true }).catch(() => {}); }
  });
}

export async function runGreenroomTask(taskId, queueIfBusy = false) {
  try {
    const isDry = Boolean(state.tasks.dryRun);
    const res = await apiFetch("/api/tasks/run", { method: "POST", body: { task_id: taskId, queue_if_busy: queueIfBusy, dry_run: isDry, dryRun: isDry } });
    if (res.status === "domain_disabled") {
      showToast(res.message || `Module '${res.domain || ""}' is disabled`);
      return;
    }
    if (res.started) showToast(`Started: ${res.label || taskId}${res.dry_run || res.dryRun || isDry ? " [Dry Run]" : ""}`);
    else if (res.queued) { showToast(`Queued #${res.queue_position}: ${res.queue_item?.label || taskId}`); state.tasks.queue = res.queue || state.tasks.queue; syncQueueControlsUI(); if (state.view === "tasks") refreshCurrentView({ background: true }); }
  } catch (err) { showToast(err.message); }
}

export const runTask = runGreenroomTask;

export async function enqueueGreenroomTask(taskId) {
  try {
    const isDry = Boolean(state.tasks.dryRun);
    const res = await apiFetch("/api/tasks/queue", { method: "POST", body: { task_id: taskId, dry_run: isDry, dryRun: isDry } });
    if (res.status === "domain_disabled") { showToast(res.message || "Module is disabled"); return; }
    if (res.started) showToast(`Started immediately: ${res.label || taskId}`);
    else if (res.queued) showToast(`Queued #${res.queue_position}: ${res.queue_item?.label || taskId}`);
    if (Array.isArray(res.queue)) state.tasks.queue = res.queue;
    syncQueueControlsUI();
    if (state.view === "tasks") refreshCurrentView({ background: true });
  } catch (err) { showToast(err.message); }
}

export function renderTasks(root) {
  if (!root) return;
  const pinned = new Set(state.prefs.global.pinned_tasks || []);
  const groups = {};
  (state.tasks.catalog || []).forEach((t) => { (groups[t.category] = groups[t.category] || []).push(t); });

  const queueItems = state.tasks.queue || [];
  const queueRowsHtml = queueItems.length
    ? queueItems.map((q, idx) => `<div class="list-row"><div class="list-row-main"><div class="list-row-title">#${idx + 1} · ${esc(q.label || q.task_id)} <span class="mono" style="color:var(--text-muted)">(${esc(q.task_id)})</span></div><div class="list-row-sub mono">Queued at ${esc((q.enqueued_at || "").slice(11, 19))}${q.dryRun || q.dry_run ? " · DRY-RUN" : ""}</div></div><button type="button" class="danger-btn" data-remove-queue="${esc(q.queue_id)}">Remove</button></div>`).join("")
    : `<p class="muted-hint">Queue is empty. Click <strong>Queue</strong> on any task below or <strong>Queue Next</strong> in the top bar.</p>`;

  const groupCards = Object.entries(groups).map(([cat, items]) => `
    <section class="card contain-panel">
      <h2 class="card-title">${esc(cat)}</h2>
      <div class="stack-list">
        ${items.map((t) => {
          const domOff = t.domain && !isDomainEnabled(t.domain);
          return `<div class="list-row"><div class="list-row-main"><div class="list-row-title">${esc(t.label)} <span class="mono" style="color:var(--text-muted)">(${esc(t.id)})</span> ${domOff ? `<span class="badge badge-status badge-neutral">MODULE OFF</span>` : ""}</div><div class="list-row-sub">${esc(t.description)}</div></div><div class="toolbar"><button type="button" class="chip-btn ${pinned.has(t.id) ? "active" : ""}" data-pin-task="${esc(t.id)}" title="Pin Task To Dashboard|Pin or unpin this task on the Command Center bar">★</button><button type="button" class="ghost-btn" data-queue-task="${esc(t.id)}" title="Add Task To Queue|Append this task to the execution queue">⏭ Queue</button><button type="button" class="primary-btn" data-exec-task="${esc(t.id)}">Run Task</button></div></div>`;
        }).join("")}
      </div>
    </section>`).join("");

  const runHeaders = ["ID", "Job Name", "Status", "Rows", "Started At", "Error"];
  const runRows = (state.tasks.runs || []).map((r) => `<tr><th scope="row" class="num">#${r.id}</th><td class="mono">${esc(r.job_name)}</td><td><span class="badge badge-status ${r.status === "success" ? "badge-green" : r.status === "failed" ? "badge-red" : "badge-yellow"}">${esc(r.status)}</span></td><td class="num">${fmtNum(r.rows_affected)}</td><td class="mono">${esc((r.started_at || "").slice(0, 19).replace("T", " "))}</td><td>${esc(r.error_message || "—")}</td></tr>`).join("");

  root.innerHTML = `
    <section class="card">
      <div class="card-head">
        <div>
          <h2 class="card-title">Task Execution Queue (${queueItems.length})</h2>
          <p class="card-subtitle">Sequential background queue with Pause, Suspend, Stop All, and Dry Run controls</p>
        </div>
        <div class="toolbar">
          <span class="badge badge-status ${state.tasks.paused ? "badge-yellow" : "badge-green"}">${state.tasks.paused ? "PAUSED" : "ACTIVE"}</span>
          ${state.tasks.suspended ? `<span class="badge badge-status badge-red">SUSPENDED</span>` : ""}
          <button type="button" class="chip-btn ${state.tasks.dryRun ? "active" : ""}" id="taskDryRunToggle" aria-pressed="${String(Boolean(state.tasks.dryRun))}" title="Toggle Dry Run Mode|Preview collector and media actions without mutating files or rows">🧪 Dry Run: ${state.tasks.dryRun ? "ON" : "OFF"}</button>
          ${state.tasks.dryRun ? `<span class="badge badge-status badge-blue">DRY-RUN MODE</span>` : ""}
        </div>
      </div>
      <div class="stack-list">${queueRowsHtml}</div>
    </section>
    <section class="card">
      <div class="card-head">
        <h2 class="card-title">Live Execution Stream</h2>
        <span class="muted-hint">Protected by job lock to prevent collisions with scheduled background runs.</span>
      </div>
      <div class="log-console" id="liveLogConsole"></div>
    </section>
    <div class="dash-grid">${groupCards}</div>
    <section class="card contain-panel">
      <h2 class="card-title">Recent System Runs Audit Trail</h2>
      <div tabindex="0" role="region" aria-label="System Runs History" class="table-wrapper">
        <table class="data-table" style="${esc(tableLabelStyle(runHeaders))}">
          <thead><tr>${runHeaders.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead>
          <tbody>${runRows || `<tr><td colspan="6" class="muted-hint">No system runs recorded yet.</td></tr>`}</tbody>
        </table>
      </div>
    </section>`;

  renderLogConsoleOnly();
  refreshTooltips(root);
  root.querySelector("#taskDryRunToggle")?.addEventListener("click", () => toggleDryRunMode());
  root.querySelectorAll("[data-exec-task]").forEach((btn) => btn.addEventListener("click", () => runGreenroomTask(btn.dataset.execTask)));
  root.querySelectorAll("[data-queue-task]").forEach((btn) => btn.addEventListener("click", () => enqueueGreenroomTask(btn.dataset.queueTask)));
  root.querySelectorAll("[data-remove-queue]").forEach((btn) => btn.addEventListener("click", async () => { await apiFetch(`/api/tasks/queue/${encodeURIComponent(btn.dataset.removeQueue)}`, { method: "DELETE" }); showToast("Removed task from queue"); refreshCurrentView({ background: true }); }));
  root.querySelectorAll("[data-pin-task]").forEach((btn) => btn.addEventListener("click", async () => {
    const tid = btn.dataset.pinTask, cur = new Set(state.prefs.global.pinned_tasks || []);
    if (cur.has(tid)) cur.delete(tid); else cur.add(tid);
    await savePreferences({ pinned_tasks: [...cur] }, "global");
    renderTasks(root);
  }));
}
