/* @module static/js/production.js
 * @purpose View 4: Creator Studio, Weighted Series Pools & Draft Desk workspace.
 * @inputs state.production and /api/production/* REST endpoints.
 * @outputs Rendered Cuts, Series Pools, Review Drafts, and Handoff Bus views with domain-disabled guard. */

import { state, esc, showToast, tableLabelStyle, switchView, refreshCurrentView } from "./state.js";
import { apiFetch } from "./api.js";
import { openCutEditor, openDraftEditor } from "./modals.js";
import { refreshTooltips } from "./tooltips.js";

export function renderProduction(root) {
  const p = state.production.data;
  if (!p || !root) return;
  if (p.status === "domain_disabled") {
    root.innerHTML = `<section class="card"><div class="card-head"><h2 class="card-title">Creator Studio Module Disabled</h2><span class="badge badge-status badge-neutral">DISABLED</span></div><p class="muted-hint">${esc(p.message || "Enable Creator Studio & Agent Drops in Settings to manage production cuts, weighted series pools, and drafts. Existing database rows are preserved.")}</p><div class="toolbar"><button type="button" class="primary-btn" id="studioEnableModBtn">Open Modules &amp; Profiles</button></div></section>`;
    root.querySelector("#studioEnableModBtn")?.addEventListener("click", () => { state.settings.subtab = "modules"; switchView("settings"); });
    return;
  }

  const sub = state.production.subtab;
  const tabs = [
    ["cuts", `Workstation Cuts (${(p.cuts || []).length})`],
    ["pools", "Series Pools & Arcs"],
    ["drafts", `Review Drafts (${(p.drafts || []).length})`],
    ["handoffs", `Handoff Queue (${(p.handoffs || []).length})`],
  ].map(([id, label]) => `<button type="button" class="chip-btn ${sub === id ? "active" : ""}" data-prod-tab="${id}">${esc(label)}</button>`).join("");

  let bodyHtml = "";
  if (sub === "cuts") {
    const headers = ["Project Title", "Channel", "Series", "Stage", "Status", "Actions"];
    const rows = (p.cuts || []).map((c, idx) => `
      <tr data-cut-idx="${idx}">
        <th scope="row">${esc(c.project_title)}</th>
        <td>${esc(c.channel_id)}</td>
        <td>${esc(c.series || "—")}</td>
        <td>${esc(c.current_stage)}</td>
        <td><span class="badge badge-status ${c.status === "active" ? "badge-green" : "badge-blue"}">${esc(c.status)}</span></td>
        <td class="no-row-open"><button type="button" class="ghost-btn" data-ready-cut="${idx}">Mark Ready</button></td>
      </tr>`).join("");
    bodyHtml = `
      <div class="toolbar" style="justify-content:space-between;">
        <button type="button" class="primary-btn" id="newCutBtn">+ New Production Cut</button>
        <button type="button" class="ghost-btn" id="recalcBuffersBtn">↻ Recalculate Channel Buffers</button>
      </div>
      <div tabindex="0" role="region" aria-label="Production Cuts" class="table-wrapper contain-panel">
        <table class="data-table" style="${esc(tableLabelStyle(headers))}">
          <thead><tr>${headers.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead>
          <tbody>${rows || `<tr><td colspan="6" class="muted-hint">No production cuts recorded yet.</td></tr>`}</tbody>
        </table>
      </div>`;
  } else if (sub === "pools") {
    const poolHeaders = ["Game", "Platform", "Pool", "Weight", "Last Picked", "Action"];
    const poolRows = (p.pools || []).map((pl) => `
      <tr>
        <th scope="row">${esc(pl.title)}</th>
        <td>${esc(pl.platform || "—")}</td>
        <td>${esc(pl.pool_name)}</td>
        <td class="num"><strong>${Number(pl.dynamic_weight).toFixed(1)}</strong></td>
        <td>${esc(pl.last_played_at ? pl.last_played_at.slice(0, 10) : "Unplayed")}</td>
        <td><button type="button" class="ghost-btn" data-pick-pool="${esc(pl.pool_name)}" data-pick-game="${esc(pl.game_id)}">Record Pick</button></td>
      </tr>`).join("");
    const arcRows = (p.series_arcs || []).slice(0, 20).map((sa) => `<div class="list-row"><div class="list-row-main"><div class="list-row-title">${esc(sa.series_name)}</div><div class="list-row-sub">${esc(sa.channel)}</div></div><span class="badge badge-blue num">Part ${sa.current_part}</span></div>`).join("") || `<p class="muted-hint">No series progress records yet.</p>`;
    bodyHtml = `
      <div class="dash-grid">
        <section class="card">
          <h2 class="card-title">Dynamic Weighted Series Pools</h2>
          <div tabindex="0" role="region" aria-label="Series Pools" class="table-wrapper">
            <table class="data-table" style="${esc(tableLabelStyle(poolHeaders))}">
              <thead><tr>${poolHeaders.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead>
              <tbody>${poolRows || `<tr><td colspan="6" class="muted-hint">No series pool entries configured yet.</td></tr>`}</tbody>
            </table>
          </div>
        </section>
        <section class="card">
          <h2 class="card-title">Series Episode Progress</h2>
          <div class="stack-list">${arcRows}</div>
        </section>
      </div>`;
  } else if (sub === "drafts") {
    const draftCards = (p.drafts || []).map((dr, idx) => `
      <div class="card">
        <div class="card-head">
          <div><span class="badge badge-status badge-blue">${esc(dr.agent_name)}</span> <strong style="margin-inline-start:8px;">${esc(dr.title)}</strong></div>
          <div class="toolbar">
            <span class="badge badge-status ${dr.status === "approved" || dr.status === "published" ? "badge-green" : "badge-yellow"}">${esc(dr.status)}</span>
            <button type="button" class="ghost-btn" data-open-draft="${idx}">Read / Edit</button>
            <button type="button" class="primary-btn" data-approve-draft="${dr.id}">Approve Draft</button>
          </div>
        </div>
        <pre class="mono" style="white-space:pre-wrap;margin:0;max-height:140px;overflow:hidden;color:var(--text-muted);">${esc((dr.content_md || "").slice(0, 420))}</pre>
      </div>`).join("");
    bodyHtml = `<div class="stack-list">${draftCards || `<p class="muted-hint">No review drafts staged.</p>`}</div>`;
  } else if (sub === "handoffs") {
    const handoffRows = (p.handoffs || []).map((h) => `
      <div class="list-row">
        <div class="list-row-main">
          <div class="list-row-title">${esc(h.from_agent)} → ${esc(h.to_agent)} (${esc(h.category)})</div>
          <div class="list-row-sub mono">${esc(h.payload_json)}</div>
        </div>
        <div class="toolbar">
          <span class="badge badge-status ${h.status === "resolved" ? "badge-green" : "badge-yellow"}">${esc(h.status)}</span>
          ${h.status !== "resolved" ? `<button type="button" class="ghost-btn" data-resolve-handoff="${h.id}">Resolve Handoff</button>` : ""}
        </div>
      </div>`).join("");
    bodyHtml = `
      <section class="card">
        <h2 class="card-title">Enqueue Task Handoff</h2>
        <form id="handoffForm" class="toolbar">
          <input type="text" name="to_agent" class="field-input" placeholder="Target reviewer or agent (e.g. primary, critic)" required>
          <input type="text" name="category" class="field-input" placeholder="Category (e.g. review, draft)" value="task">
          <input type="text" name="note" class="field-input grow" placeholder="Task instructions..." required>
          <button type="submit" class="primary-btn">Send Handoff</button>
        </form>
        <div class="stack-list">${handoffRows || `<p class="muted-hint">No handoffs in queue.</p>`}</div>
      </section>`;
  }

  root.innerHTML = `<div class="subtabs">${tabs}</div>${bodyHtml}`;
  refreshTooltips(root);

  root.querySelectorAll("[data-prod-tab]").forEach((b) => b.addEventListener("click", () => { state.production.subtab = b.dataset.prodTab; renderProduction(root); }));
  root.querySelector("#newCutBtn")?.addEventListener("click", () => openCutEditor({}));
  root.querySelector("#recalcBuffersBtn")?.addEventListener("click", async () => { await apiFetch("/api/production/recalculate", { method: "POST" }); showToast("Recalculated channel buffers"); refreshCurrentView(); });
  root.querySelectorAll("tr[data-cut-idx]").forEach((tr) => tr.addEventListener("click", (e) => { if (!e.target.closest(".no-row-open")) openCutEditor(p.cuts[Number(tr.dataset.cutIdx)]); }));
  root.querySelectorAll("[data-ready-cut]").forEach((btn) => btn.addEventListener("click", async (e) => {
    e.stopPropagation();
    const cut = p.cuts[Number(btn.dataset.readyCut)];
    await apiFetch("/api/production/cuts", { method: "PUT", body: { ...cut, current_stage: "Ready / Scheduled", status: "active" } });
    showToast(`Marked '${cut.project_title}' ready`);
    refreshCurrentView();
  }));
  root.querySelectorAll("[data-pick-game]").forEach((btn) => btn.addEventListener("click", async () => {
    await apiFetch("/api/production/pool-pick", { method: "POST", body: { pool_name: btn.dataset.pickPool, game_id: btn.dataset.pickGame } });
    showToast(`Recorded pick for ${btn.dataset.pickGame}`);
    refreshCurrentView();
  }));
  root.querySelectorAll("[data-approve-draft]").forEach((btn) => btn.addEventListener("click", async () => {
    await apiFetch(`/api/production/drafts/${btn.dataset.approveDraft}`, { method: "PATCH", body: { status: "approved" } });
    showToast("Draft approved");
    refreshCurrentView();
  }));
  root.querySelectorAll("[data-open-draft]").forEach((btn) => btn.addEventListener("click", () => openDraftEditor(p.drafts[Number(btn.dataset.openDraft)])));
  root.querySelector("#handoffForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    await apiFetch("/api/production/handoffs", { method: "POST", body: { from_agent: "user_companion", to_agent: fd.get("to_agent"), category: fd.get("category"), payload: { instruction: fd.get("note") } } });
    showToast("Handoff enqueued");
    refreshCurrentView();
  });
  root.querySelectorAll("[data-resolve-handoff]").forEach((btn) => btn.addEventListener("click", async () => {
    await apiFetch("/api/production/handoffs", { method: "PATCH", body: { id: Number(btn.dataset.resolveHandoff), status: "resolved" } });
    showToast("Handoff resolved");
    refreshCurrentView();
  }));
}
