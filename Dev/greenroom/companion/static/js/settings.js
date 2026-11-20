/* @module static/js/settings.js
 * @purpose View 8: Settings workspace with 5-Pill Sub-Page Navigation (Modules & Profiles, Accounts, Doctor & Database Healer, Coexistence & Media Rules, Notifications & Snapshots).
 * @inputs state.settings, state.coexistence, and /api/config/{accounts,domains,profiles,settings,snapshots}, /api/system/*, /api/v1/coexistence/* endpoints.
 * @outputs Rendered 5-pill Settings workspace with accessible role="tablist", modules/profiles controls, DB Healer, and Quarantine/Snapshot tools. */

import { state, esc, fmtNum, showToast, downloadBlobFile, refreshCurrentView } from "./state.js";
import { apiFetch, savePreferences } from "./api.js";
import { bindCoexistenceControls, normalizeMultipartMedia, mergeMultipartMedia } from "./coexistence.js";
import { renderAccountsSettingsCardHtml, bindAccountsSettingsCard } from "./settings_accounts.js";
import { renderModulesSettingsHtml, bindModulesSettings } from "./settings_modules.js";
import { refreshTooltips } from "./tooltips.js";

const SETTINGS_PILLS = [
  ["modules", "🧩 Modules & Profiles"],
  ["accounts", "🔑 Accounts & Logins"],
  ["doctor", "🩺 Doctor & DB Healer"],
  ["coexistence", "🤝 Coexistence & Media Rules"],
  ["notifications", "🔔 Notifications & Snapshots"],
];

function activateSettingsPill(root, nextTab, focusTab = false) {
  const validTab = SETTINGS_PILLS.some(([id]) => id === nextTab) ? nextTab : "modules";
  state.settings.subtab = validTab;
  root.querySelectorAll("[data-settings-tab]").forEach((btn) => {
    const selected = btn.dataset.settingsTab === validTab;
    btn.setAttribute("aria-selected", selected ? "true" : "false");
    btn.tabIndex = selected ? 0 : -1;
    if (selected && focusTab) btn.focus();
  });
  root.querySelectorAll("[data-settings-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.settingsPanel !== validTab;
  });
}

