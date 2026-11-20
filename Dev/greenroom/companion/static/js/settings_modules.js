/* @module static/js/settings_modules.js
 * @purpose Modules & Profiles panel in Settings: domain toggles, macro presets, series pool math, reviewer aliases, and appearance.
 * @inputs state.settings.{configSettings,domainsConfig,profilesConfig}, /api/config/{domains,profiles,settings} endpoints.
 * @outputs Rendered Modules & Profiles HTML and persistence handlers for settings.js. */

import { state, esc, showToast, refreshCurrentView } from "./state.js";
import { apiFetch, applyThemeMode, applyDensityAttr } from "./api.js";
import { applyEnabledDomainsToNav } from "./shell.js";
import { openOnboardingWizard } from "./onboarding.js";
import { refreshTooltips } from "./tooltips.js";

export const DOMAIN_FALLBACKS = [
  {
    domainId: "catalog",
    title: "Media & Games Catalog",
    whatItIsFor: "Tracking owned games, disc rips, movies, shows, books, and wishlists in one searchable local library.",
    whatItDoes: "Deduplicates multi-copy titles, compares wishlists against price guides, and verifies ROM/ISO checksums against No-Intro & Redump DATs.",
    offlineVsApis: "Works 100% offline with local folders, CSV/DAT imports, and SQLite. Optional integrations: Steam, RetroAchievements, Plex, PriceCharting CSV.",
  },
  {
    domainId: "kitchen",
    title: "Kitchen & Macro Planner",
    whatItIsFor: "Managing household pantry inventory, expiration rescue suggestions, grocery restocks, and daily nutrition targets.",
    whatItDoes: "Tracks pantry stock locations, generates waste-priority meal ideas, and calculates remaining daily calories, protein, carbs, and fat.",
    offlineVsApis: "Works 100% offline in SQLite with built-in macro presets (Standard Daily, Active / Shift Worker, Fitness / Cut, or Custom).",
  },
  {
    domainId: "studio",
    title: "Creator Studio & Agent Drops",
    whatItIsFor: "Organizing creator production cuts, release buffers, weighted game/topic series pools, review queues, and drop staging.",
    whatItDoes: "Monitors channel release buffers, rotates weighted series pools, surfaces eligible review candidates, and processes validated drop envelopes.",
    offlineVsApis: "Works offline for cuts, pools, reviews, and local drops/agent_queue/. Optional APIs: YouTube, Twitch, Buffer, Bluesky.",
  },
];

const PRESET_FALLBACKS = [
  { presetId: "standard_daily", label: "Standard Daily", description: "Balanced 2,000 kcal / 150g protein daily baseline." },
  { presetId: "active_shift", label: "Active / Shift Worker", description: "2,650 kcal on active workdays paired with 1,950 kcal rest days." },
  { presetId: "fitness_cut", label: "Fitness / Cut", description: "1,850 kcal high-protein cut days with 2,250 kcal weekend refeed." },
  { presetId: "custom", label: "Custom", description: "User-defined custom daily calorie and macronutrient targets." },
];

