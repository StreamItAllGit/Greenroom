/* @module static/js/comparator.js
 * @purpose Side-by-Side Dataset Comparator (Set A vs Set B + Multi-Copy Mismatch Bucket) and Redump/No-Intro DAT Manager.
 * @inputs state.catalog.compare, state.catalog.datsSummary, and /api/catalog/compare & /api/dats/* endpoints.
 * @outputs Rendered Comparator (4 KPI buckets + backup toggle) and DAT Manager views with hash lookup. */

import { state, esc, fmtMoney, fmtNum, tableLabelStyle, refreshCurrentView } from "./state.js";
import { apiFetch } from "./api.js";
import { runGreenroomTask } from "./tasks.js";
import { openGameEditor, openWishlistEditor } from "./modals.js";
import { renderCopyMismatchBucketRows, bindCopyReconcileControls, openImportPriceChartingOwnedModal, openImportDatModal } from "./comparator_copies.js";
import { refreshTooltips } from "./tooltips.js";

export { openImportPriceChartingOwnedModal, openImportDatModal };

export const COMMON_CONSOLES = [
  "Nintendo Switch", "SNES", "NES", "Nintendo 64", "GameCube", "Wii", "Wii U",
  "Game Boy", "Game Boy Color", "Game Boy Advance", "Nintendo DS", "Nintendo 3DS",
  "PlayStation", "PlayStation 2", "PlayStation 3", "PlayStation 4", "PlayStation 5", "PSP", "PS Vita",
  "Sega Genesis", "Sega CD", "Sega Saturn", "Sega Dreamcast",
  "Xbox", "Xbox 360", "Xbox One", "Xbox Series X", "PC", "ALL",
];

