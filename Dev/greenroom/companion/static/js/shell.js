/* @module static/js/shell.js
 * @purpose App shell controls: collapsible sidebar (220px <-> 56px), domain nav filtering, footer status bar, drive telemetry, and docked log console.
 * @inputs User clicks, drag/arrow resize events on #drawerResizeHandle, enabledDomains, and drive telemetry payloads.
 * @outputs Updated #appShell grid classes, filtered nav buttons, footer status bar text, and resized #liveLogDrawer DOM state. */

import { state, SIDEBAR_COLLAPSED_KEY, LOG_DRAWER_SIZE_KEY, esc, fmtNum } from "./state.js";
import { apiFetch, savePreferences, saveConfigSettings } from "./api.js";
import { refreshTooltips } from "./tooltips.js";

const PRESET_HEIGHTS = { S: "25dvh", M: "40dvh", L: "60dvh" };

export function applyEnabledDomainsToNav(enabledDomains) {
  const domains = { catalog: true, kitchen: false, studio: false, ...(enabledDomains || state.enabledDomains || {}) };
  state.enabledDomains = domains;
  document.querySelectorAll("[data-domain]").forEach((el) => {
    const dom = el.dataset.domain;
    if (dom) el.hidden = domains[dom] === false;
  });
  const activeLabels = [
    domains.catalog !== false ? "Catalog" : null,
    domains.kitchen ? "Kitchen" : null,
    domains.studio ? "Studio" : null,
  ].filter(Boolean);
  const summaryEl = document.getElementById("footerModulesSummary");
  if (summaryEl) {
    summaryEl.textContent = `Modules: ${activeLabels.length ? activeLabels.join(", ") : "None"}`;
  }
}

export function updateFooterStatusBar({ connected = true, taskLabel = "" } = {}) {
  const badge = document.getElementById("footerConnBadge");
  const taskEl = document.getElementById("footerTaskState");
  const verEl = document.getElementById("footerVersionText");
  const connText = document.getElementById("connStatusText");
  const connDot = document.querySelector("#connStatusPill .dot");
  if (badge) {
    badge.className = `badge badge-status ${connected ? "badge-green" : "badge-red"}`;
    badge.textContent = connected ? "ONLINE" : "OFFLINE";
  }
  if (connText) connText.textContent = connected ? "ONLINE" : "OFFLINE";
  if (connDot) connDot.className = `dot ${connected ? "ok" : "err"}`;
  if (taskEl) {
    if (state.tasks.dryRun) {
      taskEl.textContent = taskLabel ? `DRY RUN · ${taskLabel}` : "DRY RUN READY";
    } else {
      taskEl.textContent = taskLabel ? `RUNNING: ${taskLabel}` : state.tasks.paused ? "PAUSED" : "IDLE";
    }
  }
  if (verEl && state.version) {
    verEl.textContent = state.version.startsWith("v") ? state.version : `v${state.version}`;
  }
}

export function applySidebarCollapseDOM(collapsed) {
  state.sidebarCollapsed = Boolean(collapsed);
  const appShell = document.getElementById("appShell");
  const appSidebar = document.getElementById("appSidebar");
  const collapseBtn = document.getElementById("sidebarCollapseBtn");

  appShell?.classList.toggle("sidebar-collapsed", state.sidebarCollapsed);
  appSidebar?.classList.toggle("collapsed", state.sidebarCollapsed);
  if (collapseBtn) {
    collapseBtn.textContent = state.sidebarCollapsed ? "»" : "«";
    collapseBtn.setAttribute("aria-expanded", String(!state.sidebarCollapsed));
  }
}

export function toggleSidebarCollapse(forceState) {
  const next = typeof forceState === "boolean" ? forceState : !state.sidebarCollapsed;
  applySidebarCollapseDOM(next);
  try {
    localStorage.setItem(SIDEBAR_COLLAPSED_KEY, next ? "1" : "0");
  } catch (err) {
    console.debug("Sidebar state persist skipped:", err);
  }
  const nextLayout = { ...(state.prefs.device?.uiLayout || {}), sidebarCollapsed: next, logDrawerPreset: state.logDrawerPreset || "M" };
  if (state.prefs.device) state.prefs.device.uiLayout = nextLayout;
  savePreferences({ uiLayout: nextLayout }, "device").catch((err) => console.debug("Sidebar uiLayout sync skipped:", err));
  return next;
}