export function renderModulesSettingsHtml() {
  const cfg = state.settings.configSettings || {};
  const domCfg = state.settings.domainsConfig || {};
  const profCfg = state.settings.profilesConfig || {};
  const domains = domCfg.enabledDomains || cfg.enabledDomains || state.enabledDomains || { catalog: true, kitchen: false, studio: false };
  const domainMeta = Array.isArray(domCfg.domains) && domCfg.domains.length ? domCfg.domains : DOMAIN_FALLBACKS;
  const presets = Array.isArray(profCfg.macroPresets) && profCfg.macroPresets.length ? profCfg.macroPresets : PRESET_FALLBACKS;
  const activePreset = profCfg.activeMacroPresetId || cfg.activeMacroPresetId || "standard_daily";
  const custom = profCfg.customMacroTargets || cfg.customMacroTargets || { caloriesKcal: 2000, proteinGrams: 150, carbsGrams: 200, fatGrams: 65 };
  const pools = profCfg.seriesPools || {};
  const defPool = pools.default || {};
  const poolName = pools.default_pool_name || pools.defaultPoolName || defPool.display_name || "Default Pool";
  const aliases = Array.isArray(cfg.reviewerAliases) ? cfg.reviewerAliases : (profCfg.reviewerAliases?.aliases || ["primary", "critic"]);
  const themeMode = cfg.themeMode === "light" ? "light" : "dark";
  const density = ["compact", "default", "comfortable"].includes(cfg.density) ? cfg.density : "compact";
  const watchHours = Number(cfg.watchBufferMinHours ?? 10);

  const cardsHtml = domainMeta.map((m) => {
    const enabled = domains[m.domainId] !== false && Boolean(domains[m.domainId]);
    return `
      <div class="module-card ${enabled ? "is-enabled" : ""}">
        <div class="card-head">
          <strong>${esc(m.title)}</strong>
          <label class="toolbar" style="cursor:pointer;">
            <span class="badge badge-status ${enabled ? "badge-green" : "badge-neutral"}">${enabled ? "ENABLED" : "DISABLED"}</span>
            <input type="checkbox" data-mod-domain="${esc(m.domainId)}" ${enabled ? "checked" : ""}>
          </label>
        </div>
        <p class="module-detail"><strong>What it is for:</strong> ${esc(m.whatItIsFor)}</p>
        <p class="module-detail"><strong>What it does:</strong> ${esc(m.whatItDoes)}</p>
        <p class="module-detail"><strong>Offline vs. APIs:</strong> ${esc(m.offlineVsApis)}</p>
      </div>`;
  }).join("");

  return `
    <section class="card contain-panel" id="settingsModulesCard">
      <div class="card-head">
        <div>
          <h2 class="card-title">🧩 Active Workspace Modules</h2>
          <p class="card-subtitle">Toggle modules anytime. Disabling a module hides its navigation tab and pauses its background tasks while preserving all saved database rows.</p>
        </div>
        <div class="toolbar">
          <button type="button" class="ghost-btn" id="rerunOnboardingBtn" title="Re-Run Setup Wizard|Open the guided two-step module and preset wizard">✨ Re-Run Setup Wizard</button>
          <button type="button" class="primary-btn" id="saveModulesDomainsBtn">Save Active Modules</button>
        </div>
      </div>
      <div class="module-grid">${cardsHtml}</div>
    </section>

    <form id="modulesProfilesForm" class="dash-grid">
      <section class="card contain-panel">
        <div class="card-head">
          <div>
            <h2 class="card-title">🥗 Macro Preset &amp; Appearance</h2>
            <p class="card-subtitle">Select daily nutrition baselines, color theme, and layout density</p>
          </div>
          <span class="badge badge-status badge-blue">${esc(themeMode.toUpperCase())} · ${esc(density.toUpperCase())}</span>
        </div>
        <div class="form-grid">
          <div class="form-field span-full">
            <label for="modMacroPresetSelect">Nutrition &amp; Macro Preset</label>
            <select id="modMacroPresetSelect" name="activeMacroPresetId" class="field-select">
              ${presets.map((p) => `<option value="${esc(p.presetId)}" ${p.presetId === activePreset ? "selected" : ""}>${esc(p.label)} — ${esc(p.description || "")}</option>`).join("")}
            </select>
          </div>
          <div class="form-field"><label for="modCalInput">Custom Calories (kcal)</label><input id="modCalInput" type="number" step="10" name="caloriesKcal" class="field-input num" value="${esc(custom.caloriesKcal ?? 2000)}"></div>
          <div class="form-field"><label for="modProInput">Custom Protein (g)</label><input id="modProInput" type="number" step="1" name="proteinGrams" class="field-input num" value="${esc(custom.proteinGrams ?? 150)}"></div>
          <div class="form-field"><label for="modCarbInput">Custom Carbs (g)</label><input id="modCarbInput" type="number" step="1" name="carbsGrams" class="field-input num" value="${esc(custom.carbsGrams ?? 200)}"></div>
          <div class="form-field"><label for="modFatInput">Custom Fat (g)</label><input id="modFatInput" type="number" step="1" name="fatGrams" class="field-input num" value="${esc(custom.fatGrams ?? 65)}"></div>
          <div class="form-field">
            <label for="modThemeSelect">Color Theme</label>
            <select id="modThemeSelect" name="themeMode" class="field-select">
              <option value="dark" ${themeMode === "dark" ? "selected" : ""}>Dark (Deep Slate)</option>
              <option value="light" ${themeMode === "light" ? "selected" : ""}>Light</option>
            </select>
          </div>
          <div class="form-field">
            <label for="modDensitySelect">Workspace Density</label>
            <select id="modDensitySelect" name="density" class="field-select">
              <option value="compact" ${density === "compact" ? "selected" : ""}>Compact (Dense Tables)</option>
              <option value="default" ${density === "default" ? "selected" : ""}>Default (Balanced)</option>
              <option value="comfortable" ${density === "comfortable" ? "selected" : ""}>Comfortable (Relaxed)</option>
            </select>
          </div>
        </div>
      </section>

      <section class="card contain-panel">
        <div class="card-head">
          <div>
            <h2 class="card-title">🎬 Series Pool &amp; Reviewer Profiles</h2>
            <p class="card-subtitle">Configure default weighted series rotation, reviewer tags, and watch buffer hours</p>
          </div>
        </div>
        <div class="form-grid">
          <div class="form-field"><label for="modPoolNameInput">Default Series Pool Name</label><input id="modPoolNameInput" name="defaultPoolName" class="field-input" value="${esc(poolName)}"></div>
          <div class="form-field"><label for="modWatchBufInput">Watch Buffer Minimum (Hours)</label><input id="modWatchBufInput" type="number" min="1" max="168" name="watchBufferMinHours" class="field-input num" value="${esc(watchHours)}"></div>
          <div class="form-field"><label for="modInitWeightInput">Initial Pool Weight</label><input id="modInitWeightInput" type="number" step="0.5" name="initialWeight" class="field-input num" value="${esc(defPool.initial_weight ?? 5)}"></div>
          <div class="form-field"><label for="modPickPenInput">Pick Penalty (-Weight)</label><input id="modPickPenInput" type="number" step="0.5" name="pickPenalty" class="field-input num" value="${esc(defPool.pick_penalty ?? 3)}"></div>
          <div class="form-field"><label for="modFloorWeightInput">Floor Weight</label><input id="modFloorWeightInput" type="number" step="0.5" name="floorWeight" class="field-input num" value="${esc(defPool.floor_weight ?? 1)}"></div>
          <div class="form-field"><label for="modBonusWeightInput">Unpicked Bonus (+Weight)</label><input id="modBonusWeightInput" type="number" step="0.5" name="unpickedBonus" class="field-input num" value="${esc(defPool.unpicked_bonus ?? 1)}"></div>
          <div class="form-field span-full"><label for="modAliasesInput">Reviewer Aliases (comma-separated)</label><input id="modAliasesInput" name="reviewerAliases" class="field-input" placeholder="primary, critic" value="${esc(aliases.join(", "))}"></div>
        </div>
        <div class="toolbar" style="justify-content:flex-end;">
          <button type="submit" class="primary-btn" id="saveModulesProfilesBtn">Save Profiles &amp; Appearance</button>
        </div>
      </section>
    </form>`;
}