export function renderDatasetComparator(root, subtabsHtml, bindSubtabsFn) {
  const cmp = state.catalog.compare;
  const data = cmp.data || {
    left_set: { id: cmp.left_set, label: cmp.left_set },
    right_set: { id: cmp.right_set, label: cmp.right_set },
    summary: { left_count: 0, right_count: 0, in_both_count: 0, only_left_count: 0, only_right_count: 0, discrepancy_count: 0, copy_mismatch_count: 0, coverage_of_right_pct: 0 },
    in_both: [], only_left: [], only_right: [], copy_mismatches: [], available_datasets: [],
  };

  const datasets = data.available_datasets?.length
    ? data.available_datasets
    : [
        { id: "infinitebacklog", label: "Infinite Backlog (Tracked Set)", category: "Personal Owned Sets" },
        { id: "pricecharting_owned", label: "PriceCharting — Owned Collection", category: "Personal Owned Sets" },
        { id: "owned_all", label: "Greenroom Library — All Owned Games", category: "Personal Owned Sets" },
        { id: "owned_physical", label: "Greenroom Library — Physical Copies Only", category: "Personal Owned Sets" },
        { id: "local_backups", label: "Local ROMs & Disc Rips", category: "Personal Owned Sets" },
        { id: "steam", label: "Steam Digital Library", category: "Personal Owned Sets" },
        { id: "stream_completed", label: "Completed Games Set", category: "Personal Owned Sets" },
        { id: "wishlist", label: "Wishlist Items", category: "Personal Owned Sets" },
        { id: "pricecharting_guide", label: "PriceCharting — Reference Price Guide", category: "Master Reference Databases" },
        { id: "dat_catalog", label: "No-Intro / Redump — Known-Good DATs", category: "Master Reference Databases" },
      ];

  const buildSetOptions = (selectedId) => {
    const groups = {};
    datasets.forEach((d) => { (groups[d.category] = groups[d.category] || []).push(d); });
    return Object.entries(groups)
      .map(([cat, items]) => `<optgroup label="${esc(cat)}">${items.map((it) => `<option value="${esc(it.id)}" ${it.id === selectedId ? "selected" : ""}>${esc(it.label)}</option>`).join("")}</optgroup>`)
      .join("");
  };

  const s = data.summary || {};
  const copyMismatches = data.copy_mismatches || [];
  const copyMismatchCount = s.copy_mismatch_count ?? copyMismatches.length;
  const activeBucket = cmp.bucket || "only_right";
  const bucketRows = activeBucket === "only_left" ? data.only_left || [] : activeBucket === "in_both" ? data.in_both || [] : activeBucket === "copy_mismatches" ? copyMismatches : data.only_right || [];

  let headers = [], tbodyRows = "";
  if (activeBucket === "copy_mismatches") {
    headers = ["Title", "Console", "Service Copy Counts", "Local Backups", "Market Value", "Reconcile"];
    tbodyRows = renderCopyMismatchBucketRows(bucketRows);
  } else if (activeBucket === "in_both") {
    headers = ["Title", "Console", "Set A Condition/Copies", "Set B Condition/Copies", "Market Value", "DAT Hash"];
    tbodyRows = bucketRows.map((r) => `<tr><th scope="row">${esc(r.title)} ${r.has_discrepancy ? `<span class="badge badge-status badge-yellow">CONDITION DIFF</span>` : ""} ${r.copy_mismatch ? `<span class="badge badge-status badge-red">COPY MISMATCH</span>` : ""}</th><td>${esc(r.platform)}</td><td>${esc(r.left_condition || r.left_status || "Owned")} ${r.left_copies > 1 ? `<span class="badge badge-blue num">×${r.left_copies}</span>` : ""}</td><td>${esc(r.right_condition || r.right_status || "Matched")} ${r.right_copies > 1 ? `<span class="badge badge-blue num">×${r.right_copies}</span>` : ""}</td><td class="num">${r.loose_price ? `Loose ${fmtMoney(r.loose_price)}` : "—"} ${r.cib_price ? `· CIB ${fmtMoney(r.cib_price)}` : ""}</td><td class="mono">${esc(r.crc32 || (r.sha1 ? r.sha1.slice(0, 10) : "—"))}</td></tr>`).join("");
  } else {
    headers = ["Title", "Console", "Condition / Format", "Market Price", "DAT / Checksum", "Quick Actions"];
    tbodyRows = bucketRows.map((r, idx) => `<tr><th scope="row">${esc(r.title)} ${(r.copies_count || 1) > 1 ? `<span class="badge badge-blue num">×${r.copies_count}</span>` : ""}</th><td>${esc(r.platform)}</td><td>${esc(r.condition || r.format || "—")}</td><td class="num">${r.loose_price ? `Loose ${fmtMoney(r.loose_price)}` : "—"} ${r.cib_price ? `· CIB ${fmtMoney(r.cib_price)}` : ""}</td><td class="mono">${esc(r.crc32 || (r.sha1 ? r.sha1.slice(0, 10) : "—"))}</td><td class="no-row-open"><div class="toolbar" style="gap:4px;"><button type="button" class="ghost-btn" data-cmp-add-wish="${idx}">+ Wishlist</button><button type="button" class="ghost-btn" data-cmp-add-lib="${idx}">+ Library</button></div></td></tr>`).join("");
  }

  const captionLabel = activeBucket === "only_left" ? `Only in ${esc(data.left_set?.label || cmp.left_set)} (${fmtNum(s.only_left_count)})` : activeBucket === "in_both" ? `In Both Sets (${fmtNum(s.in_both_count)})` : activeBucket === "copy_mismatches" ? `Copy Count Mismatches Across Services (${fmtNum(copyMismatchCount)})` : `Only in ${esc(data.right_set?.label || cmp.right_set)} (${fmtNum(s.only_right_count)})`;

  root.innerHTML = `
    <div class="toolbar" style="justify-content:space-between;">
      <div class="subtabs">${subtabsHtml}</div>
      <div class="toolbar">
        <button type="button" class="ghost-btn" id="cmpImportPcBtn">📥 Import PriceCharting Owned CSV</button>
        <button type="button" class="ghost-btn" id="cmpSwapBtn">⇄ Swap Sets</button>
      </div>
    </div>
    <section class="card">
      <div class="card-head">
        <div>
          <h2 class="card-title">Side-by-Side Dataset Comparator (Set A vs Set B)</h2>
          <p class="muted-hint">Compare collection sets, inspect copy discrepancies, and separate local ROM backups from physical counts.</p>
        </div>
        <label style="display:inline-flex;align-items:center;gap:6px;font-size:12px;cursor:pointer;">
          <input type="checkbox" id="cmpIncludeBackups" ${cmp.include_backups ? "checked" : ""}>
          Include local ROM/ISO backups in counts
        </label>
      </div>
      <form id="cmpForm" class="toolbar">
        <label class="muted-hint">Console:</label>
        <select id="cmpPlatform" class="field-select">${COMMON_CONSOLES.map((c) => `<option value="${esc(c)}" ${cmp.platform === c ? "selected" : ""}>${c === "ALL" ? "All Consoles" : esc(c)}</option>`).join("")}</select>
        <label class="muted-hint">Region:</label>
        <select id="cmpRegion" class="field-select"><option value="NTSC-U" ${cmp.region === "NTSC-U" ? "selected" : ""}>North America (NTSC-U)</option><option value="PAL" ${cmp.region === "PAL" ? "selected" : ""}>PAL / Europe</option><option value="NTSC-J" ${cmp.region === "NTSC-J" ? "selected" : ""}>Japan (NTSC-J)</option><option value="ALL" ${cmp.region === "ALL" ? "selected" : ""}>All Regions</option></select>
        <label class="muted-hint">Set A:</label>
        <select id="cmpLeft" class="field-select">${buildSetOptions(cmp.left_set)}</select>
        <label class="muted-hint">vs Set B:</label>
        <select id="cmpRight" class="field-select">${buildSetOptions(cmp.right_set)}</select>
        <input type="search" id="cmpSearch" class="field-input grow" placeholder="Filter compared titles..." value="${esc(cmp.q || "")}">
        <button type="submit" class="primary-btn">Compare Sets</button>
      </form>
    </section>
    <section class="kpi-grid">
      <button type="button" class="kpi-card ${activeBucket === "only_left" ? "active" : ""}" data-cmp-bucket="only_left"><div class="kpi-label">Only in Set A</div><div class="kpi-val">${fmtNum(s.only_left_count)}</div><div class="kpi-meta num">${fmtNum(s.left_count)} in Set A (${fmtMoney(s.left_market_value || 0)})</div></button>
      <button type="button" class="kpi-card ${activeBucket === "only_right" ? "active" : ""}" data-cmp-bucket="only_right"><div class="kpi-label">Only in Set B</div><div class="kpi-val">${fmtNum(s.only_right_count)}</div><div class="kpi-meta num">${fmtNum(s.right_count)} in Set B · Missing: ${fmtMoney(s.only_right_market_value || 0)}</div></button>
      <button type="button" class="kpi-card ${activeBucket === "in_both" ? "active" : ""}" data-cmp-bucket="in_both"><div class="kpi-label">In Both Sets</div><div class="kpi-val">${fmtNum(s.in_both_count)}</div><div class="kpi-meta num">${s.coverage_of_right_pct || 0}% coverage · ${fmtNum(s.discrepancy_count)} diffs</div></button>
      <button type="button" class="kpi-card ${activeBucket === "copy_mismatches" ? "active" : ""}" data-cmp-bucket="copy_mismatches"><div class="kpi-label">Copy Count Mismatches</div><div class="kpi-val">${fmtNum(copyMismatchCount)}</div><div class="kpi-meta num">Copy count differences across tracked services</div></button>
    </section>
    <div tabindex="0" role="region" aria-label="Dataset Comparison Results" class="table-wrapper contain-panel">
      <table class="data-table" style="${esc(tableLabelStyle(headers))}">
        <caption>${captionLabel}</caption>
        <thead><tr>${headers.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead>
        <tbody>${tbodyRows || `<tr><td colspan="${headers.length}" class="muted-hint">No titles in this comparison bucket.</td></tr>`}</tbody>
      </table>
    </div>`;

  if (bindSubtabsFn) bindSubtabsFn(root);
  bindCopyReconcileControls(root, bucketRows);
  refreshTooltips(root);
  const runCompare = () => {
    cmp.platform = root.querySelector("#cmpPlatform").value; cmp.region = root.querySelector("#cmpRegion").value;
    cmp.left_set = root.querySelector("#cmpLeft").value; cmp.right_set = root.querySelector("#cmpRight").value;
    cmp.q = root.querySelector("#cmpSearch").value.trim(); cmp.include_backups = Boolean(root.querySelector("#cmpIncludeBackups")?.checked);
    refreshCurrentView();
  };
  root.querySelector("#cmpForm")?.addEventListener("submit", (e) => { e.preventDefault(); runCompare(); });
  ["#cmpPlatform", "#cmpRegion", "#cmpLeft", "#cmpRight", "#cmpIncludeBackups"].forEach((sel) => root.querySelector(sel)?.addEventListener("change", runCompare));
  root.querySelector("#cmpSwapBtn")?.addEventListener("click", () => { const tmp = cmp.left_set; cmp.left_set = cmp.right_set; cmp.right_set = tmp; refreshCurrentView(); });
  root.querySelector("#cmpImportPcBtn")?.addEventListener("click", () => openImportPriceChartingOwnedModal());
  root.querySelectorAll("[data-cmp-bucket]").forEach((btn) => btn.addEventListener("click", () => { cmp.bucket = btn.dataset.cmpBucket; renderDatasetComparator(root, subtabsHtml, bindSubtabsFn); }));
  root.querySelectorAll("[data-cmp-add-wish]").forEach((btn) => btn.addEventListener("click", () => { const it = bucketRows[Number(btn.dataset.cmpAddWish)]; if (it) openWishlistEditor({ title: it.title, platform: it.canonical_platform || it.platform, target_format: "CIB", current_price: it.cib_price || it.loose_price || "" }); }));
  root.querySelectorAll("[data-cmp-add-lib]").forEach((btn) => btn.addEventListener("click", () => { const it = bucketRows[Number(btn.dataset.cmpAddLib)]; if (it) openGameEditor({ title: it.title, platform: it.canonical_platform || it.platform, owned_format: "physical", play_status: "unplayed", crc32: it.crc32 || "", sha1: it.sha1 || "" }); }));
}

