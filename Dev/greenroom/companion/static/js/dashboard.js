/* @module static/js/dashboard.js
 * @purpose View 1: Command Center dashboard rendering enabled-domain KPI tiles, optional suite coexistence, buffers, and macro runway.
 * @inputs state.dashboard, state.enabledDomains, state.coexistence, and state.prefs.global.pinned_tasks.
 * @outputs Rendered Command Center DOM filtered by enabledDomains and interactive quick-action bindings. */

import { state, ANDROID_HINT_DISMISSED_KEY, esc, fmtNum, showToast, copyTextToClipboard, isDomainEnabled, switchView, refreshCurrentView } from "./state.js";
import { apiFetch, buildCompanionOriginsList, requestEnableTailscaleHttps } from "./api.js";
import { runGreenroomTask } from "./tasks.js";
import { renderCoexistenceBannerHtml, bindCoexistenceControls } from "./coexistence.js";
import { refreshTooltips } from "./tooltips.js";

export function renderDashboard(root) {
  const d = state.dashboard;
  if (!d || !root) return;
  const c = d.counts || {};
  const catOn = isDomainEnabled("catalog");
  const kitOn = isDomainEnabled("kitchen");
  const stuOn = isDomainEnabled("studio");

  const macro = d.kitchen?.macro_status || {};
  const targets = macro.targets || { caloriesKcal: 2000, proteinGrams: 150 };
  const targetCal = Number(targets.caloriesKcal ?? targets.calories ?? 2000);
  const targetPro = Number(targets.proteinGrams ?? targets.protein_g ?? 150);
  const logged = macro.consumed || macro.logged || { calories: 0, protein_g: 0 };
  const loggedCal = Number(logged.caloriesKcal ?? logged.calories ?? 0);
  const loggedPro = Number(logged.proteinGrams ?? logged.protein_g ?? 0);
  const runway = macro.remaining_runway || { calories: Math.max(0, targetCal - loggedCal), protein_g: Math.max(0, targetPro - loggedPro) };
  const calPct = Math.min(100, Math.round((loggedCal / Math.max(1, targetCal)) * 100));
  const proPct = Math.min(100, Math.round((loggedPro / Math.max(1, targetPro)) * 100));

  const pinnedIds = state.prefs.global.pinned_tasks || ["sync_telemetry", "collect_library", "recalculate_buffers", "backup"];
  const catalogById = Object.fromEntries((state.tasks.catalog || []).map((t) => [t.id, t]));
  const pinnedButtons = pinnedIds.map((tid) => {
    const meta = catalogById[tid] || { id: tid, label: tid };
    return `<button type="button" class="ghost-btn" data-run-task="${esc(tid)}">⚡ ${esc(meta.label)}</button>`;
  }).join("");

  const kpiCards = [
    catOn ? `<button type="button" class="kpi-card" data-goto-catalog="games"><div class="kpi-label">Games Library</div><div class="kpi-val">${fmtNum(c.games_total)}</div><div class="kpi-meta num">${fmtNum(c.games_completed)} completed · ${fmtNum(c.games_playing)} playing</div></button>` : "",
    catOn ? `<button type="button" class="kpi-card" data-goto-catalog="media"><div class="kpi-label">Media &amp; Literature</div><div class="kpi-val">${fmtNum(c.media_total)}</div><div class="kpi-meta num">+ ${fmtNum(c.books_total)} books &amp; manga</div></button>` : "",
    catOn ? `<button type="button" class="kpi-card" data-goto-catalog="wishlist"><div class="kpi-label">Deal Scout &amp; Wishlist</div><div class="kpi-val">${fmtNum(c.wishlist_seeking)}</div><div class="kpi-meta num">${fmtNum(c.prices_cached)} price entries</div></button>` : "",
    kitOn ? `<button type="button" class="kpi-card" data-goto-view="kitchen"><div class="kpi-label">Kitchen &amp; Pantry</div><div class="kpi-val">${fmtNum(c.pantry_in_stock)}</div><div class="kpi-meta num">${fmtNum(c.pantry_restock)} low / restock queued</div></button>` : "",
    stuOn ? `<button type="button" class="kpi-card" data-goto-view="production"><div class="kpi-label">Creator Studio</div><div class="kpi-val">${fmtNum(c.active_cuts)}</div><div class="kpi-meta num">${fmtNum(c.staged_drafts)} staged drafts · ${fmtNum(c.pending_handoffs)} handoffs</div></button>` : "",
  ].filter(Boolean).join("");

  const bufferRows = (d.buffers || []).map((b) => {
    const pct = Math.min(100, Math.round((b.total_pipelined / Math.max(1, b.target_buffer)) * 100));
    const badgeCls = b.health === "green" ? "badge-green" : b.health === "yellow" ? "badge-yellow" : "badge-red";
    const fillCls = b.health === "green" ? "" : b.health === "yellow" ? "warn" : "danger";
    return `<div class="list-row"><div class="list-row-main"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;gap:8px;"><strong>${esc(b.channel_id)}</strong><span class="badge badge-status ${badgeCls} num">${b.total_pipelined} / ${b.target_buffer} BUFFERED</span></div><div class="meter-track"><div class="meter-fill ${fillCls}" style="width:${pct}%"></div></div><div class="list-row-sub num" style="margin-top:4px;">Ready: ${b.ready_count} · Rendering: ${b.render_count} · Scheduled: ${b.scheduled_count}</div></div></div>`;
  }).join("") || `<p class="muted-hint">No channel buffers configured yet.</p>`;

  const ytStatsRows = (d.channel_stats || []).map((cs) => `<span class="badge badge-neutral num" title="${esc(cs.title || cs.channel_id)}|Channel subscriber and video metrics">${esc(cs.handle || cs.title)}: ${fmtNum(cs.subscriber_count)} subs · ${fmtNum(cs.video_count)} vids · ${fmtNum(cs.view_count)} views</span>`).join("");

  const cutRows = (d.active_cuts || []).slice(0, 6).map((cut) => `<div class="list-row"><div class="list-row-main"><div class="list-row-title">${esc(cut.project_title)}</div><div class="list-row-sub">${esc(cut.channel_id)} · ${esc(cut.current_stage)}</div></div><span class="badge badge-status ${cut.status === "active" ? "badge-green" : "badge-blue"}">${esc(cut.status)}</span></div>`).join("") || `<p class="muted-hint">No active production cuts.</p>`;

  const expiringRows = (d.kitchen?.expiring_pantry || []).slice(0, 5).map((item) => `<div class="list-row"><div class="list-row-main"><div class="list-row-title">${esc(item.name)}</div><div class="list-row-sub">${esc(item.location)} · ${esc(item.stock_status || "In Stock")} · ${esc(item.waste_tier || "")}</div></div><button type="button" class="ghost-btn" data-quick-deplete="${item.id}">Deplete Item</button></div>`).join("") || `<p class="muted-hint">No expiring or low-stock alerts.</p>`;

  const mealPicks = (d.kitchen?.meal_candidates || []).slice(0, 3).map((m) => `<div class="list-row"><div class="list-row-main"><div class="list-row-title">#${m.rank} ${esc(m.name)}</div><div class="list-row-sub num">${m.prep_time_minutes}m prep · ${m.estimated_macros?.calories || 0} kcal · ${m.estimated_macros?.protein_g || 0}g protein</div></div><button type="button" class="ghost-btn" data-quick-log-meal='${esc(JSON.stringify(m))}'>Log Meal</button></div>`).join("");

  const telemetryBadges = (d.telemetry || []).map((t) => {
    const cls = t.status === "active" ? "badge-green" : t.status === "error" ? "badge-red" : "badge-neutral";
    return `<span class="badge badge-status ${cls}">${esc(t.service_name)} (${fmtNum(t.records_synced)})</span>`;
  }).join("");

  const draftRows = (d.drafts || []).slice(0, 4).map((dr) => `<div class="list-row"><div class="list-row-main"><div class="list-row-title">[${esc(dr.agent_name)}] ${esc(dr.title)}</div><div class="list-row-sub">${esc(dr.target_type)} · ${esc(dr.status)}</div></div><button type="button" class="ghost-btn" data-jump-draft="${dr.id}">Open Draft</button></div>`).join("") || `<p class="muted-hint">No staged review drafts.</p>`;

  let androidHintDismissed = false;
  try { androidHintDismissed = localStorage.getItem(ANDROID_HINT_DISMISSED_KEY) === "1"; } catch (err) { console.debug("Android hint pref skipped:", err); }
  const showAndroidHttpBanner = !window.isSecureContext && /Android/i.test(navigator.userAgent) && !androidHintDismissed;
  const originsList = buildCompanionOriginsList();
  const androidInstallCard = showAndroidHttpBanner ? `<section class="card" style="border-color:var(--accent);"><div class="card-head"><h2 class="card-title">📲 Enable App Install on Android Chrome</h2><button type="button" class="ghost-btn" id="dismissAndroidHintBtn" aria-label="Dismiss install guide">✕</button></div><p class="muted-hint">Chrome on Android hides app installation over plain HTTP. Copy your origin URLs into Chrome flags or enable Tailscale HTTPS in Display &amp; Pairing.</p><div class="toolbar"><button type="button" class="primary-btn" data-copy-text="${esc(originsList)}" data-copy-label="Companion origins">1. Copy Origins</button><button type="button" class="ghost-btn" data-copy-text="chrome://flags/#unsafely-treat-insecure-origin-as-secure" data-copy-label="Chrome Flag URL">2. Copy Flag URL</button><button type="button" class="ghost-btn" id="dashEnableHttpsBtn">🔒 Enable Tailscale HTTPS</button></div></section>` : "";

  const studioPanels = stuOn ? `
    <section class="card contain-panel">
      <div class="card-head"><h2 class="card-title">Channel Release Buffers &amp; Video Telemetry</h2><button type="button" class="chip-btn" data-goto-view="production">Manage Studio →</button></div>
      <div class="stack-list">${bufferRows}</div>
      ${ytStatsRows ? `<div class="toolbar">${ytStatsRows}</div>` : ""}
    </section>
    <section class="card contain-panel">
      <div class="card-head"><h2 class="card-title">Active Workstation Cuts</h2><button type="button" class="chip-btn" data-goto-view="production">Edit Cuts →</button></div>
      <div class="stack-list">${cutRows}</div>
    </section>` : "";

  const kitchenPanel = kitOn ? `
    <section class="card contain-panel">
      <div class="card-head"><h2 class="card-title">Today's Macro Runway &amp; Kitchen Picks</h2><span class="badge badge-status badge-blue">${esc(macro.schedule_type || "STANDARD DAILY")}</span></div>
      <div class="stack-list">
        <div><div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:4px;" class="num"><span>Calories: <strong>${fmtNum(loggedCal)}</strong> / ${fmtNum(targetCal)} kcal</span><span>${fmtNum(runway.calories)} left</span></div><div class="meter-track"><div class="meter-fill" style="width:${calPct}%"></div></div></div>
        <div><div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:4px;" class="num"><span>Protein: <strong>${fmtNum(loggedPro)}g</strong> / ${fmtNum(targetPro)}g</span><span>${fmtNum(runway.protein_g)}g left</span></div><div class="meter-track"><div class="meter-fill" style="width:${proPct}%"></div></div></div>
      </div>
      ${mealPicks ? `<div class="stack-list">${mealPicks}</div>` : ""}
      <h3 class="card-title" style="font-size:13px;">Expiring &amp; Low Stock</h3>
      <div class="stack-list">${expiringRows}</div>
    </section>` : "";

  const noModulesBanner = !catOn && !kitOn && !stuOn ? `<section class="card"><div class="card-head"><h2 class="card-title">No Workspace Modules Active</h2><button type="button" class="primary-btn" id="dashEnableModulesBtn">Enable Modules</button></div><p class="muted-hint">Enable Catalog, Kitchen, or Creator Studio in Settings to display module summaries here.</p></section>` : "";

  root.innerHTML = `
    ${androidInstallCard}
    ${noModulesBanner}
    ${catOn ? renderCoexistenceBannerHtml() : ""}
    ${kpiCards ? `<section class="kpi-grid" aria-label="Workspace Summary Metrics">${kpiCards}</section>` : ""}
    <section class="card">
      <div class="card-head"><h2 class="card-title">Quick-Run Pinned Tasks</h2><button type="button" class="chip-btn" data-goto-view="tasks">All Tasks &amp; Logs →</button></div>
      <div class="toolbar">${pinnedButtons}</div>
    </section>
    <div class="dash-grid">
      ${studioPanels}
      ${kitchenPanel}
      <section class="card contain-panel">
        <div class="card-head">
          <h2 class="card-title">${stuOn ? "Review Drafts &amp; Service Health" : "External Service Health"}</h2>
          <div class="toolbar">
            <button type="button" class="chip-btn" id="dashOpenAccountsBtn">🔑 Keys &amp; Logins</button>
            <button type="button" class="chip-btn" data-run-task="sync_telemetry">Sync APIs</button>
          </div>
        </div>
        <div class="toolbar">${telemetryBadges || '<span class="muted-hint">No external sync telemetry recorded yet.</span>'}</div>
        ${stuOn ? `<div class="stack-list">${draftRows}</div>` : ""}
      </section>
    </div>`;

  bindCoexistenceControls(root);
  refreshTooltips(root);
  root.querySelector("#dashEnableModulesBtn")?.addEventListener("click", () => { state.settings.subtab = "modules"; switchView("settings"); });
  root.querySelectorAll("[data-goto-view]").forEach((el) => el.addEventListener("click", () => switchView(el.dataset.gotoView)));
  root.querySelectorAll("[data-goto-catalog]").forEach((el) => el.addEventListener("click", () => { state.catalog.domain = el.dataset.gotoCatalog; state.catalog.offset = 0; state.catalog.filters = {}; switchView("catalog"); }));
  root.querySelectorAll("[data-run-task]").forEach((el) => el.addEventListener("click", () => runGreenroomTask(el.dataset.runTask)));
  root.querySelectorAll("[data-quick-deplete]").forEach((el) => el.addEventListener("click", async () => { await apiFetch(`/api/kitchen/pantry/${el.dataset.quickDeplete}`, { method: "PATCH", body: { stock_status: "Depleted" } }); showToast("Marked depleted and recorded in pantry history"); refreshCurrentView(); }));
  root.querySelectorAll("[data-quick-log-meal]").forEach((el) => el.addEventListener("click", async () => {
    const meal = JSON.parse(el.dataset.quickLogMeal), em = meal.estimated_macros || {};
    await apiFetch("/api/kitchen/nutrition", { method: "POST", body: { calories: em.calories || 0, protein_g: em.protein_g || 0, carbs_g: em.carbs_g || 0, fat_g: em.fat_g || 0, notes: meal.name } });
    showToast(`Logged ${meal.name} (${em.calories || 0} kcal)`);
    refreshCurrentView();
  }));
  root.querySelectorAll("[data-jump-draft]").forEach((el) => el.addEventListener("click", () => { state.production.subtab = "drafts"; switchView("production"); }));
  root.querySelectorAll("[data-copy-text]").forEach((btn) => btn.addEventListener("click", async () => { const ok = await copyTextToClipboard(btn.dataset.copyText); showToast(ok ? `Copied ${btn.dataset.copyLabel || "text"}` : "Unable to copy automatically"); }));
  root.querySelector("#dashOpenAccountsBtn")?.addEventListener("click", () => { state.settings.subtab = "accounts"; switchView("settings"); });
  root.querySelector("#dashEnableHttpsBtn")?.addEventListener("click", () => requestEnableTailscaleHttps(() => refreshCurrentView({ background: true })));
  root.querySelector("#dismissAndroidHintBtn")?.addEventListener("click", () => {
    try { localStorage.setItem(ANDROID_HINT_DISMISSED_KEY, "1"); } catch (err) { console.debug("Hint dismiss skipped:", err); }
    renderDashboard(root);
  });
}
