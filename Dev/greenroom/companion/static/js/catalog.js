/* @module static/js/catalog.js
 * @purpose View 2: Catalog & Deal Scout workspace with personal vs critic ratings and multi-part media controls.
 * @inputs state.catalog, /api/catalog/*, /api/scout, and /api/v1/media/multipart/* endpoints.
 * @outputs Rendered Catalog tables, Deal Scout workspace, saved filter presets, and domain-disabled guard. */

import { state, esc, fmtMoney, fmtNum, showToast, yieldToMain, tableLabelStyle, catalogCacheKey, exportRowsAsCsv, exportRowsAsJson, switchView, refreshCurrentView } from "./state.js";
import { apiFetch, savePreferences } from "./api.js";
import { renderRetailerPills, openGameEditor, openWishlistEditor, openGenericRowEditor } from "./modals.js";
import { renderDatasetComparator, renderDatsManager, openImportPriceChartingOwnedModal } from "./comparator.js";
import { normalizeMultipartMedia, mergeMultipartMedia } from "./coexistence.js";
import { refreshTooltips } from "./tooltips.js";

export async function loadCatalogData() {
  if (state.catalog.domain === "scout") return;
  if (state.catalog.domain === "compare") {
    const cmp = state.catalog.compare;
    const qs = new URLSearchParams({ left_set: cmp.left_set, right_set: cmp.right_set, platform: cmp.platform, region: cmp.region, q: cmp.q || "", include_backups: cmp.include_backups ? "1" : "0", limit: "250" });
    cmp.data = await apiFetch(`/api/catalog/compare?${qs}`);
    return;
  }
  if (state.catalog.domain === "dats") {
    state.catalog.datsSummary = await apiFetch("/api/dats/summary");
    return;
  }
  const key = catalogCacheKey();
  const qs = new URLSearchParams({ q: state.catalog.q, sort_by: state.catalog.sort_by, sort_dir: state.catalog.sort_dir, limit: String(state.catalog.limit), offset: String(state.catalog.offset), ...state.catalog.filters });
  const res = await apiFetch(`/api/catalog/${state.catalog.domain}?${qs}`);
  state.catalog.cacheByKey[key] = res;
  state.catalog.data = res;
}

export function bindCatalogSubtabs(root, renderSkeletonFn) {
  root.querySelectorAll("[data-cat-tab]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      state.catalog.domain = btn.dataset.catTab;
      state.catalog.offset = 0;
      state.catalog.filters = {};
      if (state.catalog.domain === "scout") return renderCatalog(root, renderSkeletonFn);
      if (state.catalog.domain === "compare") {
        if (state.catalog.compare.data) { renderCatalog(root, renderSkeletonFn); await yieldToMain(); refreshCurrentView({ background: true }).catch(() => {}); }
        else { if (renderSkeletonFn) renderSkeletonFn(root, "Comparing datasets..."); await yieldToMain(); refreshCurrentView({ background: false }); }
        return;
      }
      if (state.catalog.domain === "dats") {
        if (state.catalog.datsSummary) { renderCatalog(root, renderSkeletonFn); await yieldToMain(); refreshCurrentView({ background: true }).catch(() => {}); }
        else { if (renderSkeletonFn) renderSkeletonFn(root, "Loading Redump & No-Intro DAT catalog..."); await yieldToMain(); refreshCurrentView({ background: false }); }
        return;
      }
      const cached = state.catalog.cacheByKey[catalogCacheKey()];
      if (cached) { state.catalog.data = cached; renderCatalog(root, renderSkeletonFn); await yieldToMain(); refreshCurrentView({ background: true }).catch(() => {}); }
      else { if (renderSkeletonFn) renderSkeletonFn(root, `Loading ${state.catalog.domain}...`); await yieldToMain(); refreshCurrentView({ background: false }); }
    });
  });
}