export function renderDatsManager(root, subtabsHtml, bindSubtabsFn) {
  const ds = state.catalog.datsSummary || { total_entries: 0, verified_games: 0, systems: [] };
  const sysHeaders = ["Source", "Platform", "DAT Header Name", "Version", "Known-Good ROMs", "Imported At"];
  const sysRows = (ds.systems || []).map((s) => `<tr><th scope="row"><span class="badge badge-status ${s.dat_source === "redump" ? "badge-blue" : "badge-green"}">${esc(s.dat_source)}</span></th><td><strong>${esc(s.platform)}</strong></td><td>${esc(s.dat_name)}</td><td class="mono">${esc(s.dat_version || "—")}</td><td class="num">${fmtNum(s.rom_count)}</td><td class="mono">${esc((s.imported_at || "").slice(0, 19).replace("T", " "))}</td></tr>`).join("");
  root.innerHTML = `
    <div class="toolbar" style="justify-content:space-between;">
      <div class="subtabs">${subtabsHtml}</div>
      <div class="toolbar">
        <button type="button" class="primary-btn" id="openImportDatModalBtn">+ Import DAT / XML File</button>
        <button type="button" class="ghost-btn" data-run-dat-task="import_dats">Scan drops/dats/</button>
        <button type="button" class="ghost-btn" data-run-dat-task="verify_hashes">⚡ Verify ROM Hashes</button>
      </div>
    </div>
    <section class="kpi-grid">
      <div class="kpi-card"><div class="kpi-label">Known-Good DAT Hashes</div><div class="kpi-val">${fmtNum(ds.total_entries)}</div><div class="kpi-meta">No-Intro &amp; Redump reference entries</div></div>
      <div class="kpi-card"><div class="kpi-label">DAT-Verified Games</div><div class="kpi-val">${fmtNum(ds.verified_games)}</div><div class="kpi-meta">Matched by CRC32 / MD5 / SHA-1 &amp; sidecars</div></div>
      <div class="kpi-card"><div class="kpi-label">Systems Loaded</div><div class="kpi-val">${fmtNum((ds.systems || []).length)}</div><div class="kpi-meta">Place .dat or .xml files in drops/dats/</div></div>
    </section>
    <section class="card">
      <h2 class="card-title">Instant Checksum Lookup (CRC32 / MD5 / SHA-1)</h2>
      <form id="datLookupForm" class="toolbar">
        <input type="text" id="lookupCrc" class="field-input mono" placeholder="CRC32 (8-hex, e.g. B19ED489)" style="width:210px;">
        <input type="text" id="lookupSha1" class="field-input mono grow" placeholder="SHA-1 (40-hex) or MD5 (32-hex)...">
        <button type="submit" class="primary-btn">Lookup Hash</button>
      </form>
      <div id="datLookupResult" class="muted-hint">Enter a ROM or ISO checksum to verify against loaded No-Intro or Redump catalogs.</div>
    </section>
    <div tabindex="0" role="region" aria-label="Loaded No-Intro and Redump DATs" class="table-wrapper contain-panel">
      <table class="data-table" style="${esc(tableLabelStyle(sysHeaders))}">
        <caption>Loaded No-Intro &amp; Redump System Catalogs</caption>
        <thead><tr>${sysHeaders.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead>
        <tbody>${sysRows || `<tr><td colspan="6" class="muted-hint">No .dat or .xml catalogs imported yet.</td></tr>`}</tbody>
      </table>
    </div>`;
  if (bindSubtabsFn) bindSubtabsFn(root);
  refreshTooltips(root);
  root.querySelectorAll("[data-run-dat-task]").forEach((btn) => btn.addEventListener("click", () => runGreenroomTask(btn.dataset.runDatTask)));
  root.querySelector("#openImportDatModalBtn")?.addEventListener("click", () => openImportDatModal());
  root.querySelector("#datLookupForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const crc = root.querySelector("#lookupCrc").value.trim(), shaOrMd5 = root.querySelector("#lookupSha1").value.trim(), qs = new URLSearchParams();
    if (crc) qs.set("crc32", crc);
    if (shaOrMd5.length === 32) qs.set("md5", shaOrMd5); else if (shaOrMd5) qs.set("sha1", shaOrMd5);
    const res = await apiFetch(`/api/dats/lookup?${qs}`), out = root.querySelector("#datLookupResult");
    if (res.matched && res.entry) {
      const en = res.entry;
      out.innerHTML = `<span class="badge badge-status badge-green">VERIFIED ${esc(en.dat_source)}</span> <strong>${esc(en.rom_name)}</strong> (${esc(en.platform)}) · CRC32: <code class="mono">${esc(en.crc32 || "—")}</code> · SHA-1: <code class="mono">${esc(en.sha1 || "—")}</code>`;
    } else {
      out.innerHTML = `<span class="badge badge-status badge-red">NO MATCH</span> Checksum not found in currently loaded DAT catalogs.`;
    }
  });
}
