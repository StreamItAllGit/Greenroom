/* @module static/app.js
 * @purpose Greenroom Companion ES module entrypoint orchestrating boot, domain gating, onboarding wizard, view routing, and hotkeys.
 * @inputs Modular controllers in /static/js/*.js and browser DOM/lifecycle events.
 * @outputs Initialized PWA shell, active workspace routing, and global window bindings. */

import { state, TOKEN_KEY, COLOR_SCHEME_KEY, LAST_VIEW_KEY, VIEW_TITLES, VIEW_HOTKEY_ORDER, esc, isDomainEnabled, showToast, yieldToMain, catalogCacheKey, dbCacheKey, registerRouter } from "./js/state.js";
import { apiFetch, promptPinUnlock, savePreferences, applyThemeMode, applyDensityAttr, triggerPwaInstallPrompt, setDeferredInstallPrompt } from "./js/api.js";
import { initSidebarCollapse, toggleSidebarCollapse, initLogDrawerControls, toggleLogDrawer, refreshDriveTelemetry, applyEnabledDomainsToNav, updateFooterStatusBar } from "./js/shell.js";
import { initTooltips, refreshTooltips } from "./js/tooltips.js";
import { loadCoexistenceStatus } from "./js/coexistence.js";
import { renderDashboard } from "./js/dashboard.js";
import { loadCatalogData, renderCatalog } from "./js/catalog.js";
import { renderKitchen } from "./js/kitchen.js";
import { renderProduction } from "./js/production.js";
import { syncQueueControlsUI, setActiveTaskBanner, applyTaskStateSnapshot, connectTaskStream, runGreenroomTask, runTask, enqueueGreenroomTask, toggleDryRunMode, stopAllRunningJobs, renderLogConsoleOnly, renderTasks } from "./js/tasks.js";
import { renderSchedules } from "./js/schedules.js";
import { renderSettingsView } from "./js/settings.js";
import { openOnboardingWizard } from "./js/onboarding.js";
import { loadDbTablePage, renderDatabaseExplorer } from "./js/explorer.js";
import { setupLightDismissFallback, openOmniSearch, executeOmniSearch, openPreferencesModal } from "./js/modals.js";

let navSeq = 0, viewPrefTimer = null, prefetchStarted = false, omniTimer = null;

function renderWorkspaceSkeleton(root, label = "Loading workspace...") {
  if (root) root.innerHTML = `<div class="stack-list" aria-busy="true" aria-label="${esc(label)}"><div class="skeleton-pulse" style="height:76px;"></div><div class="skeleton-pulse" style="height:180px;"></div><div class="skeleton-pulse" style="height:240px;"></div></div>`;
}

function isUserEditingInWorkspace(root) {
  const active = document.activeElement;
  return Boolean(active && root && root.contains(active) && ["INPUT", "TEXTAREA", "SELECT"].includes(active.tagName));
}

function hasCachedViewData(viewName) {
  if (viewName === "dashboard") return Boolean(state.dashboard);
  if (viewName === "catalog") return ["scout", "compare", "dats"].includes(state.catalog.domain) || Boolean(state.catalog.cacheByKey[catalogCacheKey()] || state.catalog.data);
  if (viewName === "kitchen") return Boolean(state.kitchen.data);
  if (viewName === "production") return Boolean(state.production.data);
  if (viewName === "tasks") return Boolean(state.tasks.catalog?.length);
  if (viewName === "schedules") return Boolean(state.schedules.data);
  if (viewName === "database") return Boolean(state.db.cacheByKey[dbCacheKey()] || state.db.page);
  if (viewName === "settings") return Boolean(state.settings.doctor && state.settings.notifications && state.settings.configSettings);
  return false;
}