export function bindModulesSettings(root) {
  const card = root?.querySelector("#settingsModulesCard");
  if (!card) return;
  refreshTooltips(root);
  root.querySelector("#rerunOnboardingBtn")?.addEventListener("click", () => {
    openOnboardingWizard({ canClose: true, onComplete: () => refreshCurrentView({ background: false }) });
  });
  root.querySelector("#modThemeSelect")?.addEventListener("change", (e) => applyThemeMode(e.target.value));
  root.querySelector("#modDensitySelect")?.addEventListener("change", (e) => applyDensityAttr(e.target.value));
  root.querySelector("#saveModulesDomainsBtn")?.addEventListener("click", async () => {
    const enabledDomains = {};
    root.querySelectorAll("[data-mod-domain]").forEach((cb) => { enabledDomains[cb.dataset.modDomain] = Boolean(cb.checked); });
    const res = await apiFetch("/api/config/domains", { method: "PUT", body: { enabledDomains } });
    state.settings.domainsConfig = res;
    if (res.enabledDomains) applyEnabledDomainsToNav(res.enabledDomains);
    state.dashboard = null;
    showToast("Saved active workspace modules");
    refreshCurrentView({ background: false });
  });
  root.querySelector("#modulesProfilesForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    const activeMacroPresetId = String(fd.get("activeMacroPresetId") || "standard_daily");
    const customMacroTargets = {
      caloriesKcal: Number(fd.get("caloriesKcal") || 2000),
      proteinGrams: Number(fd.get("proteinGrams") || 150),
      carbsGrams: Number(fd.get("carbsGrams") || 200),
      fatGrams: Number(fd.get("fatGrams") || 65),
    };
    const themeMode = applyThemeMode(String(fd.get("themeMode") || "dark"));
    const density = applyDensityAttr(String(fd.get("density") || "compact"));
    const reviewerAliases = String(fd.get("reviewerAliases") || "").split(",").map((s) => s.trim()).filter(Boolean);
    const watchBufferMinHours = Math.max(1, Number(fd.get("watchBufferMinHours") || 10));
    const seriesPools = {
      default_pool_name: String(fd.get("defaultPoolName") || "Default Pool").trim() || "Default Pool",
      default: {
        pool_id: "default_pool",
        display_name: String(fd.get("defaultPoolName") || "Default Pool").trim() || "Default Pool",
        is_enabled: true,
        initial_weight: Number(fd.get("initialWeight") || 5),
        pick_penalty: Number(fd.get("pickPenalty") || 3),
        floor_weight: Number(fd.get("floorWeight") || 1),
        unpicked_bonus: Number(fd.get("unpickedBonus") || 1),
      },
    };
    const [savedSet, savedProf] = await Promise.all([
      apiFetch("/api/config/settings", {
        method: "PUT",
        body: { activeMacroPresetId, customMacroTargets, themeMode, density, reviewerAliases, watchBufferMinHours },
      }),
      apiFetch("/api/config/profiles", {
        method: "PUT",
        body: { activeMacroPresetId, customMacroTargets, seriesPools, reviewerAliases: { aliases: reviewerAliases } },
      }).catch(() => null),
    ]);
    state.settings.configSettings = savedSet;
    if (savedProf) state.settings.profilesConfig = savedProf;
    state.production.poolName = seriesPools.default_pool_name;
    showToast("Saved nutrition presets, pool settings, and appearance");
    refreshCurrentView({ background: true });
  });
}
