/* @module static/js/onboarding.js
 * @purpose 960px-max First-Run Setup Wizard modal guiding module selection, macro presets, appearance, and optional suite alignment.
 * @inputs isOnboardingCompleted flag from /api/config/settings, /api/config/domains, and /api/config/profiles.
 * @outputs Interactive 2-step Setup Wizard dialog saving via POST /api/config/onboarding and updating nav/theme/density. */

import { state, esc, showToast } from "./state.js";
import { apiFetch, applyThemeMode, applyDensityAttr } from "./api.js";
import { applyEnabledDomainsToNav } from "./shell.js";
import { DOMAIN_FALLBACKS } from "./settings_modules.js";

function readInitialWizardState() {
  const cfg = state.settings.configSettings || {};
  const domains = cfg.enabledDomains || state.enabledDomains || { catalog: true, kitchen: false, studio: false };
  const custom = cfg.customMacroTargets || { caloriesKcal: 2000, proteinGrams: 150, carbsGrams: 200, fatGrams: 65 };
  return {
    step: 1,
    enabledDomains: {
      catalog: domains.catalog !== false,
      kitchen: Boolean(domains.kitchen),
      studio: Boolean(domains.studio),
    },
    activeMacroPresetId: cfg.activeMacroPresetId || "standard_daily",
    customMacroTargets: {
      caloriesKcal: Number(custom.caloriesKcal ?? 2000),
      proteinGrams: Number(custom.proteinGrams ?? 150),
      carbsGrams: Number(custom.carbsGrams ?? 200),
      fatGrams: Number(custom.fatGrams ?? 65),
    },
    themeMode: cfg.themeMode === "light" ? "light" : "dark",
    density: ["compact", "default", "comfortable"].includes(cfg.density) ? cfg.density : "compact",
    shouldAutoAlignSisterApps: Boolean(cfg.shouldAutoAlignSisterApps),
  };
}