function renderCachedView(viewName, root) {
  if (viewName === "dashboard") renderDashboard(root);
  else if (viewName === "catalog") {
    if (!["scout", "compare", "dats"].includes(state.catalog.domain)) state.catalog.data = state.catalog.cacheByKey[catalogCacheKey()] || state.catalog.data;
    renderCatalog(root, renderWorkspaceSkeleton);
  } else if (viewName === "kitchen") renderKitchen(root);
  else if (viewName === "production") renderProduction(root);
  else if (viewName === "tasks") renderTasks(root);
  else if (viewName === "schedules") renderSchedules(root);
  else if (viewName === "database") { state.db.page = state.db.cacheByKey[dbCacheKey()] || state.db.page; renderDatabaseExplorer(root, renderWorkspaceSkeleton); }
  else if (viewName === "settings") renderSettingsView(root);
}

export async function switchView(viewName, persist = true) {
  if (!VIEW_TITLES[viewName]) viewName = "dashboard";
  if (!persist && ["catalog", "kitchen", "production"].includes(viewName) && !isDomainEnabled(viewName)) viewName = "dashboard";
  const root = document.getElementById("workspaceRoot");
  state.view = viewName;
  state.selectedRowKeys.clear();
  document.getElementById("viewHeading").textContent = VIEW_TITLES[viewName];
  document.querySelectorAll("[data-view]").forEach((btn) => btn.classList.toggle("active", btn.dataset.view === viewName));
  if (persist) {
    try { localStorage.setItem(LAST_VIEW_KEY, viewName); } catch (err) { console.debug("Last view storage skipped:", err); }
    clearTimeout(viewPrefTimer);
    if (state.prefs.device?.last_view !== viewName) viewPrefTimer = setTimeout(() => savePreferences({ last_view: viewName }, "device").catch(() => {}), 2000);
  }
  try {
    if (hasCachedViewData(viewName)) {
      renderCachedView(viewName, root);
      await yieldToMain();
      refreshCurrentView({ background: true }).catch(() => {});
    } else {
      renderWorkspaceSkeleton(root, VIEW_TITLES[viewName]);
      await yieldToMain();
      await refreshCurrentView({ background: false });
    }
  } catch (err) { showToast(err.message || "Failed to switch view"); }
}

export async function refreshCurrentView({ background = false } = {}) {
  const root = document.getElementById("workspaceRoot");
  const seq = ++navSeq, targetView = state.view;
  const canRender = () => seq === navSeq && state.view === targetView && !(background && isUserEditingInWorkspace(root));
  try {
    if (targetView === "dashboard") {
      const dash = await apiFetch("/api/dashboard");
      state.dashboard = dash;
      state.network = dash.network || state.network;
      if (Array.isArray(dash.recent_runs) && !state.tasks.runs.length) state.tasks.runs = dash.recent_runs;
      if (dash.tasks_state) applyTaskStateSnapshot(dash.tasks_state);
      if (isDomainEnabled("catalog")) await loadCoexistenceStatus();
      if (canRender()) renderDashboard(root);
    } else if (targetView === "catalog") {
      await loadCatalogData();
      if (canRender()) renderCatalog(root, renderWorkspaceSkeleton);
    } else if (targetView === "kitchen") {
      const qs = new URLSearchParams({ location: state.kitchen.location, stock_status: state.kitchen.stock_status, q: state.kitchen.q });
      state.kitchen.data = await apiFetch(`/api/kitchen?${qs}`);
      if (canRender()) renderKitchen(root);
    } else if (targetView === "production") {
      state.production.data = await apiFetch("/api/production");
      if (canRender()) renderProduction(root);
    } else if (targetView === "tasks") {
      const st = await apiFetch("/api/tasks");
      state.tasks.runs = st.runs || []; state.tasks.telemetry = st.telemetry || [];
      applyTaskStateSnapshot(st);
      if (canRender()) renderTasks(root);
    } else if (targetView === "schedules") {
      state.schedules.data = await apiFetch("/api/schedules");
      if (canRender()) renderSchedules(root);
    } else if (targetView === "database") {
      await loadDbTablePage();
      if (canRender()) renderDatabaseExplorer(root, renderWorkspaceSkeleton);
    } else if (targetView === "settings") {
      const [doc, notif, snaps, quar, cfgSet, domCfg, profCfg] = await Promise.all([
        apiFetch("/api/system/doctor"),
        apiFetch("/api/notifications/settings"),
        apiFetch("/api/config/snapshots"),
        apiFetch("/api/quarantine"),
        apiFetch("/api/config/settings").catch(() => null),
        apiFetch("/api/config/domains").catch(() => null),
        apiFetch("/api/config/profiles").catch(() => null),
        loadCoexistenceStatus(),
      ]);
      state.settings.doctor = doc; state.settings.notifications = notif;
      state.settings.snapshots = snaps.snapshots || []; state.settings.quarantine = quar.items || [];
      if (cfgSet) state.settings.configSettings = cfgSet;
      if (domCfg) state.settings.domainsConfig = domCfg;
      if (profCfg) state.settings.profilesConfig = profCfg;
      if (canRender()) renderSettingsView(root);
    }
  } catch (err) {
    if (!background && seq === navSeq && err.message !== "PIN authentication required") {
      root.innerHTML = `<div class="card"><p class="error-text">Failed to load workspace: ${esc(err.message)}</p></div>`;
    }
  }
}

