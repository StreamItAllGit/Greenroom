/* @module static/js/state.js
 * @purpose Centralized reactive client state, storage keys, domain enablement helpers, formatting utilities, and view router hooks.
 * @inputs Browser localStorage, user interaction mutations, and REST/SSE payloads.
 * @outputs Exported state singleton, storage constants, domain gate helpers, formatting functions, and router dispatchers. */

export const DEVICE_KEY = "gr_companion_device_id";
export const TOKEN_KEY = "gr_companion_token";
export const COLOR_SCHEME_KEY = "color-scheme";
export const THEME_MODE_KEY = "gr_theme_mode";
export const DENSITY_KEY = "gr_density";
export const LAST_VIEW_KEY = "gr_companion_last_view";
export const SIDEBAR_COLLAPSED_KEY = "gr_sidebar_collapsed";
export const LOG_DRAWER_SIZE_KEY = "gr_log_drawer_size";
export const ANDROID_HINT_DISMISSED_KEY = "gr_companion_android_https_hint_dismissed";

export function getDeviceId() {
  try {
    let id = localStorage.getItem(DEVICE_KEY);
    if (!id) {
      const platformTag = /Mobi|Android|iPhone|iPad/i.test(navigator.userAgent) ? "phone" : "desktop";
      id = `${platformTag}-${Math.random().toString(36).slice(2, 10)}`;
      localStorage.setItem(DEVICE_KEY, id);
    }
    return id;
  } catch (err) {
    console.debug("Device ID storage fallback:", err);
    return "session-device";
  }
}

const tasksState = {
  logs: [],
  activeTask: null,
  queue: [],
  queueDepth: 0,
  paused: false,
  suspended: false,
  _dryRun: false,
  catalog: [],
  runs: [],
  telemetry: [],
  get dryRun() { return this._dryRun; },
  set dryRun(val) { this._dryRun = Boolean(val); },
  get dry_run() { return this._dryRun; },
  set dry_run(val) { this._dryRun = Boolean(val); },
};

export const state = {
  deviceId: getDeviceId(),
  view: "dashboard",
  get activeView() { return this.view; },
  set activeView(v) { this.view = v; },
  version: "0.3.0",
  sidebarCollapsed: false,
  logDrawerOpen: false,
  logDrawerLevel: "all",
  logDrawerPreset: "M",
  logDrawerHeightPx: null,
  selectedRowKeys: new Set(),
  drives: [],
  enabledDomains: { catalog: true, kitchen: false, studio: false },
  coexistence: {
    detectedPeers: [],
    sharedRoots: [],
    activeLocks: [],
    recommendations: [],
    autoAlignWithSisterApps: false,
    useSisterVerifiedHashes: true,
    leaveMultipartFilesSplit: true,
    autoMergeMultipartMedia: false,
  },
  prefs: {
    global: {
      theme: "dark",
      pinned_tasks: ["sync_telemetry", "collect_library", "recalculate_buffers", "backup"],
      saved_views: [],
      useSisterVerifiedHashes: true,
      autoAlignWithSisterApps: false,
      leaveMultipartFilesSplit: true,
      autoMergeMultipartMedia: false,
    },
    device: {
      density: "compact",
      last_view: "dashboard",
      uiLayout: { sidebarCollapsed: false, logDrawerPreset: "M" },
    },
  },
  network: null,
  pinEnabled: false,
  dashboard: null,
  catalog: {
    domain: "games",
    q: "",
    filters: {},
    sort_by: "",
    sort_dir: "desc",
    sortCol: "",
    sortDir: "asc",
    selectedIndices: new Set(),
    offset: 0,
    limit: 50,
    data: null,
    scoutResult: null,
    cacheByKey: {},
    compare: {
      left_set: "infinitebacklog",
      right_set: "pricecharting_owned",
      platform: "Nintendo Switch",
      region: "NTSC-U",
      q: "",
      include_backups: false,
      bucket: "only_right",
      data: null,
    },
    datsSummary: null,
  },
  kitchen: { location: "", stock_status: "available", q: "", data: null },
  production: { subtab: "cuts", poolName: "Default Pool", seriesFilter: "", data: null },
  tasks: tasksState,
  schedules: { data: null, status: null },
  settings: {
    subtab: "modules",
    doctor: null,
    healReport: null,
    configSettings: null,
    domainsConfig: null,
    profilesConfig: null,
    notifications: null,
    snapshots: [],
    quarantine: [],
  },
  db: {
    tables: [],
    selectedTable: "games",
    q: "",
    sort_by: "",
    sort_dir: "desc",
    selectedIndices: new Set(),
    offset: 0,
    limit: 50,
    page: null,
    cacheByKey: {},
  },
  sse: null,
};