export function openOnboardingWizard({ canClose = false, onComplete } = {}) {
  const dlg = document.getElementById("onboardingDialog");
  const body = document.getElementById("onboardingBody");
  const closeBtn = document.getElementById("onboardingCloseBtn");
  if (!dlg || !body) return;

  dlg.setAttribute("closedby", canClose ? "any" : "none");
  if (closeBtn) closeBtn.hidden = !canClose;

  const wiz = readInitialWizardState();
  const domainMeta = Array.isArray(state.settings.domainsConfig?.domains) && state.settings.domainsConfig.domains.length
    ? state.settings.domainsConfig.domains
    : DOMAIN_FALLBACKS;

  const renderStep = () => {
    const stepBadges = `
      <div class="wizard-steps">
        <span class="badge badge-status ${wiz.step === 1 ? "badge-green" : "badge-neutral"}">STEP 1: MODULES</span>
        <span class="badge badge-status ${wiz.step === 2 ? "badge-green" : "badge-neutral"}">STEP 2: PRESETS &amp; APPEARANCE</span>
      </div>`;

    if (wiz.step === 1) {
      const cards = domainMeta.map((m) => {
        const checked = Boolean(wiz.enabledDomains[m.domainId]);
        return `
          <label class="module-card ${checked ? "is-enabled" : ""}" style="cursor:pointer;">
            <div class="card-head">
              <strong>${esc(m.title)}</strong>
              <input type="checkbox" data-wiz-domain="${esc(m.domainId)}" ${checked ? "checked" : ""}>
            </div>
            <p class="module-detail"><strong>What it is for:</strong> ${esc(m.whatItIsFor)}</p>
            <p class="module-detail"><strong>What it does:</strong> ${esc(m.whatItDoes)}</p>
            <p class="module-detail"><strong>Offline vs. APIs:</strong> ${esc(m.offlineVsApis)}</p>
          </label>`;
      }).join("");

      body.innerHTML = `
        <div class="stack-list">
          ${stepBadges}
          <p class="muted-hint">Choose which workspaces to enable. Catalog is ready out of the box, and Kitchen or Creator Studio can be enabled now or later without losing any saved data.</p>
          <div class="module-grid">${cards}</div>
          <div class="wizard-footer">
            <span class="muted-hint">All modules work offline with local SQLite storage.</span>
            <button type="button" class="primary-btn" id="wizNextStepBtn">Next: Presets &amp; Appearance →</button>
          </div>
        </div>`;

      body.querySelectorAll("[data-wiz-domain]").forEach((cb) => {
        cb.addEventListener("change", () => {
          wiz.enabledDomains[cb.dataset.wizDomain] = Boolean(cb.checked);
          cb.closest(".module-card")?.classList.toggle("is-enabled", Boolean(cb.checked));
        });
      });
      body.querySelector("#wizNextStepBtn")?.addEventListener("click", () => {
        wiz.step = 2;
        renderStep();
      });
    } else {
      const showCustom = wiz.activeMacroPresetId === "custom";
      body.innerHTML = `
        <form id="wizFinishForm" class="stack-list">
          ${stepBadges}
          <div class="form-grid">
            <div class="form-field">
              <label for="wizThemeSelect">Color Theme</label>
              <select id="wizThemeSelect" class="field-select">
                <option value="dark" ${wiz.themeMode === "dark" ? "selected" : ""}>Dark (Deep Slate Default)</option>
                <option value="light" ${wiz.themeMode === "light" ? "selected" : ""}>Light</option>
              </select>
            </div>
            <div class="form-field">
              <label for="wizDensitySelect">Interface Density</label>
              <select id="wizDensitySelect" class="field-select">
                <option value="compact" ${wiz.density === "compact" ? "selected" : ""}>Compact (More Rows Visible)</option>
                <option value="default" ${wiz.density === "default" ? "selected" : ""}>Default (Standard)</option>
                <option value="comfortable" ${wiz.density === "comfortable" ? "selected" : ""}>Comfortable (Larger Controls)</option>
              </select>
            </div>
            <div class="form-field span-full">
              <label for="wizPresetSelect">Daily Nutrition Preset (Used When Kitchen Is Enabled)</label>
              <select id="wizPresetSelect" class="field-select">
                <option value="standard_daily" ${wiz.activeMacroPresetId === "standard_daily" ? "selected" : ""}>Standard Daily (2,000 kcal / 150g protein)</option>
                <option value="active_shift" ${wiz.activeMacroPresetId === "active_shift" ? "selected" : ""}>Active / Shift Worker (2,650 kcal workday / 1,950 kcal rest)</option>
                <option value="fitness_cut" ${wiz.activeMacroPresetId === "fitness_cut" ? "selected" : ""}>Fitness / Cut (1,850 kcal cut / 2,250 kcal weekend refeed)</option>
                <option value="custom" ${wiz.activeMacroPresetId === "custom" ? "selected" : ""}>Custom Targets</option>
              </select>
            </div>
          </div>

          <div class="form-grid" id="wizCustomMacroBox" ${showCustom ? "" : "hidden"}>
            <div class="form-field"><label for="wizCalInp">Calories (kcal)</label><input id="wizCalInp" type="number" class="field-input num" value="${esc(wiz.customMacroTargets.caloriesKcal)}"></div>
            <div class="form-field"><label for="wizProInp">Protein (g)</label><input id="wizProInp" type="number" class="field-input num" value="${esc(wiz.customMacroTargets.proteinGrams)}"></div>
            <div class="form-field"><label for="wizCarbInp">Carbs (g)</label><input id="wizCarbInp" type="number" class="field-input num" value="${esc(wiz.customMacroTargets.carbsGrams)}"></div>
            <div class="form-field"><label for="wizFatInp">Fat (g)</label><input id="wizFatInp" type="number" class="field-input num" value="${esc(wiz.customMacroTargets.fatGrams)}"></div>
          </div>

          <label class="list-row" style="cursor:pointer;">
            <span class="list-row-main">
              <strong class="list-row-title">Optional Companion Suite Folder Alignment</strong>
              <span class="list-row-sub" style="display:block;">Greenroom works completely standalone. Enable this only if you also use Mother or companion suite apps and want shared library folders aligned automatically.</span>
            </span>
            <input type="checkbox" id="wizSuiteAlignCheck" ${wiz.shouldAutoAlignSisterApps ? "checked" : ""}>
          </label>

          <div class="wizard-footer">
            <button type="button" class="ghost-btn" id="wizBackBtn">← Back to Modules</button>
            <button type="submit" class="primary-btn" id="wizCompleteBtn">Complete Setup &amp; Open Greenroom</button>
          </div>
        </form>`;

      body.querySelector("#wizThemeSelect")?.addEventListener("change", (e) => {
        wiz.themeMode = applyThemeMode(e.target.value);
      });
      body.querySelector("#wizDensitySelect")?.addEventListener("change", (e) => {
        wiz.density = applyDensityAttr(e.target.value);
      });
      body.querySelector("#wizPresetSelect")?.addEventListener("change", (e) => {
        wiz.activeMacroPresetId = e.target.value;
        const box = body.querySelector("#wizCustomMacroBox");
        if (box) box.hidden = wiz.activeMacroPresetId !== "custom";
      });
      body.querySelector("#wizSuiteAlignCheck")?.addEventListener("change", (e) => {
        wiz.shouldAutoAlignSisterApps = Boolean(e.target.checked);
      });
      body.querySelector("#wizBackBtn")?.addEventListener("click", () => {
        wiz.step = 1;
        renderStep();
      });
      body.querySelector("#wizFinishForm")?.addEventListener("submit", async (e) => {
        e.preventDefault();
        if (wiz.activeMacroPresetId === "custom") {
          wiz.customMacroTargets = {
            caloriesKcal: Number(body.querySelector("#wizCalInp")?.value || 2000),
            proteinGrams: Number(body.querySelector("#wizProInp")?.value || 150),
            carbsGrams: Number(body.querySelector("#wizCarbInp")?.value || 200),
            fatGrams: Number(body.querySelector("#wizFatInp")?.value || 65),
          };
        }
        const payload = {
          enabledDomains: wiz.enabledDomains,
          activeMacroPresetId: wiz.activeMacroPresetId,
          customMacroTargets: wiz.customMacroTargets,
          themeMode: wiz.themeMode,
          density: wiz.density,
          shouldAutoAlignSisterApps: wiz.shouldAutoAlignSisterApps,
        };
        let saved;
        try {
          saved = await apiFetch("/api/config/onboarding", { method: "POST", body: payload });
        } catch {
          saved = await apiFetch("/api/config/settings", { method: "PUT", body: { ...payload, isOnboardingCompleted: true } });
        }
        state.settings.configSettings = saved;
        applyThemeMode(saved.themeMode || wiz.themeMode);
        applyDensityAttr(saved.density || wiz.density);
        applyEnabledDomainsToNav(saved.enabledDomains || wiz.enabledDomains);
        state.dashboard = null;
        if (dlg.open) dlg.close();
        showToast("Greenroom setup complete");
        if (onComplete) onComplete(saved);
      });
    }
  };

  renderStep();
  if (!dlg.open) dlg.showModal();
}