export function renderSettingsView(root) {
  if (!root) return;
  const activeTab = SETTINGS_PILLS.some(([id]) => id === state.settings.subtab) ? state.settings.subtab : "modules";
  const doc = state.settings.doctor || {};
  const heal = state.settings.healReport;
  const cfgSet = state.settings.configSettings || {};
  const notif = state.settings.notifications || {};
  const snaps = state.settings.snapshots || [];
  const qItems = state.settings.quarantine || [];
  const cx = state.coexistence || {};
  const useGlobalFmt = Boolean(cfgSet.useGlobalFormatRules ?? state.prefs.global?.useGlobalFormatRules ?? true);
  const useSisterHashes = Boolean(cfgSet.useSisterVerifiedHashes ?? cx.useSisterVerifiedHashes ?? state.prefs.global?.useSisterVerifiedHashes ?? true);
  const autoAlignSister = Boolean(cfgSet.shouldAutoAlignSisterApps ?? cx.autoAlignWithSisterApps ?? state.prefs.global?.autoAlignWithSisterApps ?? false);
  const leaveSplit = Boolean(cfgSet.leaveMultipartFilesSplit ?? cx.leaveMultipartFilesSplit ?? state.prefs.global?.leaveMultipartFilesSplit ?? true);
  const autoMerge = Boolean(cfgSet.autoMergeMultipartMedia ?? cx.autoMergeMultipartMedia ?? state.prefs.global?.autoMergeMultipartMedia ?? false);

  const pillsHtml = SETTINGS_PILLS.map(
    ([id, label]) => `<button type="button" class="pill-tab" role="tab" data-settings-tab="${id}" aria-selected="${activeTab === id ? "true" : "false"}" tabindex="${activeTab === id ? "0" : "-1"}">${esc(label)}</button>`
  ).join("");

  const healSummaryHtml = heal
    ? `<div class="card" style="background:var(--bg-card-alt);">
        <div class="card-head">
          <strong>${heal.dry_run ? "🧪 Dry-Run Preview Report (Zero Rows Mutated)" : "✓ Database Heal Completed"}</strong>
          <span class="badge badge-status ${heal.total_mutations === 0 ? "badge-green" : "badge-blue"} num">${fmtNum(heal.total_mutations)} MUTATIONS</span>
        </div>
        <div class="toolbar">
          <span class="badge badge-neutral num">Integrity: ${esc(heal.integrity?.quick_check || "ok")}</span>
          <span class="badge badge-neutral num">False Playing Repaired: ${fmtNum(heal.status_repairs)}</span>
          <span class="badge badge-neutral num">Platforms Repaired: ${fmtNum(heal.platform_repairs)}</span>
          <span class="badge badge-neutral num">Duplicates Merged: ${fmtNum(heal.merged_duplicates)}</span>
          <span class="badge badge-neutral num">References Remapped: ${fmtNum(heal.fk_remapped)}</span>
          <span class="badge badge-neutral num">Copy Counts Synced: ${fmtNum(heal.copy_resyncs)}</span>
        </div>
        ${heal.backup_path ? `<div class="list-row-sub mono">Pre-heal .7z backup: ${esc(heal.backup_path)}</div>` : ""}
      </div>`
    : `<p class="muted-hint">Run <strong>Preview Dry Run</strong> to inspect repairs safely, or <strong>Heal Database Now</strong> to create a compressed backup and apply all 6 repair stages.</p>`;

  const snapRows = snaps.map((s) => `
    <div class="list-row">
      <div class="list-row-main">
        <div class="list-row-title mono">${esc(s.label || s.id)}</div>
        <div class="list-row-sub mono">${esc(s.id)} · ${esc((s.created_at || "").slice(0, 19).replace("T", " "))} · ${fmtNum(s.size_bytes)} B</div>
      </div>
      <button type="button" class="ghost-btn" data-restore-snap="${esc(s.id)}">↺ Restore Snapshot</button>
    </div>`).join("");

  const quarantineRows = qItems.map((q) => `
    <div class="list-row">
      <div class="list-row-main">
        <div class="list-row-title">[${esc(q.table_name)}] ${esc(q.summary || q.id)}</div>
        <div class="list-row-sub mono">Quarantined ${esc((q.deleted_at || "").slice(0, 19).replace("T", " "))} · Expires ${esc((q.expires_at || "").slice(0, 10))}</div>
      </div>
      <div class="toolbar">
        <button type="button" class="primary-btn" data-restore-quarantine="${esc(q.id)}">↺ Restore Row</button>
        <button type="button" class="danger-btn" data-purge-quarantine="${esc(q.id)}">Purge Row</button>
      </div>
    </div>`).join("");

  root.innerHTML = `
    <div class="pill-tablist" role="tablist" aria-label="Settings Sections">${pillsHtml}</div>

    <div role="tabpanel" data-settings-panel="modules" ${activeTab !== "modules" ? "hidden" : ""} class="stack-list">
      ${renderModulesSettingsHtml()}
    </div>

    <div role="tabpanel" data-settings-panel="accounts" ${activeTab !== "accounts" ? "hidden" : ""} class="stack-list">
      ${renderAccountsSettingsCardHtml()}
    </div>

    <div role="tabpanel" data-settings-panel="doctor" ${activeTab !== "doctor" ? "hidden" : ""} class="stack-list">
      <div class="dash-grid">
        <section class="card contain-panel">
          <div class="card-head">
            <div>
              <h2 class="card-title">🩺 System Doctor &amp; Integrity Check</h2>
              <p class="card-subtitle">Fast SQLite header health check by default, or on-demand full database scan</p>
            </div>
            <span class="badge badge-status ${doc.status === "healthy" ? "badge-green" : "badge-yellow"}">${esc((doc.status || "ready").toUpperCase())} (${esc((doc.integrity_check || "ok").toUpperCase())})</span>
          </div>
          <div class="kpi-grid" style="grid-template-columns:repeat(auto-fit,minmax(130px,1fr));">
            <div class="kpi-card"><span class="kpi-label">DB Size</span><div class="kpi-value">${esc(doc.db_size_mb ?? 0)} MB</div></div>
            <div class="kpi-card"><span class="kpi-label">Journal Mode</span><div class="kpi-value mono">${esc((doc.wal_mode || "wal").toUpperCase())}</div></div>
            <div class="kpi-card"><span class="kpi-label">Disk Free</span><div class="kpi-value">${esc(doc.disk_free_gb ?? "—")} GB</div></div>
            <div class="kpi-card"><span class="kpi-label">Backups</span><div class="kpi-value">${fmtNum((doc.recent_backups || []).length)}</div></div>
          </div>
          <div class="toolbar">
            <button type="button" class="ghost-btn" id="sysDeepCheckBtn" title="Deep SQLite Integrity Check|Run full integrity check across all database pages">🔍 Deep Integrity Check</button>
            <button type="button" class="primary-btn" id="sysBackupDbBtn">🛡 Backup DB (.7z)</button>
            <button type="button" class="ghost-btn" id="sysDiagBundleBtn">📦 Export Diagnostic Bundle</button>
          </div>
        </section>

        <section class="card contain-panel" id="settingsHealDbCard">
          <div class="card-head">
            <div>
              <h2 class="card-title">🩹 6-Stage Database Healer</h2>
              <p class="card-subtitle">Repairs stale statuses, recovers unknown platforms, merges duplicates, and syncs copy counts</p>
            </div>
            <span class="badge badge-status badge-green">PRE-HEAL BACKUP</span>
          </div>
          ${healSummaryHtml}
          <div class="toolbar">
            <button type="button" class="ghost-btn" id="healDbDryRunBtn" title="Preview Heal Dry Run|Run all 6 repair stages inside a rolled-back preview">🧪 Preview Dry Run</button>
            <button type="button" class="primary-btn" id="healDbExecuteBtn" title="Heal Database Now|Create a compressed .7z backup and execute all 6 repair stages">🩹 Heal Database Now</button>
          </div>
        </section>
      </div>

      <section class="card contain-panel">
        <div class="card-head">
          <div>
            <h2 class="card-title">🛡 Soft-Delete Quarantine (${qItems.length})</h2>
            <p class="card-subtitle">Deleted rows are held safely here for 30 days (up to 50 items) before permanent purge</p>
          </div>
        </div>
        <div class="stack-list">${quarantineRows || `<p class="muted-hint">Quarantine bin is clean.</p>`}</div>
      </section>
    </div>

    <div role="tabpanel" data-settings-panel="coexistence" ${activeTab !== "coexistence" ? "hidden" : ""} class="stack-list">
      <div class="dash-grid">
        <section class="card contain-panel" id="settingsIntegrationsCard">
          <div class="card-head">
            <div>
              <h2 class="card-title">🤝 Global Format Rules &amp; Companion Suite Coexistence</h2>
              <p class="card-subtitle">Shared naming rules, sidecar checksum reuse, and active folder lock deferral</p>
            </div>
            <span class="badge badge-status badge-green">OPTIONAL SUITE</span>
          </div>
          <div class="stack-list">
            <label class="list-row" style="cursor:pointer;">
              <span class="list-row-main">
                <strong class="list-row-title">Use global suite format and naming rules</strong>
                <span class="list-row-sub" style="display:block;">Persist shared naming, platform normalization, and format rules in your settings file.</span>
              </span>
              <input type="checkbox" id="toggleUseGlobalFormatRules" name="useGlobalFormatRules" ${useGlobalFmt ? "checked" : ""}>
            </label>
            <label class="list-row" style="cursor:pointer;">
              <span class="list-row-main">
                <strong class="list-row-title">Reuse verified sidecar and manifest checksums</strong>
                <span class="list-row-sub" style="display:block;">Skip redundant CRC32, MD5, and SHA-1 hashing when valid <code>.hash</code> sidecars or manifests exist.</span>
              </span>
              <input type="checkbox" id="toggleUseSisterHashes" name="useSisterVerifiedHashes" ${useSisterHashes ? "checked" : ""}>
            </label>
            <label class="list-row" style="cursor:pointer;">
              <span class="list-row-main">
                <strong class="list-row-title">Auto-align shared folders and defer active locks</strong>
                <span class="list-row-sub" style="display:block;">Synchronize shared library folders and pause updates when companion suite apps hold an active lock.</span>
              </span>
              <input type="checkbox" id="toggleAutoAlignSisterApps" name="autoAlignWithSisterApps" ${autoAlignSister ? "checked" : ""}>
            </label>
          </div>
          <div class="toolbar">
            <button type="button" class="chip-btn ${useSisterHashes ? "active" : ""}" data-coexistence-preset="use_sister_hashes">⚡ Reuse Verified Sidecar Hashes</button>
            <button type="button" class="chip-btn ${autoAlignSister ? "active" : ""}" data-coexistence-preset="align_redroom_roots">📂 Align Shared Media Roots</button>
            <button type="button" class="primary-btn" data-coexistence-preset="apply_all">✓ Apply All Suite Presets</button>
          </div>
        </section>

        <section class="card contain-panel" id="settingsMediaGamesCard">
          <div class="card-head">
            <div>
              <h2 class="card-title">🎬 Multi-Part Media Naming &amp; Auto-Merge</h2>
              <p class="card-subtitle">Keep split media parts formatted cleanly before the extension or consolidate split parts losslessly</p>
            </div>
            <span class="badge badge-status badge-blue">MULTI-PART</span>
          </div>
          <div class="stack-list">
            <label class="list-row" style="cursor:pointer;">
              <span class="list-row-main">
                <strong class="list-row-title">Keep multi-part media files split</strong>
                <span class="list-row-sub" style="display:block;">Keep multi-part episodes or movies split while enforcing terminal part naming (<code>pt1</code>, <code>pt2</code>).</span>
              </span>
              <input type="checkbox" id="toggleLeaveMultipartSplit" name="leaveMultipartFilesSplit" ${leaveSplit ? "checked" : ""}>
            </label>
            <label class="list-row" style="cursor:pointer;">
              <span class="list-row-main">
                <strong class="list-row-title">Auto-merge split multi-part media files</strong>
                <span class="list-row-sub" style="display:block;">Sequentially append split parts into one consolidated file, shift subtitles, and refresh sidecar checksums.</span>
              </span>
              <input type="checkbox" id="toggleAutoMergeMultipart" name="autoMergeMultipartMedia" ${autoMerge ? "checked" : ""}>
            </label>
          </div>
          <div class="toolbar">
            <button type="button" class="ghost-btn" id="settingsNormalizeMultipartBtn">🏷 Normalize Split Filenames</button>
            <button type="button" class="primary-btn" id="settingsMergeMultipartBtn">🔗 Merge Split Parts</button>
          </div>
        </section>
      </div>
    </div>

    <div role="tabpanel" data-settings-panel="notifications" ${activeTab !== "notifications" ? "hidden" : ""} class="stack-list">
      <section class="card contain-panel">
        <div class="card-head">
          <div>
            <h2 class="card-title">🔔 Desktop Toast &amp; Discord Webhook</h2>
            <p class="card-subtitle">Send task completion and failure alerts to Windows notifications or Discord</p>
          </div>
        </div>
        <form id="notifSettingsForm" class="stack-list">
          <label style="display:flex;align-items:center;gap:8px;font-size:13px;">
            <input type="checkbox" name="toast_enabled" ${notif.toast_enabled !== false ? "checked" : ""}>
            Enable Windows desktop toast notifications
          </label>
          <div class="form-field">
            <label>Discord Webhook URL (Optional)</label>
            <input type="url" name="discord_webhook_url" class="field-input mono" placeholder="https://discord.com/api/webhooks/..." value="${esc(notif.discord_webhook_url || "")}">
          </div>
          <div class="toolbar" style="justify-content:space-between;">
            <button type="button" class="ghost-btn" id="sendTestNotifBtn">🔔 Send Test Notification</button>
            <button type="submit" class="primary-btn">Save Notification Settings</button>
          </div>
        </form>
      </section>

      <section class="card contain-panel">
        <div class="card-head">
          <div>
            <h2 class="card-title">💾 Config Rollback Snapshots (${snaps.length} / 20)</h2>
            <p class="card-subtitle">Automatic ring buffer of schedule and preference snapshots with one-click restore and JSON export</p>
          </div>
          <div class="toolbar">
            <button type="button" class="primary-btn" id="createConfigSnapBtn">💾 Save Snapshot</button>
            <button type="button" class="ghost-btn" id="exportConfigJsonBtn">📥 Export JSON</button>
            <button type="button" class="ghost-btn" id="importConfigJsonBtn">📤 Import JSON</button>
          </div>
        </div>
        <div class="stack-list">${snapRows || `<p class="muted-hint">No config snapshots recorded yet.</p>`}</div>
      </section>
    </div>`;

  bindModulesSettings(root);
  bindAccountsSettingsCard(root);
  bindCoexistenceControls(root);
  refreshTooltips(root);

  const tablist = root.querySelector('[role="tablist"]');
  root.querySelectorAll("[data-settings-tab]").forEach((btn) => {
    btn.addEventListener("click", () => activateSettingsPill(root, btn.dataset.settingsTab, false));
  });
  tablist?.addEventListener("keydown", (e) => {
    if (!["ArrowRight", "ArrowLeft", "Home", "End"].includes(e.key)) return;
    e.preventDefault();
    const ids = SETTINGS_PILLS.map(([id]) => id);
    const curIdx = Math.max(0, ids.indexOf(state.settings.subtab));
    const nextIdx = e.key === "Home" ? 0 : e.key === "End" ? ids.length - 1 : (curIdx + (e.key === "ArrowRight" ? 1 : ids.length - 1)) % ids.length;
    activateSettingsPill(root, ids[nextIdx], true);
  });

  root.querySelector("#sysDeepCheckBtn")?.addEventListener("click", async () => {
    showToast("Running full SQLite integrity check...");
    state.settings.doctor = await apiFetch("/api/system/doctor?full=1");
    showToast(`Integrity check: ${state.settings.doctor.integrity_check || "ok"}`);
    renderSettingsView(root);
  });

  const execHeal = async (dryRun) => {
    showToast(dryRun ? "Running 6-stage DB Healer dry-run preview..." : "Creating .7z backup and healing database...");
    const report = await apiFetch("/api/system/heal-db", { method: "POST", body: { dry_run: dryRun, create_backup: !dryRun } });
    state.settings.healReport = report;
    state.catalog.cacheByKey = {};
    showToast(`${dryRun ? "[Dry Run] " : ""}Healer finished: ${report.total_mutations || 0} mutations`);
    renderSettingsView(root);
  };
  root.querySelector("#healDbDryRunBtn")?.addEventListener("click", () => execHeal(true));
  root.querySelector("#healDbExecuteBtn")?.addEventListener("click", () => execHeal(false));

  root.querySelector("#toggleUseGlobalFormatRules")?.addEventListener("change", async (e) => {
    const val = Boolean(e.target.checked);
    state.settings.configSettings = await apiFetch("/api/config/settings", { method: "PUT", body: { useGlobalFormatRules: val } });
    showToast(`Global suite format rules ${val ? "enabled" : "disabled"}`);
  });

  root.querySelector("#toggleUseSisterHashes")?.addEventListener("change", async (e) => {
    const val = Boolean(e.target.checked);
    state.coexistence.useSisterVerifiedHashes = val;
    await apiFetch("/api/config/settings", { method: "PUT", body: { useSisterVerifiedHashes: val } }).catch(() => {});
    await savePreferences({ useSisterVerifiedHashes: val, use_sister_verified_hashes: val }, "global");
    showToast(`Sidecar checksum reuse ${val ? "enabled" : "disabled"}`);
  });
  root.querySelector("#toggleAutoAlignSisterApps")?.addEventListener("change", async (e) => {
    const val = Boolean(e.target.checked);
    state.coexistence.autoAlignWithSisterApps = val;
    await apiFetch("/api/config/settings", { method: "PUT", body: { shouldAutoAlignSisterApps: val } }).catch(() => {});
    await savePreferences({ autoAlignWithSisterApps: val, auto_align_with_sister_apps: val }, "global");
    showToast(`Companion suite auto-align ${val ? "enabled" : "disabled"}`);
  });
  root.querySelector("#toggleLeaveMultipartSplit")?.addEventListener("change", async (e) => {
    const leave = Boolean(e.target.checked), merge = !leave;
    state.coexistence.leaveMultipartFilesSplit = leave; state.coexistence.autoMergeMultipartMedia = merge;
    const mergeBox = root.querySelector("#toggleAutoMergeMultipart"); if (mergeBox) mergeBox.checked = merge;
    await apiFetch("/api/config/settings", { method: "PUT", body: { leaveMultipartFilesSplit: leave, autoMergeMultipartMedia: merge } }).catch(() => {});
    await savePreferences({ leaveMultipartFilesSplit: leave, leave_multipart_files_split: leave, autoMergeMultipartMedia: merge, auto_merge_multipart_media: merge }, "global");
    showToast(leave ? "Multi-part files will stay split" : "Auto-merge enabled for multi-part media");
  });
  root.querySelector("#toggleAutoMergeMultipart")?.addEventListener("change", async (e) => {
    const merge = Boolean(e.target.checked), leave = !merge;
    state.coexistence.autoMergeMultipartMedia = merge; state.coexistence.leaveMultipartFilesSplit = leave;
    const leaveBox = root.querySelector("#toggleLeaveMultipartSplit"); if (leaveBox) leaveBox.checked = leave;
    await apiFetch("/api/config/settings", { method: "PUT", body: { leaveMultipartFilesSplit: leave, autoMergeMultipartMedia: merge } }).catch(() => {});
    await savePreferences({ leaveMultipartFilesSplit: leave, leave_multipart_files_split: leave, autoMergeMultipartMedia: merge, auto_merge_multipart_media: merge }, "global");
    showToast(merge ? "Auto-merge enabled for multi-part media" : "Multi-part files will stay split");
  });
  root.querySelector("#settingsNormalizeMultipartBtn")?.addEventListener("click", () => normalizeMultipartMedia([]));
  root.querySelector("#settingsMergeMultipartBtn")?.addEventListener("click", () => mergeMultipartMedia("all", []));

  root.querySelector("#sysBackupDbBtn")?.addEventListener("click", async () => {
    const res = await apiFetch("/api/system/backup-db", { method: "POST" });
    showToast(`Created compressed backup: ${res.backup_file}`);
    refreshCurrentView({ background: true });
  });
  root.querySelector("#sysDiagBundleBtn")?.addEventListener("click", async () => {
    const res = await apiFetch("/api/system/diagnostic-bundle", { method: "POST" });
    showToast(`Diagnostic bundle saved: ${res.bundle_path}`);
  });
  root.querySelector("#notifSettingsForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const res = await apiFetch("/api/notifications/settings", { method: "PUT", body: { toast_enabled: fd.get("toast_enabled") === "on", discord_webhook_url: String(fd.get("discord_webhook_url") || "").trim() } });
    state.settings.notifications = res.settings;
    showToast("Saved notification settings");
  });
  root.querySelector("#sendTestNotifBtn")?.addEventListener("click", async () => {
    await apiFetch("/api/notifications/test", { method: "POST" });
    showToast("Dispatched test notification");
  });
  root.querySelector("#createConfigSnapBtn")?.addEventListener("click", async () => {
    const snap = await apiFetch("/api/config/snapshots", { method: "POST", body: { label: "settings_manual" } });
    showToast(`Saved config snapshot ${snap.id}`);
    refreshCurrentView({ background: true });
  });
  root.querySelector("#exportConfigJsonBtn")?.addEventListener("click", async () => {
    const bundle = await apiFetch("/api/config/export");
    downloadBlobFile(`greenroom_config_${new Date().toISOString().slice(0, 10)}.json`, JSON.stringify(bundle, null, 2), "application/json;charset=utf-8");
    showToast("Exported Greenroom config bundle");
  });
  root.querySelector("#importConfigJsonBtn")?.addEventListener("click", () => {
    const inp = document.createElement("input"); inp.type = "file"; inp.accept = ".json,application/json";
    inp.addEventListener("change", async () => {
      const file = inp.files?.[0]; if (!file) return;
      await apiFetch("/api/config/import", { method: "POST", body: JSON.parse(await file.text()) });
      showToast("Imported configuration & created pre-import snapshot");
      refreshCurrentView();
    });
    inp.click();
  });
  root.querySelectorAll("[data-restore-snap]").forEach((btn) => btn.addEventListener("click", async () => {
    await apiFetch(`/api/config/snapshots/${encodeURIComponent(btn.dataset.restoreSnap)}/restore`, { method: "POST" });
    showToast(`Restored snapshot ${btn.dataset.restoreSnap}`); refreshCurrentView();
  }));
  root.querySelectorAll("[data-restore-quarantine]").forEach((btn) => btn.addEventListener("click", async () => {
    const res = await apiFetch(`/api/quarantine/${encodeURIComponent(btn.dataset.restoreQuarantine)}/restore`, { method: "POST" });
    showToast(`Restored row into ${res.restored_table}`); refreshCurrentView();
  }));
  root.querySelectorAll("[data-purge-quarantine]").forEach((btn) => btn.addEventListener("click", async () => {
    await apiFetch(`/api/quarantine/${encodeURIComponent(btn.dataset.purgeQuarantine)}`, { method: "DELETE" });
    showToast("Permanently purged item from quarantine"); refreshCurrentView();
  }));
}