export const VIEW_TITLES = {
  dashboard: "Command Center",
  catalog: "Catalog & Deal Scout",
  kitchen: "Kitchen, Pantry & Macros",
  production: "Creator Studio, Pools & Drafts",
  tasks: "Task Runner & Queue",
  schedules: "Schedules & Catch-Up",
  database: "Database Explorer",
  settings: "Settings & Modules",
};

export const VIEW_HOTKEY_ORDER = [
  "dashboard",
  "catalog",
  "kitchen",
  "production",
  "tasks",
  "schedules",
  "database",
  "settings",
];

let _router = {
  switchView: async () => {},
  refreshCurrentView: async () => {},
};

export function registerRouter(handlers) {
  _router = { ..._router, ...handlers };
}

export function switchView(viewName, persist = true) {
  return _router.switchView(viewName, persist);
}

export function refreshCurrentView(opts = {}) {
  return _router.refreshCurrentView(opts);
}

export function isDomainEnabled(domain) {
  const key = String(domain || "").trim().toLowerCase();
  if (!key) return true;
  if (key === "production") return Boolean(state.enabledDomains?.studio);
  if (key in (state.enabledDomains || {})) return Boolean(state.enabledDomains[key]);
  return key === "catalog";
}

export function esc(val) {
  if (val === null || val === undefined) return "";
  return String(val)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function safeHttpUrl(val) {
  if (!val || typeof val !== "string") return "";
  const trimmed = val.trim();
  if (!trimmed) return "";
  try {
    const parsed = new URL(trimmed, window.location.origin);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.href : "";
  } catch (err) {
    console.debug("Invalid URL skipped:", err);
    return "";
  }
}

export function fmtMoney(val) {
  if (val === null || val === undefined || val === "") return "—";
  const num = Number(val);
  return Number.isFinite(num) ? `$${num.toFixed(2)}` : "—";
}

export function fmtNum(val) {
  if (val === null || val === undefined || val === "") return "0";
  const num = Number(val);
  return Number.isFinite(num) ? num.toLocaleString() : esc(val);
}

export function showToast(msg) {
  const region = document.getElementById("toastRegion");
  if (!region) return;
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = msg;
  region.appendChild(el);
  setTimeout(() => el.remove(), 3400);
}

export async function yieldToMain() {
  if ("scheduler" in window && "yield" in window.scheduler) {
    return await window.scheduler.yield();
  }
  return new Promise((resolve) => setTimeout(resolve, 0));
}

export async function copyTextToClipboard(text) {
  if (!text) return false;
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (err) {
      console.debug("Navigator clipboard write failed, using fallback:", err);
    }
  }
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  ta.style.pointerEvents = "none";
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch (err) {
    console.debug("Fallback clipboard copy failed:", err);
  }
  ta.remove();
  return ok;
}

export function tableLabelStyle(headers) {
  return headers
    .slice(1)
    .map((h, idx) => `--label-${idx + 2}: '${String(h).replace(/'/g, "")}'`)
    .join("; ");
}

export function catalogCacheKey(cat = state.catalog) {
  return `${cat.domain}|${cat.q}|${JSON.stringify(cat.filters || {})}|${cat.sort_by}|${cat.sort_dir}|${cat.offset}`;
}

export function dbCacheKey(dbState = state.db) {
  return `${dbState.selectedTable}|${dbState.q}|${dbState.sort_by}|${dbState.sort_dir}|${dbState.offset}`;
}

export function downloadBlobFile(filename, content, mimeType) {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1500);
}

export function exportRowsAsCsv(filenamePrefix, rows) {
  if (!Array.isArray(rows) || !rows.length) {
    showToast("No rows available to export.");
    return;
  }
  const cols = Object.keys(rows[0]);
  const escCsv = (v) => {
    if (v === null || v === undefined) return "";
    const s = typeof v === "object" ? JSON.stringify(v) : String(v);
    return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const csvLines = [cols.map(escCsv).join(","), ...rows.map((r) => cols.map((c) => escCsv(r[c])).join(","))];
  const cleanName = filenamePrefix.replace(/\.(csv|json)$/i, "");
  downloadBlobFile(`${cleanName}_${new Date().toISOString().slice(0, 10)}.csv`, csvLines.join("\r\n"), "text/csv;charset=utf-8");
  showToast(`Exported ${rows.length} row(s) to CSV`);
}

export function exportRowsAsJson(filenamePrefix, rows) {
  if (!Array.isArray(rows) || !rows.length) {
    showToast("No rows available to export.");
    return;
  }
  const cleanName = filenamePrefix.replace(/\.(csv|json)$/i, "");
  downloadBlobFile(`${cleanName}_${new Date().toISOString().slice(0, 10)}.json`, JSON.stringify(rows, null, 2), "application/json;charset=utf-8");
  showToast(`Exported ${rows.length} row(s) to JSON`);
}