export function initSidebarCollapse() {
  let initial = false;
  try {
    const raw = localStorage.getItem(SIDEBAR_COLLAPSED_KEY);
    if (raw !== null) initial = raw === "1" || raw === "true";
    else if (typeof state.prefs.device?.uiLayout?.sidebarCollapsed === "boolean") initial = state.prefs.device.uiLayout.sidebarCollapsed;
  } catch (err) {
    console.debug("Sidebar state read skipped:", err);
  }
  applySidebarCollapseDOM(initial);
  document.getElementById("sidebarCollapseBtn")?.addEventListener("click", () => toggleSidebarCollapse());
}

export function renderDrawerLogs() {
  const box = document.getElementById("drawerLogStream");
  if (!box) return;
  const lvlFilter = state.logDrawerLevel || "all";
  const lines = (state.tasks.logs || [])
    .filter((l) => lvlFilter === "all" || String(l.level || (l.event === "error" ? "error" : "info")).toLowerCase().includes(lvlFilter))
    .slice(-100);
  box.innerHTML = lines.length
    ? lines
        .map((l) => {
          const lv = String(l.level || (l.event === "error" ? "error" : "info")).toUpperCase();
          const col = lv.startsWith("ERR") ? "#f87171" : lv.startsWith("WARN") ? "#fbbf24" : "#34d399";
          return `<div><span style="color:#8b98ab">[${esc((l.timestamp || "").slice(11, 19))}]</span> <span style="color:${col}">[${esc(lv)}]</span> ${esc(l.message || l.event)}</div>`;
        })
        .join("")
    : `<div style="color:#8b98ab">No log entries matching filter '${esc(lvlFilter)}'.</div>`;
  box.scrollTop = box.scrollHeight;
}

export function applyLogDrawerSizePreset(preset, customHeightPx = null) {
  const drawer = document.getElementById("liveLogDrawer");
  if (!drawer) return;
  const cleanPreset = ["S", "M", "L"].includes(preset) ? preset : "M";
  state.logDrawerPreset = cleanPreset;
  drawer.dataset.sizePreset = cleanPreset;
  if (typeof customHeightPx === "number" && Number.isFinite(customHeightPx)) {
    const clamped = Math.round(Math.max(120, Math.min(Math.max(180, window.innerHeight - 160), customHeightPx)));
    state.logDrawerHeightPx = clamped;
    drawer.style.setProperty("--drawer-height", `${clamped}px`);
    drawer.style.height = `${clamped}px`;
  } else {
    state.logDrawerHeightPx = null;
    const cssVal = PRESET_HEIGHTS[cleanPreset] || "40dvh";
    drawer.style.setProperty("--drawer-height", cssVal);
    drawer.style.height = cssVal;
  }
  drawer.querySelectorAll("[data-drawer-size]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.drawerSize === cleanPreset && customHeightPx === null);
  });
  try {
    localStorage.setItem(LOG_DRAWER_SIZE_KEY, JSON.stringify({ preset: cleanPreset, heightPx: state.logDrawerHeightPx }));
  } catch (err) {
    console.debug("Drawer size persist skipped:", err);
  }
}

export function toggleLogDrawer(forceState, skipSave = false) {
  const drawer = document.getElementById("liveLogDrawer");
  if (!drawer) return false;
  state.logDrawerOpen = typeof forceState === "boolean" ? forceState : !state.logDrawerOpen;
  drawer.hidden = !state.logDrawerOpen;
  if (state.logDrawerOpen) renderDrawerLogs();
  if (!skipSave && state.settings.configSettings) {
    const nextLayout = { ...(state.settings.configSettings.layout || {}), isTerminalVisible: state.logDrawerOpen };
    saveConfigSettings({ layout: nextLayout }).catch((err) => console.debug("Terminal visibility save skipped:", err));
  }
  return state.logDrawerOpen;
}