registerRouter({ switchView, refreshCurrentView });

async function prefetchSiblingWorkspaces() {
  if (prefetchStarted) return;
  prefetchStarted = true;
  const jobs = [
    ["dashboard", async () => { if (!state.dashboard) state.dashboard = await apiFetch("/api/dashboard"); }],
    ...(isDomainEnabled("catalog") ? [["catalog", async () => { const k = "games||{}||desc|0"; if (!state.catalog.cacheByKey[k]) state.catalog.cacheByKey[k] = await apiFetch("/api/catalog/games?q=&sort_by=&sort_dir=desc&limit=50&offset=0"); }]] : []),
    ...(isDomainEnabled("kitchen") ? [["kitchen", async () => { if (!state.kitchen.data) state.kitchen.data = await apiFetch("/api/kitchen?location=&stock_status=available&q="); }]] : []),
    ...(isDomainEnabled("studio") ? [["production", async () => { if (!state.production.data) state.production.data = await apiFetch("/api/production"); }]] : []),
    ["tasks", async () => { if (!state.tasks.runs.length) { const st = await apiFetch("/api/tasks"); state.tasks.catalog = st.catalog || state.tasks.catalog; state.tasks.runs = st.runs || []; } }],
  ];
  for (const [viewKey, job] of jobs) {
    if (state.view === viewKey) continue;
    await yieldToMain();
    try { await job(); } catch (err) { console.debug("Prefetch skipped:", err); }
  }
}

