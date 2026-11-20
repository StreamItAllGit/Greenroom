/* @module static/js/api.js
 * @purpose Authenticated REST client, PIN unlock prompt, config/preferences synchronization, and theme/density DOM helpers.
 * @inputs API endpoint paths, request options, and state.deviceId / localStorage auth token.
 * @outputs Parsed JSON responses, settings/preferences persistence, and [data-theme]/[data-density] DOM updates. */

import { state, TOKEN_KEY, COLOR_SCHEME_KEY, THEME_MODE_KEY, DENSITY_KEY, showToast } from "./state.js";

let deferredInstallPrompt = null;

export function setDeferredInstallPrompt(promptEvent) {
  deferredInstallPrompt = promptEvent;
}

export function getDeferredInstallPrompt() {
  return deferredInstallPrompt;
}

export function promptPinUnlock() {
  const dlg = document.getElementById("pinDialog");
  if (dlg && !dlg.open) {
    const errEl = document.getElementById("pinError");
    const inpEl = document.getElementById("pinInput");
    if (errEl) errEl.hidden = true;
    if (inpEl) inpEl.value = "";
    dlg.showModal();
  }
}

export const BASE_PREFIX =
  (typeof window !== "undefined" && window.location?.pathname?.match(/^\/app\/[^/]+/)?.[0]) || "";

export function apiUrl(path) {
  if (typeof path !== "string" || !path.startsWith("/")) return path;
  if (BASE_PREFIX && (path === BASE_PREFIX || path.startsWith(`${BASE_PREFIX}/`))) return path;
  return `${BASE_PREFIX}${path}`;
}

export async function apiFetch(path, options = {}) {
  const headers = { ...(options.headers || {}), "X-Device-Id": state.deviceId };
  try {
    const tok = localStorage.getItem(TOKEN_KEY);
    if (tok) headers["Authorization"] = `Bearer ${tok}`;
  } catch (err) {
    console.debug("Unable to read auth token from localStorage:", err);
  }
  if (options.body && typeof options.body === "object" && !(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(options.body);
  }
  const resp = await fetch(apiUrl(path), { credentials: "same-origin", ...options, headers });
  if (resp.status === 401 && !["/api/auth/unlock", "/api/auth/status", "/api/auth/logout"].includes(path)) {
    promptPinUnlock();
    throw new Error("PIN authentication required");
  }
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new Error(data.error || `Request failed (${resp.status})`);
  }
  return data;
}

export async function savePreferences(updates, scope = "global") {
  const res = await apiFetch("/api/preferences", {
    method: "PUT",
    body: { scope, device_id: state.deviceId, updates },
  });
  state.prefs.global = { ...state.prefs.global, ...(res.global || {}) };
  state.prefs.device = { ...state.prefs.device, ...(res.device || {}) };
  return res;
}

export async function saveConfigSettings(patch) {
  const saved = await apiFetch("/api/config/settings", { method: "PUT", body: patch });
  state.settings.configSettings = saved;
  if (saved.enabledDomains) state.enabledDomains = { ...state.enabledDomains, ...saved.enabledDomains };
  return saved;
}

export function applyThemeMode(themeMode) {
  const normalized = themeMode === "light" ? "light" : "dark";
  document.documentElement.dataset.theme = normalized;
  if (document.body) document.body.dataset.theme = normalized;
  const meta = document.querySelector('meta[name="color-scheme"]');
  if (meta) meta.content = normalized;
  try {
    localStorage.setItem(THEME_MODE_KEY, normalized);
    localStorage.setItem(COLOR_SCHEME_KEY, normalized);
  } catch (err) {
    console.debug("Unable to persist themeMode in localStorage:", err);
  }
  return normalized;
}

export function applyColorSchemeMeta(schemeValue) {
  if (schemeValue === "light" || schemeValue === "dark") {
    return applyThemeMode(schemeValue);
  }
  const prefersLight = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches;
  const resolved = prefersLight ? "light" : "dark";
  document.documentElement.dataset.theme = resolved;
  if (document.body) document.body.dataset.theme = resolved;
  const meta = document.querySelector('meta[name="color-scheme"]');
  if (meta) meta.content = "dark light";
  try {
    localStorage.removeItem(COLOR_SCHEME_KEY);
    localStorage.removeItem(THEME_MODE_KEY);
  } catch (err) {
    console.debug("Unable to clear color-scheme in localStorage:", err);
  }
  return resolved;
}

export function applyDensityAttr(density) {
  const raw = String(density || "").trim().toLowerCase();
  const clean = raw === "touch" ? "comfortable" : ["compact", "default", "comfortable"].includes(raw) ? raw : "compact";
  document.documentElement.dataset.density = clean;
  if (document.body) document.body.dataset.density = clean;
  const footerBadge = document.getElementById("footerDensityBadge");
  if (footerBadge) footerBadge.textContent = clean.toUpperCase();
  try {
    localStorage.setItem(DENSITY_KEY, clean);
  } catch (err) {
    console.debug("Unable to persist density in localStorage:", err);
  }
  return clean;
}

export function buildCompanionOriginsList() {
  const net = state.network || {};
  const raw = [
    window.location.origin,
    ...(net.tailscale_urls || []),
    ...(net.lan_urls || []),
  ].filter((u) => typeof u === "string" && u.startsWith("http://"));
  return [...new Set(raw)].join(",");
}

export async function triggerPwaInstallPrompt() {
  if (!deferredInstallPrompt) return false;
  try {
    await deferredInstallPrompt.prompt();
    const choice = await deferredInstallPrompt.userChoice;
    if (choice?.outcome === "accepted") {
      deferredInstallPrompt = null;
      const btn = document.getElementById("installPwaBtn");
      if (btn) btn.hidden = true;
      showToast("Installing Greenroom Companion...");
      return true;
    }
  } catch (err) {
    console.debug("Install prompt dismissed or unavailable:", err);
  }
  return false;
}

export async function requestEnableTailscaleHttps(onDone) {
  try {
    showToast("Configuring Tailscale HTTPS proxy...");
    const res = await apiFetch("/api/network/enable-https", { method: "POST" });
    if (res.network) state.network = res.network;
    if (res.ok && res.https_url) {
      showToast(`Tailscale HTTPS active: ${res.https_url}`);
    } else if (res.ok) {
      showToast("Tailscale HTTPS enabled! Refresh Preferences to copy HTTPS URL.");
    } else {
      showToast(res.message || "Enable HTTPS in Tailscale Admin DNS first.");
    }
    if (onDone) onDone(res);
  } catch (err) {
    showToast(err.message || "Unable to configure Tailscale HTTPS");
  }
}
