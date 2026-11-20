/* @module static/js/coexistence.js
 * @purpose Optional Companion Suite Coexistence UI, preset runner, and multi-part media normalize/merge actions.
 * @inputs /api/v1/coexistence/status, /api/v1/coexistence/apply-recommendations, and /api/v1/media/multipart/* endpoints.
 * @outputs Coexistence banner HTML, preset execution handlers, and multi-part media triggers. */

import { state, esc, showToast, refreshCurrentView } from "./state.js";
import { apiFetch, savePreferences } from "./api.js";
import { refreshTooltips } from "./tooltips.js";

export async function loadCoexistenceStatus() {
  try {
    const res = await apiFetch("/api/v1/coexistence/status");
    state.coexistence = {
      ...state.coexistence,
      ...res,
      detectedPeers: res.detectedPeers || res.detected_peers || [],
      sharedRoots: res.sharedRoots || res.shared_roots || [],
      activeLocks: res.activeLocks || res.active_locks || [],
      recommendations: res.recommendations || [],
      useSisterVerifiedHashes: Boolean(
        res.useSisterVerifiedHashes ?? res.use_sister_verified_hashes ?? state.coexistence.useSisterVerifiedHashes
      ),
      autoAlignWithSisterApps: Boolean(
        res.autoAlignWithSisterApps ?? res.auto_align_with_sister_apps ?? state.coexistence.autoAlignWithSisterApps
      ),
      leaveMultipartFilesSplit: Boolean(
        res.leaveMultipartFilesSplit ?? res.leave_multipart_files_split ?? state.coexistence.leaveMultipartFilesSplit
      ),
      autoMergeMultipartMedia: Boolean(
        res.autoMergeMultipartMedia ?? res.auto_merge_multipart_media ?? state.coexistence.autoMergeMultipartMedia
      ),
    };
  } catch (err) {
    console.debug("Coexistence status fallback:", err);
  }
  return state.coexistence;
}

export async function applyCoexistencePreset(presetId) {
  try {
    const res = await apiFetch("/api/v1/coexistence/apply-recommendations", {
      method: "POST",
      body: { presetId, preset_id: presetId },
    });
    const snapId = res.snapshotId || res.snapshot_id || "auto";
    showToast(`Applied suite preset (Snapshot: ${snapId})`);
    await loadCoexistenceStatus();
    await refreshCurrentView({ background: true });
    return res;
  } catch (err) {
    try {
      const snap = await apiFetch("/api/config/snapshots", {
        method: "POST",
        body: { label: `coexistence_${presetId}` },
      });
      const updates =
        presetId === "use_sister_hashes"
          ? { useSisterVerifiedHashes: true, use_sister_verified_hashes: true }
          : presetId === "align_redroom_roots"
          ? { autoAlignWithSisterApps: true, auto_align_with_sister_apps: true }
          : {
              useSisterVerifiedHashes: true,
              use_sister_verified_hashes: true,
              autoAlignWithSisterApps: true,
              auto_align_with_sister_apps: true,
            };
      state.coexistence = { ...state.coexistence, ...updates };
      await savePreferences(updates, "global");
      showToast(`Applied suite preset (Snapshot: ${snap.id || "saved"})`);
      await refreshCurrentView({ background: true });
    } catch (innerErr) {
      showToast(innerErr.message || err.message || "Failed to apply suite preset");
    }
  }
}

export async function normalizeMultipartMedia(targetPaths = []) {
  const dryRun = Boolean(state.tasks.dryRun);
  try {
    const res = await apiFetch("/api/v1/media/multipart/normalize", {
      method: "POST",
      body: { targetPaths, target_paths: targetPaths, dryRun, dry_run: dryRun },
    });
    const count = res.renamedCount ?? res.renamed_count ?? (res.normalizedFiles || []).length;
    showToast(`${dryRun ? "[Dry Run] " : ""}Normalized ${count} multi-part file(s)`);
    await refreshCurrentView({ background: true });
    return res;
  } catch (err) {
    showToast(err.message || "Multi-part filename normalization failed");
    return null;
  }
}

export async function mergeMultipartMedia(groupKey = "all", partPaths = []) {
  const dryRun = Boolean(state.tasks.dryRun);
  try {
    const res = await apiFetch("/api/v1/media/multipart/merge", {
      method: "POST",
      body: { groupKey, group_key: groupKey, partPaths, part_paths: partPaths, dryRun, dry_run: dryRun },
    });
    const mergedTarget = res.mergedPath || res.merged_path || groupKey;
    showToast(`${dryRun ? "[Dry Run] " : ""}Merged split parts into ${mergedTarget}`);
    await refreshCurrentView({ background: true });
    return res;
  } catch (err) {
    showToast(err.message || "Multi-part media merge failed");
    return null;
  }
}

export function renderCoexistenceBannerHtml() {
  const cx = state.coexistence || {};
  const peers = cx.detectedPeers || [];
  const locks = cx.activeLocks || [];
  const recs = cx.recommendations || [];
  const peerBadges = peers.length
    ? peers
        .map((p) => {
          const name = typeof p === "string" ? p : p.appId || p.name || "Suite App";
          const st = typeof p === "object" && p.online ? "badge-green" : "badge-blue";
          return `<span class="badge badge-status ${st}">⇄ ${esc(name)}</span>`;
        })
        .join(" ")
    : `<span class="badge badge-status badge-green">SIDECAR HASH CACHE READY</span>`;

  const lockAlert = locks.length
    ? `<div class="badge badge-status badge-yellow">YIELDING TO ${locks.length} ACTIVE LOCK(S)</div>`
    : "";

  const recHint = recs.length
    ? esc(typeof recs[0] === "string" ? recs[0] : recs[0].message || recs[0].title || "")
    : "Sidecar checksum reuse (.hash, .archive_hashes.json, collection manifests) and active lock deferral are active.";

  return `
    <section class="card coexistence-banner" id="coexistenceBannerCard" aria-label="Optional Suite Coexistence">
      <div class="card-head">
        <div>
          <h2 class="card-title">🤝 Optional Suite Coexistence &amp; Sidecar Cache</h2>
          <p class="card-subtitle">${recHint}</p>
        </div>
        <div class="toolbar">
          ${peerBadges}
          ${lockAlert}
        </div>
      </div>
      <div class="toolbar">
        <button type="button" class="chip-btn ${cx.useSisterVerifiedHashes !== false ? "active" : ""}" data-coexistence-preset="use_sister_hashes" data-tooltip="Reuse Sidecar Hashes|Skip redundant ROM and ISO hashing when verified sidecar files exist">
          ⚡ Reuse Verified Sidecar Hashes
        </button>
        <button type="button" class="chip-btn ${cx.autoAlignWithSisterApps ? "active" : ""}" data-coexistence-preset="align_redroom_roots" data-tooltip="Align Shared Media Roots|Synchronize shared media and ROM library folders with companion suite apps">
          📂 Align Shared Media Roots
        </button>
        <button type="button" class="primary-btn" data-coexistence-preset="apply_all" data-tooltip="Apply All Suite Presets|Save a config snapshot and enable shared sidecar and folder alignment rules">
          ✓ Apply All Suite Presets
        </button>
      </div>
    </section>
  `;
}

export function bindCoexistenceControls(root) {
  if (!root) return;
  root.querySelectorAll("[data-coexistence-preset]").forEach((btn) => {
    btn.addEventListener("click", () => {
      applyCoexistencePreset(btn.dataset.coexistencePreset);
    });
  });
  refreshTooltips(root);
}