async function loadInitialData() {
  try {
    const [prefs, v1Status, cfgSet, domCfg, profCfg] = await Promise.all([
      apiFetch(`/api/preferences?device_id=${encodeURIComponent(state.deviceId)}`),
      apiFetch("/api/v1/status").catch(() => null),
      apiFetch("/api/config/settings").catch(() => null),
      apiFetch("/api/config/domains").catch(() => null),
      apiFetch("/api/config/profiles").catch(() => null),
    ]);
    state.prefs.global = { ...state.prefs.global, ...(prefs.global || {}) };
    state.prefs.device = { ...state.prefs.device, ...(prefs.device || {}) };
    if (v1Status?.version) {
      state.version = String(v1Status.version).replace(/^v/, "");
      const badge = document.getElementById("versionBadge");
      if (badge) badge.textContent = `v${state.version}`;
    }
    if (cfgSet) state.settings.configSettings = cfgSet;
    if (domCfg) state.settings.domainsConfig = domCfg;
    if (profCfg) {
      state.settings.profilesConfig = profCfg;
      if (profCfg.seriesPools?.default_pool_name) state.production.poolName = profCfg.seriesPools.default_pool_name;
    }
    applyThemeMode(cfgSet?.themeMode || state.prefs.global.theme || "dark");
    applyDensityAttr(cfgSet?.density || state.prefs.device.density || "compact");
    applyEnabledDomainsToNav(cfgSet?.enabledDomains || domCfg?.enabledDomains || state.enabledDomains);
    updateFooterStatusBar({ connected: true });
    if (cfgSet?.layout?.isTerminalVisible && window.innerWidth > 900 && cfgSet?.isOnboardingCompleted !== false) {
      toggleLogDrawer(true, true);
    }
    connectTaskStream();
    refreshDriveTelemetry().catch(() => {});
    setInterval(() => { if (!document.hidden) refreshDriveTelemetry().catch(() => {}); }, 30000);
    let localLastView = null;
    try { localLastView = localStorage.getItem(LAST_VIEW_KEY); } catch (err) { console.debug("Last view read skipped:", err); }
    await switchView(localLastView || cfgSet?.layout?.defaultTab || state.prefs.device.last_view || "dashboard", false);
    if (cfgSet && cfgSet.isOnboardingCompleted === false) {
      openOnboardingWizard({ canClose: false, onComplete: () => refreshCurrentView({ background: false }) });
    }
    const scheduleIdle = window.requestIdleCallback || ((cb) => setTimeout(cb, 300));
    scheduleIdle(() => prefetchSiblingWorkspaces().catch(() => {}));
  } catch (err) { if (err.message !== "PIN authentication required") showToast(err.message); }
}

