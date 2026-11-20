/* @module static/js/schedules.js
 * @purpose View 6: Schedules, Catch-Up Cadence Matrix, and optional Mother supervisor deferral controls.
 * @inputs state.schedules and /api/schedules & /api/v1/scheduler/defer endpoints.
 * @outputs Rendered Schedules workspace and schedule persistence handlers. */

import { state, esc, showToast, tableLabelStyle } from "./state.js";
import { apiFetch } from "./api.js";
import { runGreenroomTask } from "./tasks.js";
import { refreshTooltips } from "./tooltips.js";

export function renderSchedules(root) {
  if (!root) return;
  const sData = state.schedules.data || {};
  const schedStatus = state.schedules.status || {};
  const jobs = sData.jobs || {};
  const deferred = Boolean(schedStatus.deferred_to_mother ?? sData.deferred_to_mother ?? schedStatus.deferredUntilUtc);
  const headers = ["Job Key", "Enabled", "Interval (Minutes)", "Catch-Up If Missed", "Command", "Description"];

  const jobRows = Object.entries(jobs)
    .map(
      ([jobKey, cfg]) => `
      <tr data-sched-job="${esc(jobKey)}">
        <th scope="row" class="mono">${esc(jobKey)}</th>
        <td><input type="checkbox" name="enabled" ${cfg.enabled !== false ? "checked" : ""}></td>
        <td><input type="number" name="interval_minutes" class="field-input num" style="max-width:110px;" min="1" value="${esc(cfg.interval_minutes ?? 60)}"></td>
        <td><input type="checkbox" name="run_if_missed" ${cfg.run_if_missed !== false ? "checked" : ""}></td>
        <td class="mono">${esc(cfg.command || "")}</td>
        <td><input type="text" name="description" class="field-input" value="${esc(cfg.description || "")}"></td>
      </tr>`
    )
    .join("");

  root.innerHTML = `
    <section class="card">
      <div class="card-head">
        <div>
          <h2 class="card-title">Scheduler &amp; Optional Suite Supervision</h2>
          <p class="card-subtitle">When running alongside an optional Mother suite supervisor, standalone schedule ticks can yield to central coordination.</p>
        </div>
        <div class="toolbar">
          <span class="badge badge-status ${deferred ? "badge-yellow" : "badge-green"}">${deferred ? "DEFERRED TO SUITE" : "STANDALONE ACTIVE"}</span>
          <button type="button" class="ghost-btn" id="toggleMotherDeferBtn">${deferred ? "Resume Standalone Ticks" : "Defer Ticks to Suite"}</button>
          <button type="button" class="primary-btn" id="runDueSchedulesBtn">▶ Run Due Tick Now</button>
        </div>
      </div>
      <div class="muted-hint mono">Active Schedule File: ${esc(sData.path || "config/schedule.yaml")} · Timezone: ${esc(sData.timezone || "Local")}</div>
    </section>

    <section class="card contain-panel">
      <div class="card-head">
        <div>
          <h2 class="card-title">Catch-Up Schedule Matrix (${Object.keys(jobs).length} Jobs)</h2>
          <p class="card-subtitle">Jobs marked <strong>Catch-Up If Missed</strong> automatically run on startup or wake if their scheduled window passed while offline.</p>
        </div>
        <button type="button" class="primary-btn" id="saveSchedulesBtn">💾 Save Schedule Config</button>
      </div>
      <div tabindex="0" role="region" aria-label="Schedule Cadence Matrix" class="table-wrapper">
        <table class="data-table" style="${esc(tableLabelStyle(headers))}">
          <thead><tr>${headers.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead>
          <tbody>${jobRows || `<tr><td colspan="6" class="muted-hint">No scheduled jobs configured.</td></tr>`}</tbody>
        </table>
      </div>
    </section>
  `;

  refreshTooltips(root);

  root.querySelector("#toggleMotherDeferBtn")?.addEventListener("click", async () => {
    const res = await apiFetch("/api/scheduler/defer", {
      method: "POST",
      body: { deferred: !deferred, minutes: !deferred ? 60 : 0, reason: !deferred ? "companion_ui_manual" : "standalone", callerAppId: "greenroom" },
    });
    state.schedules.status = res;
    showToast(res.deferred_to_mother || res.deferredUntilUtc ? "Scheduler deferred to suite supervisor" : "Standalone scheduler resumed");
    renderSchedules(root);
  });

  root.querySelector("#runDueSchedulesBtn")?.addEventListener("click", () => {
    runGreenroomTask("tick");
  });

  root.querySelector("#saveSchedulesBtn")?.addEventListener("click", async () => {
    const nextJobs = {};
    root.querySelectorAll("tr[data-sched-job]").forEach((tr) => {
      const key = tr.dataset.schedJob;
      const orig = jobs[key] || {};
      nextJobs[key] = {
        ...orig,
        enabled: tr.querySelector('input[name="enabled"]').checked,
        interval_minutes: Math.max(1, Number(tr.querySelector('input[name="interval_minutes"]').value || 60)),
        run_if_missed: tr.querySelector('input[name="run_if_missed"]').checked,
        description: tr.querySelector('input[name="description"]').value.trim(),
      };
    });
    const saved = await apiFetch("/api/schedules", {
      method: "PUT",
      body: { timezone: sData.timezone || "UTC", jobs: nextJobs },
    });
    state.schedules.data = saved;
    showToast("Saved schedule configuration and created snapshot");
    renderSchedules(root);
  });
}