export function renderCatalog(root, renderSkeletonFn) {
  if (!root) return;
  const disabledMsg = state.catalog.data?.status === "domain_disabled" ? state.catalog.data : state.catalog.compare.data?.status === "domain_disabled" ? state.catalog.compare.data : state.catalog.datsSummary?.status === "domain_disabled" ? state.catalog.datsSummary : null;
  if (disabledMsg) {
    root.innerHTML = `<section class="card"><div class="card-head"><h2 class="card-title">Catalog Module Disabled</h2><span class="badge badge-status badge-neutral">DISABLED</span></div><p class="muted-hint">${esc(disabledMsg.message || "Enable Media & Games Catalog in Settings to browse your library, wishlists, price guides, and DAT checksums.")}</p><div class="toolbar"><button type="button" class="primary-btn" id="catEnableModBtn">Open Modules &amp; Profiles</button></div></section>`;
    root.querySelector("#catEnableModBtn")?.addEventListener("click", () => { state.settings.subtab = "modules"; switchView("settings"); });
    return;
  }

  const dom = state.catalog.domain;
  const savedViews = state.prefs.global.saved_views || [];
  const subtabsHtml = [
    ["games", "Games (My Library)"], ["compare", "⇄ Compare Sets (A vs B)"], ["wishlist", "Wishlist & Retailers"],
    ["prices", "Reference Price Guide"], ["dats", "✓ Redump / No-Intro DATs"], ["scout", "In-Store Deal Scout"], ["media", "Plex Media"], ["books", "Books"],
  ].map(([id, label]) => `<button type="button" class="chip-btn ${dom === id ? "active" : ""}" data-cat-tab="${id}">${esc(label)}</button>`).join("");
  const savedChips = savedViews.map((sv, idx) => `<span class="badge badge-blue" style="cursor:pointer;" data-apply-saved="${idx}" title="Apply Saved Preset|Restore filter preset '${esc(sv.name)}'">★ ${esc(sv.name)}<button type="button" data-del-saved="${idx}" style="background:none;border:none;color:inherit;cursor:pointer;padding:0 0 0 4px;" aria-label="Remove preset">×</button></span>`).join("");

  if (dom === "compare") return renderDatasetComparator(root, subtabsHtml, (r) => bindCatalogSubtabs(r, renderSkeletonFn));
  if (dom === "dats") return renderDatsManager(root, subtabsHtml, (r) => bindCatalogSubtabs(r, renderSkeletonFn));
  if (dom === "scout") {
    const sr = state.catalog.scoutResult;
    root.innerHTML = `
      <div class="subtabs">${subtabsHtml}</div>
      <section class="card">
        <h2 class="card-title">In-Store Deal Scout (Ownership, Market Valuation &amp; Retailers)</h2>
        <form id="scoutForm" class="toolbar">
          <input type="search" id="scoutTitle" class="field-input grow" placeholder="Game title (e.g. Chrono Trigger, Silent Hill 2)..." required value="${esc(sr?.query?.title || "")}">
          <input type="text" id="scoutPlatform" class="field-input" placeholder="Platform (optional, e.g. SNES, PS2, Switch)" value="${esc(sr?.query?.platform || "")}">
          <button type="submit" class="primary-btn">Scout Deal</button>
        </form>
        ${sr ? `<div class="dash-grid" style="margin-top:8px;">
          <div class="card" style="background:var(--bg-card-alt);">
            <div class="card-head"><h3 class="card-title">Library Ownership Check</h3><span class="badge badge-status ${sr.is_owned ? "badge-green" : "badge-yellow"}">${sr.is_owned ? "OWNED IN LIBRARY" : "NOT IN LIBRARY"}</span></div>
            <div class="stack-list">${sr.owned_matches.length ? sr.owned_matches.map((g) => `<div class="list-row"><div class="list-row-main"><div class="list-row-title">${esc(g.title)}</div><div class="list-row-sub">${esc(g.platform)} · ${esc(g.owned_format || "owned")} · ${esc(g.play_status)}</div></div>${(g.user_rating ?? g.score) != null ? `<span class="badge badge-green num">My ★ ${g.user_rating ?? g.score}</span>` : ""}</div>`).join("") : `<p class="muted-hint">No matching copies found in your local games library.</p>`}</div>
          </div>
          <div class="card" style="background:var(--bg-card-alt);">
            <div class="card-head"><h3 class="card-title">Reference Valuation &amp; Retailer Links</h3>${sr.valuation?.console_name ? `<span class="badge badge-blue">${esc(sr.valuation.console_name)}</span>` : ""}</div>
            ${sr.valuation ? `<div class="kpi-grid"><div class="list-row"><div class="list-row-main"><div class="list-row-sub">Loose</div><strong class="num" style="font-size:20px;">${fmtMoney(sr.valuation.loose_price)}</strong></div></div><div class="list-row"><div class="list-row-main"><div class="list-row-sub">Complete (CIB)</div><strong class="num" style="font-size:20px;">${fmtMoney(sr.valuation.cib_price)}</strong></div></div><div class="list-row"><div class="list-row-main"><div class="list-row-sub">New / Sealed</div><strong class="num" style="font-size:20px;">${fmtMoney(sr.valuation.new_price)}</strong></div></div></div>` : `<p class="muted-hint">No reference price listing matched.</p>`}
            <div class="toolbar"><span class="muted-hint">Check Retailers:</span>${renderRetailerPills(sr.retailers || [], 8)}</div>
            <div class="toolbar"><button type="button" class="primary-btn" id="scoutAddWishBtn">+ Add to Wishlist</button><button type="button" class="ghost-btn" id="scoutAddGameBtn">+ Add to Owned Library</button></div>
          </div>
        </div>` : `<p class="muted-hint">Check instant library ownership, Loose / CIB / New market prices, and major retailer links.</p>`}
      </section>`;
    bindCatalogSubtabs(root, renderSkeletonFn);
    refreshTooltips(root);
    root.querySelector("#scoutForm")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      state.catalog.scoutResult = await apiFetch(`/api/scout?title=${encodeURIComponent(root.querySelector("#scoutTitle").value.trim())}&platform=${encodeURIComponent(root.querySelector("#scoutPlatform").value.trim())}`);
      renderCatalog(root, renderSkeletonFn);
    });
    root.querySelector("#scoutAddWishBtn")?.addEventListener("click", () => {
      const v = state.catalog.scoutResult.valuation || {};
      openWishlistEditor({ title: v.product_name || state.catalog.scoutResult.query.title, platform: v.console_name || state.catalog.scoutResult.query.platform || "", target_format: "CIB", current_price: v.cib_price || v.loose_price || "", store_url: v.url || "" });
    });
    root.querySelector("#scoutAddGameBtn")?.addEventListener("click", () => {
      const v = state.catalog.scoutResult.valuation || {};
      openGameEditor({ title: v.product_name || state.catalog.scoutResult.query.title, platform: v.console_name || state.catalog.scoutResult.query.platform || "Nintendo Switch", owned_format: "physical", play_status: "unplayed" });
    });
    return;
  }

  const d = state.catalog.data || { rows: [], total: 0, facets: {} };
  const facets = d.facets || {};
  let filterControls = "";
  if (dom === "games") {
    filterControls = `<select class="field-select" data-cat-filter="platform"><option value="">All Consoles</option>${(facets.platforms || []).map((p) => `<option value="${esc(p)}" ${state.catalog.filters.platform === p ? "selected" : ""}>${esc(p)}</option>`).join("")}</select><select class="field-select" data-cat-filter="source_set"><option value="">All Collection Sets</option>${(facets.source_sets || []).map((s) => `<option value="${esc(s.id)}" ${state.catalog.filters.source_set === s.id ? "selected" : ""}>${esc(s.label)}</option>`).join("")}</select><select class="field-select" data-cat-filter="owned_format"><option value="">All Formats</option>${(facets.formats || []).map((f) => `<option value="${esc(f)}" ${state.catalog.filters.owned_format === f ? "selected" : ""}>${esc(f)}</option>`).join("")}</select><select class="field-select" data-cat-filter="condition"><option value="">All Conditions</option>${(facets.conditions || []).map((c) => `<option value="${esc(c)}" ${state.catalog.filters.condition === c ? "selected" : ""}>${esc(c)}</option>`).join("")}</select><select class="field-select" data-cat-filter="play_status"><option value="">All Play Statuses</option>${(facets.statuses || []).map((s) => `<option value="${esc(s)}" ${state.catalog.filters.play_status === s ? "selected" : ""}>${esc(s)}</option>`).join("")}</select><select class="field-select" data-cat-filter="dat_status"><option value="">All Hash States</option>${(facets.dat_statuses || []).map((ds) => `<option value="${esc(ds)}" ${state.catalog.filters.dat_status === ds ? "selected" : ""}>DAT: ${esc(ds)}</option>`).join("")}</select>`;
  } else if (dom === "media") {
    filterControls = `<select class="field-select" data-cat-filter="plex_library"><option value="">All Libraries</option>${(facets.libraries || []).map((l) => `<option value="${esc(l)}" ${state.catalog.filters.plex_library === l ? "selected" : ""}>${esc(l)}</option>`).join("")}</select><select class="field-select" data-cat-filter="media_type"><option value="">All Types</option>${(facets.media_types || []).map((m) => `<option value="${esc(m)}" ${state.catalog.filters.media_type === m ? "selected" : ""}>${esc(m)}</option>`).join("")}</select>`;
  } else if (dom === "books") {
    filterControls = `<select class="field-select" data-cat-filter="status"><option value="">All Shelf Statuses</option>${(facets.statuses || []).map((s) => `<option value="${esc(s)}" ${state.catalog.filters.status === s ? "selected" : ""}>${esc(s)}</option>`).join("")}</select>`;
  } else if (dom === "wishlist") {
    filterControls = `<select class="field-select" data-cat-filter="status"><option value="">All Statuses</option>${(facets.statuses || []).map((s) => `<option value="${esc(s)}" ${state.catalog.filters.status === s ? "selected" : ""}>${esc(s)}</option>`).join("")}</select><select class="field-select" data-cat-filter="priority"><option value="">All Priorities</option>${(facets.priorities || []).map((p) => `<option value="${esc(p)}" ${state.catalog.filters.priority === p ? "selected" : ""}>${esc(p)}</option>`).join("")}</select>`;
  }

  const sortedRows = [...(d.rows || [])];
  if (state.catalog.sortCol) {
    const colKey = state.catalog.sortCol, dir = state.catalog.sortDir === "desc" ? -1 : 1;
    sortedRows.sort((a, b) => {
      const va = a[colKey] ?? "", vb = b[colKey] ?? "";
      return typeof va === "number" && typeof vb === "number" ? (va - vb) * dir : String(va).localeCompare(String(vb), undefined, { numeric: true, sensitivity: "base" }) * dir;
    });
  }

  let headers = [], sortKeys = [], tbodyRows = "";
  if (dom === "games") {
    headers = ["☑", "Title", "Console", "Sets & Copies", "Status", "Format", "DAT / Hash", "My ★ / Critic"];
    sortKeys = ["", "title", "platform", "tags", "play_status", "owned_format", "dat_status", "user_rating"];
    tbodyRows = sortedRows.map((r, i) => {
      let tagBadges = "";
      const svcCounts = r.serviceCounts || r.service_counts || {};
      const svcBadges = Object.entries(svcCounts).filter(([, cnt]) => Number(cnt) > 0).map(([svc, cnt]) => `<span class="badge badge-blue num">${esc(svc === "infinitebacklog" ? "IB" : svc === "pricecharting" ? "PC" : svc === "steam" ? "Steam" : svc)}${Number(cnt) > 1 ? ` ×${cnt}` : ""}</span>`).join(" ");
      try { const parsedTags = r.tags ? JSON.parse(r.tags) : []; if (!svcBadges && Array.isArray(parsedTags)) tagBadges = parsedTags.slice(0, 3).map((t) => `<span class="badge ${t === "pricecharting_owned" || t === "infinitebacklog" ? "badge-blue" : "badge-neutral"}">${esc(t)}</span>`).join(" "); } catch (err) { console.debug("Tag parse skipped:", err); }
      let condText = "";
      try { const em = r.extra_metadata ? JSON.parse(r.extra_metadata) : {}; condText = em["PriceCharting Condition"] || em.Condition || ""; } catch (err) { console.debug("Meta parse skipped:", err); }
      const bkpCnt = Number(r.personalBackupCount ?? r.personal_backup_count ?? 0);
      const bkpBadge = bkpCnt > 0 ? `<span class="badge badge-green num" title="Personal ROM/ISO Backups|Counted separately from physical or digital copies">💾 ×${bkpCnt}</span>` : "";
      const mismatchBadge = r.copyMismatch || r.copy_mismatch ? `<span class="badge badge-status badge-red" title="Copy Count Mismatch|Service copy counts disagree across tracked platforms">COPY DIFF</span>` : "";
      const editionBadge = r.version_edition || r.versionEdition ? ` <span class="badge badge-neutral">${esc(r.version_edition || r.versionEdition)}</span>` : "";
      const statusCls = r.play_status === "completed" ? "badge-green" : r.play_status === "playing" ? "badge-blue" : r.play_status === "shelved" ? "badge-yellow" : "badge-neutral";
      const datSt = r.dat_status || "unverified";
      const datBadge = datSt === "verified" ? `<span class="badge badge-green mono" title="${esc(r.dat_rom_name || r.sha1 || r.crc32 || "")}">✓ ${esc(r.dat_source || "DAT")} ${esc(r.crc32 || "")}</span>` : datSt === "mismatch" ? `<span class="badge badge-status badge-red mono">MISMATCH ${esc(r.crc32 || "")}</span>` : r.crc32 ? `<span class="badge badge-yellow mono">${esc(r.crc32)}</span>` : `<span class="muted-hint">—</span>`;
      const myVal = r.user_rating ?? r.score;
      const myBadge = myVal != null ? `<span class="badge badge-green num" title="Personal Rating|Your personal game score">My ★ ${myVal}</span>` : "";
      const critBadge = r.critic_rating != null ? `<span class="badge badge-neutral num" title="Critic Rating|Critic or IGDB score">Critic ★ ${r.critic_rating}</span>` : r.audience_rating != null ? `<span class="badge badge-neutral num" title="Audience Rating|Community score">Aud ★ ${r.audience_rating}</span>` : "";
      return `<tr data-row-idx="${i}"><td class="no-row-open" style="width:32px;"><input type="checkbox" data-cat-check="${i}" ${state.catalog.selectedIndices?.has(i) ? "checked" : ""}></td><th scope="row">${esc(r.title)}${editionBadge}</th><td>${esc(r.platform)}</td><td>${svcBadges || tagBadges} ${bkpBadge} ${mismatchBadge} ${condText ? `<span class="badge badge-neutral">${esc(condText)}</span>` : ""}</td><td><span class="badge badge-status ${statusCls}">${esc(r.play_status)}</span></td><td>${esc(r.owned_format || "—")}</td><td>${datBadge}</td><td class="num">${[myBadge, critBadge].filter(Boolean).join(" ") || "—"}</td></tr>`;
    }).join("");
  } else if (dom === "media") {
    headers = ["☑", "Title", "Series", "Library", "Type", "Year", "My Rating / Critic · Aud"];
    sortKeys = ["", "title", "series_title", "plex_library", "media_type", "release_year", "user_rating"];
    tbodyRows = sortedRows.map((r, i) => {
      const isMultipart = /\b(pt\d+|part\s*\d+|cd\d+|disc\d+)\b/i.test(String(r.title || r.file_path || ""));
      const ratingsHtml = `${r.user_rating != null ? `<span class="badge badge-green num">My ★ ${r.user_rating}</span> ` : ""}${r.critic_rating != null ? `<span class="badge badge-neutral num">Crit ★ ${r.critic_rating}</span> ` : ""}${r.audience_rating != null ? `<span class="badge badge-blue num">Aud ★ ${r.audience_rating}</span> ` : ""}`;
      return `<tr data-row-idx="${i}"><td class="no-row-open" style="width:32px;"><input type="checkbox" data-cat-check="${i}" ${state.catalog.selectedIndices?.has(i) ? "checked" : ""}></td><th scope="row">${esc(r.title)}</th><td>${esc(r.series_title ? `${r.series_title} S${r.season_number || 0}E${r.episode_number || 0}` : "—")}</td><td>${esc(r.plex_library || "—")}</td><td><span class="badge badge-status badge-neutral">${esc(r.media_type)}</span></td><td class="num">${esc(r.release_year || "—")}</td><td class="no-row-open num">${ratingsHtml}${isMultipart ? `<button type="button" class="chip-btn" data-merge-part-row="${esc(r.title || r.id)}">🔗 Merge Split Parts</button>` : ratingsHtml ? "" : `<span class="muted-hint">—</span>`}</td></tr>`;
    }).join("");
  } else if (dom === "books") {
    headers = ["☑", "Title", "Author", "Series", "Status", "Format", "My Rating / Avg"];
    sortKeys = ["", "title", "author", "series", "status", "format", "user_rating"];
    tbodyRows = sortedRows.map((r, i) => {
      const ratingsHtml = `${r.user_rating != null ? `<span class="badge badge-green num">My ★ ${r.user_rating}</span> ` : ""}${r.audience_rating != null ? `<span class="badge badge-neutral num">Avg ★ ${r.audience_rating}</span>` : !r.user_rating && r.rating != null ? `★ ${r.rating}` : !r.user_rating ? "—" : ""}`;
      return `<tr data-row-idx="${i}"><td class="no-row-open" style="width:32px;"><input type="checkbox" data-cat-check="${i}" ${state.catalog.selectedIndices?.has(i) ? "checked" : ""}></td><th scope="row">${esc(r.title)}</th><td>${esc(r.author || "—")}</td><td>${esc(r.series || "—")}</td><td><span class="badge badge-status ${r.status === "finished" ? "badge-green" : "badge-neutral"}">${esc(r.status)}</span></td><td>${esc(r.format || "—")}</td><td class="num">${ratingsHtml}</td></tr>`;
    }).join("");
  } else if (dom === "wishlist") {
    headers = ["☑", "Title", "Platform", "Format", "Target $", "Market $", "Major Retailers", "Status"];
    sortKeys = ["", "title", "platform", "target_format", "target_price", "current_price", "", "status"];
    tbodyRows = sortedRows.map((r, i) => {
      const isDeal = r.target_price && r.current_price && Number(r.current_price) <= Number(r.target_price);
      return `<tr data-row-idx="${i}"><td class="no-row-open" style="width:32px;"><input type="checkbox" data-cat-check="${i}" ${state.catalog.selectedIndices?.has(i) ? "checked" : ""}></td><th scope="row">${esc(r.title)}</th><td>${esc(r.platform || r.item_type)}</td><td>${esc(r.target_format || "—")}</td><td class="num">${fmtMoney(r.target_price)}</td><td class="num"><span class="badge ${isDeal ? "badge-green" : "badge-neutral"}">${fmtMoney(r.current_price)}</span></td><td class="no-row-open"><div class="toolbar" style="gap:4px;">${renderRetailerPills(r.retailers || [], 5)}</div></td><td><span class="badge badge-status badge-blue">${esc(r.status)} (${esc(r.priority)})</span></td></tr>`;
    }).join("");
  } else if (dom === "prices") {
    headers = ["☑", "Product (Reference Guide)", "Console", "In Library", "Loose", "CIB", "New"];
    sortKeys = ["", "product_name", "console_name", "is_owned", "loose_price", "cib_price", "new_price"];
    tbodyRows = sortedRows.map((r, i) => `<tr data-row-idx="${i}"><td class="no-row-open" style="width:32px;"><input type="checkbox" data-cat-check="${i}" ${state.catalog.selectedIndices?.has(i) ? "checked" : ""}></td><th scope="row">${esc(r.product_name)}</th><td>${esc(r.console_name)}</td><td><span class="badge badge-status ${r.is_owned ? "badge-green" : "badge-neutral"}">${r.is_owned ? "OWNED" : "REF ONLY"}</span></td><td class="num">${fmtMoney(r.loose_price)}</td><td class="num">${fmtMoney(r.cib_price)}</td><td class="num">${fmtMoney(r.new_price)}</td></tr>`).join("");
  }

  const startNum = d.total === 0 ? 0 : d.offset + 1, endNum = Math.min(d.total, d.offset + sortedRows.length);
  const priceGuideNotice = dom === "prices" ? `<div class="card" style="background:var(--bg-card-alt);padding:8px 12px;"><span class="badge badge-status badge-yellow">REFERENCE DATABASE</span><span class="muted-hint" style="margin-inline-start:8px;">This view displays the PriceCharting Reference Price Guide, not your personal owned library.</span></div>` : "";
  const mediaMultipartStrip = dom === "media" ? `<div class="card" style="background:var(--bg-card-alt);padding:8px 12px;"><div class="card-head"><div><strong>🎬 Multi-Part Media Naming &amp; Auto-Merge</strong><p class="card-subtitle">Normalize split files to terminal <code>ptX</code> naming or merge split parts losslessly.</p></div><div class="toolbar"><button type="button" class="ghost-btn" id="catNormalizeMultipartBtn">🏷 Normalize Filenames</button><button type="button" class="primary-btn" id="catMergeMultipartBtn">🔗 Merge Split Parts</button></div></div></div>` : "";
  const thCells = headers.map((h, idx) => {
    const sk = sortKeys[idx] || "";
    if (!sk) return `<th>${esc(h)}</th>`;
    const arrow = state.catalog.sortCol === sk ? (state.catalog.sortDir === "desc" ? "▼" : "▲") : "⇅";
    return `<th class="sortable-th" data-cat-sort="${esc(sk)}">${esc(h)} <span class="sort-indicator">${arrow}</span></th>`;
  }).join("");

  root.innerHTML = `
    <div class="toolbar" style="justify-content:space-between;">
      <div class="subtabs">${subtabsHtml}</div>
      <div class="toolbar">
        <button type="button" class="ghost-btn" id="catSelectAllBtn" title="Select All Visible Rows|Check all visible rows on this page">☑ All</button>
        <button type="button" class="ghost-btn" id="catInvertSelBtn" title="Invert Selection|Flip checked rows on this page">⇄ Invert</button>
        <button type="button" class="ghost-btn" id="catExportCsvBtn" title="Export Rows To CSV|Export selected or visible rows to CSV">📥 CSV</button>
        <button type="button" class="ghost-btn" id="catExportJsonBtn" title="Export Rows To JSON|Export selected or visible rows to JSON">📥 JSON</button>
        <button type="button" class="ghost-btn" id="importPcOwnedBtn">📥 Import PriceCharting CSV</button>
        <button type="button" class="primary-btn" id="addGameBtn">+ Add Game</button>
        <button type="button" class="ghost-btn" id="addWishBtn">+ Add Wishlist</button>
      </div>
    </div>
    ${priceGuideNotice}${mediaMultipartStrip}
    ${savedChips ? `<div class="toolbar"><span class="muted-hint">Saved Views:</span>${savedChips}</div>` : ""}
    <form id="catSearchForm" class="toolbar">
      <input type="search" id="catSearchInput" class="field-input grow" placeholder="Filter ${esc(dom)}..." value="${esc(state.catalog.q)}">
      ${filterControls}
      <button type="submit" class="ghost-btn">Search</button>
      <button type="button" class="ghost-btn" id="saveCurrentViewBtn" title="Save Filter Preset|Remember this filter preset across devices">★ Save View</button>
    </form>
    <div tabindex="0" role="region" aria-labelledby="catTableCaption" class="table-wrapper contain-panel">
      <table class="data-table" style="${esc(tableLabelStyle(headers))}">
        <caption id="catTableCaption">Showing ${fmtNum(startNum)}–${fmtNum(endNum)} of ${fmtNum(d.total)} records (click headers to sort or click a row to edit)</caption>
        <thead><tr>${thCells}</tr></thead>
        <tbody>${tbodyRows || `<tr><td colspan="${headers.length}" class="muted-hint">No matching records.</td></tr>`}</tbody>
      </table>
    </div>
    <div class="toolbar" style="justify-content:space-between;">
      <span class="muted-hint num">Records ${fmtNum(startNum)}–${fmtNum(endNum)} of ${fmtNum(d.total)}</span>
      <div class="toolbar">
        <button type="button" class="ghost-btn" id="catPrevBtn" ${d.offset <= 0 ? "disabled" : ""}>← Previous</button>
        <button type="button" class="ghost-btn" id="catNextBtn" ${d.offset + d.limit >= d.total ? "disabled" : ""}>Next →</button>
      </div>
    </div>`;

  bindCatalogSubtabs(root, renderSkeletonFn);
  refreshTooltips(root);
  root.querySelector("#catNormalizeMultipartBtn")?.addEventListener("click", () => normalizeMultipartMedia([]));
  root.querySelector("#catMergeMultipartBtn")?.addEventListener("click", () => mergeMultipartMedia("all", []));
  root.querySelectorAll("[data-merge-part-row]").forEach((btn) => btn.addEventListener("click", (e) => { e.stopPropagation(); mergeMultipartMedia(btn.dataset.mergePartRow, []); }));
  root.querySelectorAll("[data-cat-sort]").forEach((th) => th.addEventListener("click", () => {
    const sk = th.dataset.catSort;
    if (state.catalog.sortCol === sk) state.catalog.sortDir = state.catalog.sortDir === "asc" ? "desc" : "asc";
    else { state.catalog.sortCol = sk; state.catalog.sortDir = "asc"; }
    renderCatalog(root, renderSkeletonFn);
  }));
  root.querySelectorAll("[data-cat-check]").forEach((cb) => cb.addEventListener("change", () => {
    const idx = Number(cb.dataset.catCheck);
    if (cb.checked) state.catalog.selectedIndices.add(idx); else state.catalog.selectedIndices.delete(idx);
  }));
  root.querySelector("#catSelectAllBtn")?.addEventListener("click", () => { sortedRows.forEach((_, idx) => state.catalog.selectedIndices.add(idx)); renderCatalog(root, renderSkeletonFn); });
  root.querySelector("#catInvertSelBtn")?.addEventListener("click", () => { sortedRows.forEach((_, idx) => state.catalog.selectedIndices.has(idx) ? state.catalog.selectedIndices.delete(idx) : state.catalog.selectedIndices.add(idx)); renderCatalog(root, renderSkeletonFn); });
  const getExportTargetRows = () => (state.catalog.selectedIndices?.size > 0 ? sortedRows.filter((_, idx) => state.catalog.selectedIndices.has(idx)) : sortedRows);
  root.querySelector("#catExportCsvBtn")?.addEventListener("click", () => exportRowsAsCsv(`greenroom_${dom}`, getExportTargetRows()));
  root.querySelector("#catExportJsonBtn")?.addEventListener("click", () => exportRowsAsJson(`greenroom_${dom}`, getExportTargetRows()));
  root.querySelector("#catSearchForm").addEventListener("submit", (e) => { e.preventDefault(); state.catalog.q = root.querySelector("#catSearchInput").value.trim(); state.catalog.offset = 0; refreshCurrentView(); });
  root.querySelectorAll("[data-cat-filter]").forEach((sel) => sel.addEventListener("change", () => { state.catalog.filters[sel.dataset.catFilter] = sel.value; state.catalog.offset = 0; refreshCurrentView(); }));
  root.querySelector("#catPrevBtn")?.addEventListener("click", () => { state.catalog.offset = Math.max(0, state.catalog.offset - state.catalog.limit); refreshCurrentView(); });
  root.querySelector("#catNextBtn")?.addEventListener("click", () => { state.catalog.offset += state.catalog.limit; refreshCurrentView(); });
  root.querySelector("#addGameBtn")?.addEventListener("click", () => openGameEditor({}));
  root.querySelector("#addWishBtn")?.addEventListener("click", () => openWishlistEditor({}));
  root.querySelector("#importPcOwnedBtn")?.addEventListener("click", () => openImportPriceChartingOwnedModal());
  root.querySelector("#saveCurrentViewBtn")?.addEventListener("click", async () => {
    const label = prompt("Name this saved filter preset:");
    if (!label) return;
    await savePreferences({ saved_views: [...(state.prefs.global.saved_views || []), { name: label.trim(), domain: state.catalog.domain, q: state.catalog.q, filters: { ...state.catalog.filters } }] }, "global");
    showToast(`Saved preset '${label.trim()}'`);
    renderCatalog(root, renderSkeletonFn);
  });
  root.querySelectorAll("[data-apply-saved]").forEach((chip) => chip.addEventListener("click", (e) => {
    if (e.target.dataset.delSaved !== undefined) return;
    const preset = (state.prefs.global.saved_views || [])[Number(chip.dataset.applySaved)];
    if (!preset) return;
    state.catalog.domain = preset.domain || "games"; state.catalog.q = preset.q || ""; state.catalog.filters = { ...(preset.filters || {}) }; state.catalog.offset = 0; refreshCurrentView();
  }));
  root.querySelectorAll("[data-del-saved]").forEach((btn) => btn.addEventListener("click", async (e) => {
    e.stopPropagation();
    await savePreferences({ saved_views: (state.prefs.global.saved_views || []).filter((_, i) => i !== Number(btn.dataset.delSaved)) }, "global");
    renderCatalog(root, renderSkeletonFn);
  }));
  root.querySelectorAll("tbody tr[data-row-idx]").forEach((tr) => tr.addEventListener("click", (e) => {
    if (e.target.closest(".no-row-open")) return;
    const row = sortedRows[Number(tr.dataset.rowIdx)];
    if (!row) return;
    if (dom === "games") openGameEditor(row);
    else if (dom === "wishlist") openWishlistEditor(row);
    else if (dom === "prices") { state.catalog.domain = "scout"; apiFetch(`/api/scout?title=${encodeURIComponent(row.product_name)}&platform=${encodeURIComponent(row.console_name)}`).then((res) => { state.catalog.scoutResult = res; renderCatalog(root, renderSkeletonFn); }); }
    else openGenericRowEditor(d.table, d.columns, d.primary_keys, row);
  }));
}