async function boot() {
  setActiveTaskBanner(null);
  setupLightDismissFallback();
  initTooltips();
  initSidebarCollapse();
  initLogDrawerControls(() => { if (state.view === "tasks") renderLogConsoleOnly(); });
  window.addEventListener("storage", (e) => { if (e.key === COLOR_SCHEME_KEY && (e.newValue === "dark" || e.newValue === "light")) applyThemeMode(e.newValue); });
  window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); setDeferredInstallPrompt(e); const btn = document.getElementById("installPwaBtn"); if (btn) btn.hidden = false; });
  window.addEventListener("appinstalled", () => { setDeferredInstallPrompt(null); const btn = document.getElementById("installPwaBtn"); if (btn) btn.hidden = true; showToast("Greenroom installed"); });

  document.getElementById("installPwaBtn")?.addEventListener("click", () => triggerPwaInstallPrompt());
  document.querySelectorAll("[data-view]").forEach((btn) => btn.addEventListener("click", () => switchView(btn.dataset.view)));
  document.getElementById("topAccountsBtn")?.addEventListener("click", () => { state.settings.subtab = "accounts"; switchView("settings"); });
  document.getElementById("topQueueNextBtn")?.addEventListener("click", () => enqueueGreenroomTask("tick"));
  document.getElementById("topPauseQueueBtn")?.addEventListener("click", async () => {
    const res = await apiFetch("/api/tasks/pause", { method: "POST", body: { paused: !state.tasks.paused } });
    state.tasks.paused = Boolean(res.paused); syncQueueControlsUI(); showToast(state.tasks.paused ? "Task queue paused" : "Task queue resumed");
  });
  document.getElementById("topSuspendQueueBtn")?.addEventListener("click", async () => {
    const res = await apiFetch("/api/tasks/suspend", { method: "POST", body: { suspended: !state.tasks.suspended } });
    state.tasks.suspended = Boolean(res.suspended); syncQueueControlsUI(); showToast(state.tasks.suspended ? "Task execution suspended" : "Task execution unsuspended");
  });
  document.getElementById("topStopAllBtn")?.addEventListener("click", () => stopAllRunningJobs(document.getElementById("workspaceRoot")));
  document.getElementById("topDryRunBtn")?.addEventListener("click", () => toggleDryRunMode());
  document.getElementById("quickSnapshotBtn")?.addEventListener("click", async () => { const snap = await apiFetch("/api/config/snapshots", { method: "POST", body: { label: "topbar_quick_snapshot" } }); showToast(`Saved config snapshot ${snap.id}`); });
  document.getElementById("openShortcutsBtn")?.addEventListener("click", () => document.getElementById("shortcutsDialog")?.showModal());
  document.getElementById("omniTriggerBtn")?.addEventListener("click", openOmniSearch);
  document.getElementById("refreshBtn")?.addEventListener("click", () => refreshCurrentView({ background: false }));
  document.getElementById("openPrefsBtn")?.addEventListener("click", openPreferencesModal);
  document.getElementById("mobilePrefsBtn")?.addEventListener("click", openPreferencesModal);
  document.getElementById("omniInput")?.addEventListener("input", (e) => { clearTimeout(omniTimer); omniTimer = setTimeout(() => executeOmniSearch(e.target.value).catch(() => {}), 160); });

  window.addEventListener("keydown", (e) => {
    const tag = (e.target?.tagName || "").toLowerCase();
    const isTyping = tag === "input" || tag === "textarea" || tag === "select" || e.target?.isContentEditable;
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "b") { e.preventDefault(); toggleSidebarCollapse(); return; }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openOmniSearch(); return; }
    if ((e.ctrlKey || e.metaKey) && e.key === "`") { e.preventDefault(); toggleLogDrawer(); return; }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s" && !isTyping) {
      e.preventDefault();
      apiFetch("/api/config/snapshots", { method: "POST", body: { label: "hotkey_ctrl_s" } }).then((snap) => showToast(`Saved config snapshot ${snap.id}`)).catch((err) => showToast(err.message));
      return;
    }
    if (!isTyping && !e.ctrlKey && !e.metaKey && !e.altKey) {
      if (e.key === "?") { e.preventDefault(); document.getElementById("shortcutsDialog")?.showModal(); return; }
      const num = Number(e.key);
      if (num >= 1 && num <= VIEW_HOTKEY_ORDER.length) {
        const target = VIEW_HOTKEY_ORDER[num - 1];
        if (!["catalog", "kitchen", "production"].includes(target) || isDomainEnabled(target)) { e.preventDefault(); switchView(target); }
      }
    }
  });

  document.getElementById("pinForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const res = await apiFetch("/api/auth/unlock", { method: "POST", body: { pin: document.getElementById("pinInput").value, device_id: state.deviceId } });
      if (res.token) { try { localStorage.setItem(TOKEN_KEY, res.token); } catch (err) { console.debug("Token save skipped:", err); } }
      document.getElementById("pinDialog")?.close();
      await loadInitialData();
    } catch (err) { const errEl = document.getElementById("pinError"); if (errEl) { errEl.textContent = err.message || "Invalid PIN"; errEl.hidden = false; } }
  });

  if ("serviceWorker" in navigator && window.isSecureContext) navigator.serviceWorker.register("./sw.js?v=0.3.0", { scope: "./" }).catch(() => {});
  const auth = await apiFetch("/api/auth/status");
  state.pinEnabled = Boolean(auth.pin_enabled); state.network = auth.network || null;
  if (auth.version) { state.version = String(auth.version).replace(/^v/, ""); document.getElementById("versionBadge").textContent = `v${state.version}`; }
  if (!auth.authorized) { promptPinUnlock(); return; }
  await loadInitialData();
  refreshTooltips(document);
}

Object.assign(window, { state, switchView, refreshCurrentView, toggleSidebarCollapse, toggleLogDrawer, runTask, runGreenroomTask, stopAllRunningJobs, toggleDryRunMode, openOnboardingWizard });
boot().catch((err) => { console.error("Companion boot error:", err); showToast(err.message || "Failed to initialize Greenroom"); });