export function initLogDrawerControls(onClearExtra) {
  const drawer = document.getElementById("liveLogDrawer");
  const handle = document.getElementById("drawerResizeHandle");
  try {
    const saved = JSON.parse(localStorage.getItem(LOG_DRAWER_SIZE_KEY) || "null");
    applyLogDrawerSizePreset(saved?.preset || "M", saved?.heightPx ?? null);
  } catch (err) {
    console.debug("Log drawer size read skipped:", err);
    applyLogDrawerSizePreset("M", null);
  }
  document.getElementById("toggleLogDrawerBtn")?.addEventListener("click", () => toggleLogDrawer());
  document.getElementById("footerLogsToggleBtn")?.addEventListener("click", () => toggleLogDrawer());
  document.getElementById("drawerCloseBtn")?.addEventListener("click", () => toggleLogDrawer(false));
  document.getElementById("drawerLogLevelFilter")?.addEventListener("change", (e) => {
    state.logDrawerLevel = e.target.value;
    renderDrawerLogs();
  });
  document.getElementById("drawerClearLogsBtn")?.addEventListener("click", () => {
    state.tasks.logs = [];
    renderDrawerLogs();
    if (onClearExtra) onClearExtra();
  });
  drawer?.querySelectorAll("[data-drawer-size]").forEach((btn) => {
    btn.addEventListener("click", () => applyLogDrawerSizePreset(btn.dataset.drawerSize, null));
  });
  if (handle && drawer) {
    let dragging = false, startY = 0, startH = 0;
    const onPointerMove = (e) => {
      if (!dragging) return;
      applyLogDrawerSizePreset(state.logDrawerPreset, startH + (startY - e.clientY));
    };
    const onPointerUp = () => {
      if (!dragging) return;
      dragging = false;
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onPointerUp);
    };
    handle.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      dragging = true;
      startY = e.clientY;
      startH = drawer.getBoundingClientRect().height || 260;
      window.addEventListener("pointermove", onPointerMove);
      window.addEventListener("pointerup", onPointerUp);
    });
    handle.addEventListener("dblclick", () => applyLogDrawerSizePreset("M", null));
    handle.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
      e.preventDefault();
      const curH = drawer.getBoundingClientRect().height || 260;
      applyLogDrawerSizePreset(state.logDrawerPreset, curH + (e.key === "ArrowUp" ? 24 : -24));
    });
  }
}

export async function refreshDriveTelemetry() {
  if (document.hidden) return;
  try {
    let res;
    try { res = await apiFetch("/api/v1/system/drives"); } catch { res = await apiFetch("/api/telemetry/drives"); }
    state.drives = res.drives || [];
    const box = document.getElementById("driveTelemetryFooter");
    if (!box) return;
    if (!state.drives.length) { box.innerHTML = `<div class="muted-hint">Drive space ready</div>`; return; }
    box.innerHTML = state.drives.slice(0, 3).map((d) => {
      const driveName = d.driveRoot || d.drive || "Drive";
      const pct = Math.min(100, Math.max(0, Number(d.usedPct ?? d.used_pct ?? 0)));
      const freeGb = d.free_gb ?? (d.freeBytes ? (Number(d.freeBytes) / 1073741824).toFixed(1) : 0);
      const totalGb = d.total_gb ?? (d.totalBytes ? (Number(d.totalBytes) / 1073741824).toFixed(1) : 0);
      const fillCls = pct >= 90 ? "danger" : pct >= 78 ? "warn" : "";
      return `<div class="drive-row" data-tooltip="${esc(d.label || driveName)}|${freeGb} GB free of ${totalGb} GB (${pct}% used)"><div class="drive-row-head"><strong>${esc(driveName)}</strong><span>${fmtNum(freeGb)} GB free</span></div><div class="meter-track" style="height:4px;"><div class="meter-fill ${fillCls}" style="width:${pct}%"></div></div></div>`;
    }).join("");
    refreshTooltips(box);
  } catch (err) {
    console.debug("Drive telemetry refresh skipped:", err);
  }
}
