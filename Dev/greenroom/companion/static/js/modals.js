/* @module static/js/modals.js
 * @purpose Modal editors (Games, Wishlist, Pantry, Cuts, Drafts, Generic Row CRUD), Omni-Search dialog, and Preferences dialog.
 * @inputs Selected entity records, Omni-Search query input, and user preference selections.
 * @outputs Native <dialog> modal interactions and REST persistence mutations. */

import { state, esc, safeHttpUrl, showToast, copyTextToClipboard, switchView, refreshCurrentView } from "./state.js";
import { apiFetch, savePreferences, saveConfigSettings, applyThemeMode, applyColorSchemeMeta, applyDensityAttr, buildCompanionOriginsList, triggerPwaInstallPrompt, getDeferredInstallPrompt, requestEnableTailscaleHttps } from "./api.js";
import { refreshTooltips } from "./tooltips.js";

export function renderRetailerPills(retailers = [], maxCount = 6) {
  if (!Array.isArray(retailers) || !retailers.length) return "";
  return retailers.slice(0, maxCount).map((r) => {
    const safeHref = safeHttpUrl(r.url);
    if (!safeHref) return "";
    const cls = r.pinned ? "badge badge-green" : "badge badge-neutral";
    const icon = r.pinned ? "★ " : "↗ ";
    return `<a href="${esc(safeHref)}" target="_blank" rel="noopener noreferrer" class="${cls} no-row-open" style="text-decoration:none;" title="${esc(r.pinned ? "Direct Store Link|Open saved product listing in a new tab" : `Search ${r.name}|Search this title on ${r.name}`)}">${icon}${esc(r.name)}</a>`;
  }).filter(Boolean).join(" ");
}

export function setupLightDismissFallback() {
  if (!("closedBy" in HTMLDialogElement.prototype)) {
    document.querySelectorAll('dialog[closedby="any"]').forEach((dialog) => {
      dialog.addEventListener("click", (event) => {
        if (event.target !== dialog) return;
        const rect = dialog.getBoundingClientRect();
        if (!(rect.top <= event.clientY && event.clientY <= rect.top + rect.height && rect.left <= event.clientX && event.clientX <= rect.left + rect.width)) dialog.close();
      });
    });
  }
  document.querySelectorAll("[data-close-dialog]").forEach((btn) => btn.addEventListener("click", () => { const dlg = document.getElementById(btn.dataset.closeDialog); if (dlg?.open) dlg.close(); }));
}

export function openEditorModal(title, innerHtml, bindFn) {
  const dlg = document.getElementById("editorDialog");
  document.getElementById("editorDialogTitle").textContent = title;
  const body = document.getElementById("editorBody");
  body.innerHTML = innerHtml;
  refreshTooltips(body);
  if (bindFn) bindFn(body, dlg);
  if (!dlg.open) dlg.showModal();
}

export function openGameEditor(game) {
  const isEdit = Boolean(game && game.id);
  const datStatus = game.dat_status || "unverified";
  const datCls = datStatus === "verified" ? "badge-green" : datStatus === "mismatch" ? "badge-red" : "badge-neutral";
  openEditorModal(
    isEdit ? `Edit Game: ${game.title}` : "Add Game to Library",
    `<form id="gameEditForm" class="stack-list">
      <div class="form-grid">
        <div class="form-field span-full"><label>Title</label><input name="title" class="field-input" required value="${esc(game.title || "")}"></div>
        <div class="form-field"><label>Platform</label><input name="platform" class="field-input" required value="${esc(game.platform || "Nintendo Switch")}"></div>
        <div class="form-field"><label>Edition / Version</label><input name="version_edition" class="field-input" placeholder="e.g. GOTY, Greatest Hits" value="${esc(game.version_edition || game.versionEdition || "")}"></div>
        <div class="form-field"><label>Play Status</label><select name="play_status" class="field-select">${["unplayed", "playing", "shelved", "completed", "abandoned", "endless", "wishlist"].map((s) => `<option value="${s}" ${(game.play_status || "unplayed") === s ? "selected" : ""}>${s}</option>`).join("")}</select></div>
        <div class="form-field"><label>Owned Format</label><select name="owned_format" class="field-select">${["physical", "digital", "rom", "rip"].map((f) => `<option value="${f}" ${(game.owned_format || "physical") === f ? "selected" : ""}>${f}</option>`).join("")}</select></div>
        <div class="form-field"><label>Personal Rating (My ★)</label><input type="number" step="0.1" name="score" class="field-input" value="${esc(game.user_rating ?? game.score ?? "")}"></div>
        <div class="form-field"><label>Playtime (minutes)</label><input type="number" name="playtime_minutes" class="field-input" value="${esc(game.playtime_minutes ?? 0)}"></div>
        <div class="form-field"><label>Completion Date</label><input type="text" name="completion_date" class="field-input" placeholder="YYYY-MM-DD" value="${esc(game.completion_date || "")}"></div>
        <div class="form-field span-full"><label>Tags (JSON array or comma-separated)</label><input name="tags" class="field-input mono" value="${esc(game.tags || "")}"></div>
      </div>
      <div class="card" style="background:var(--bg-card-alt);">
        <div class="card-head"><h3 class="card-title">ROM / ISO Hash Verification (No-Intro &amp; Redump)</h3><span class="badge badge-status ${datCls}">${esc(datStatus)}</span></div>
        <div class="form-grid">
          <div class="form-field"><label>CRC32 (8-hex)</label><input name="crc32" id="gameCrcInput" class="field-input mono" placeholder="e.g. A1B2C3D4" value="${esc(game.crc32 || "")}"></div>
          <div class="form-field"><label>MD5 (32-hex)</label><input name="md5" id="gameMd5Input" class="field-input mono" value="${esc(game.md5 || "")}"></div>
          <div class="form-field span-full"><label>SHA-1 (40-hex)</label><input name="sha1" id="gameSha1Input" class="field-input mono" value="${esc(game.sha1 || "")}"></div>
          <div class="form-field"><label>DAT Source</label><select name="dat_source" id="gameDatSourceInput" class="field-select"><option value="" ${!game.dat_source ? "selected" : ""}>—</option><option value="no-intro" ${game.dat_source === "no-intro" ? "selected" : ""}>no-intro</option><option value="redump" ${game.dat_source === "redump" ? "selected" : ""}>redump</option><option value="custom" ${game.dat_source === "custom" ? "selected" : ""}>custom</option></select></div>
          <div class="form-field"><label>DAT Verification Status</label><select name="dat_status" id="gameDatStatusInput" class="field-select">${["unverified", "verified", "mismatch", "no_dat"].map((ds) => `<option value="${ds}" ${datStatus === ds ? "selected" : ""}>${ds}</option>`).join("")}</select></div>
          <div class="form-field span-full"><label>Matched DAT ROM / Track Name</label><input name="dat_rom_name" id="gameDatRomInput" class="field-input mono" value="${esc(game.dat_rom_name || "")}"></div>
          <div class="form-field"><label>Local Backup File Path</label><input name="file_path" class="field-input mono" value="${esc(game.file_path || "")}"></div>
          <div class="form-field"><label>File Size (Bytes)</label><input type="number" name="file_size" class="field-input mono" value="${esc(game.file_size ?? "")}"></div>
        </div>
        <div class="toolbar">
          ${isEdit ? `<button type="button" class="ghost-btn" id="verifySingleGameHashBtn">⚡ Verify File Hash</button>` : ""}
          <button type="button" class="ghost-btn" id="lookupGameHashInDatsBtn">🔍 Lookup Hash in Loaded DATs</button>
        </div>
      </div>
      <div class="form-field span-full"><label>Notes</label><textarea name="notes" class="field-textarea">${esc(game.notes || "")}</textarea></div>
      <div class="toolbar" style="justify-content:flex-end;"><button type="submit" class="primary-btn">${isEdit ? "Save Changes" : "Add Game"}</button></div>
    </form>`,
    (body, dlg) => {
      body.querySelector("#verifySingleGameHashBtn")?.addEventListener("click", async () => {
        try {
          const res = await apiFetch(`/api/games/${encodeURIComponent(game.id)}/verify-hash`, { method: "POST" });
          const g = res.game || {};
          body.querySelector("#gameCrcInput").value = g.crc32 || ""; body.querySelector("#gameMd5Input").value = g.md5 || "";
          body.querySelector("#gameSha1Input").value = g.sha1 || ""; body.querySelector("#gameDatSourceInput").value = g.dat_source || "";
          body.querySelector("#gameDatStatusInput").value = g.dat_status || "unverified"; body.querySelector("#gameDatRomInput").value = g.dat_rom_name || "";
          showToast(`Hash status: ${res.dat_status}`); state.catalog.cacheByKey = {};
        } catch (err) { showToast(err.message); }
      });
      body.querySelector("#lookupGameHashInDatsBtn")?.addEventListener("click", async () => {
        try {
          const crc = body.querySelector("#gameCrcInput").value.trim(), md5 = body.querySelector("#gameMd5Input").value.trim(), sha1 = body.querySelector("#gameSha1Input").value.trim();
          if (!crc && !md5 && !sha1) return showToast("Enter a CRC32, MD5, or SHA-1 checksum first.");
          const qs = new URLSearchParams();
          if (crc) qs.set("crc32", crc); if (md5) qs.set("md5", md5); if (sha1) qs.set("sha1", sha1);
          const res = await apiFetch(`/api/dats/lookup?${qs}`);
          if (res.matched && res.entry) {
            const en = res.entry;
            if (en.crc32) body.querySelector("#gameCrcInput").value = en.crc32;
            if (en.md5) body.querySelector("#gameMd5Input").value = en.md5;
            if (en.sha1) body.querySelector("#gameSha1Input").value = en.sha1;
            body.querySelector("#gameDatSourceInput").value = en.dat_source || "no-intro";
            body.querySelector("#gameDatStatusInput").value = "verified";
            body.querySelector("#gameDatRomInput").value = en.rom_name || "";
            showToast(`Matched ${en.dat_source}: ${en.rom_name}`);
          } else { body.querySelector("#gameDatStatusInput").value = "mismatch"; showToast("Checksum not found in loaded DATs."); }
        } catch (err) { showToast(err.message); }
      });
      body.querySelector("#gameEditForm").addEventListener("submit", async (e) => {
        e.preventDefault();
        const payload = Object.fromEntries(new FormData(e.target).entries());
        if (isEdit) payload.id = game.id;
        payload.user_rating = payload.score;
        if (payload.dat_status === "verified") payload.verified_backup = 1;
        await apiFetch("/api/games", { method: "POST", body: payload });
        dlg.close(); showToast("Saved game record"); state.catalog.cacheByKey = {}; refreshCurrentView();
      });
    }
  );
}

export function openWishlistEditor(item) {
  const isEdit = Boolean(item && item.id);
  openEditorModal(
    isEdit ? `Edit Wishlist: ${item.title}` : "Add Target to Wishlist & Deal Scout",
    `<form id="wishEditForm" class="stack-list">
      <div class="form-grid">
        <div class="form-field span-full"><label>Title</label><input name="title" class="field-input" required value="${esc(item.title || "")}"></div>
        <div class="form-field"><label>Type</label><select name="item_type" class="field-select">${["game", "movie", "tv", "anime", "book", "hardware"].map((t) => `<option value="${t}" ${(item.item_type || "game") === t ? "selected" : ""}>${t}</option>`).join("")}</select></div>
        <div class="form-field"><label>Platform</label><input name="platform" class="field-input" placeholder="Nintendo Switch, SNES, PS2, 4K UHD..." value="${esc(item.platform || "")}"></div>
        <div class="form-field"><label>Target Format</label><select name="target_format" class="field-select">${["CIB", "Loose", "New", "Digital", "Hardcover"].map((f) => `<option value="${f}" ${(item.target_format || "CIB") === f ? "selected" : ""}>${f}</option>`).join("")}</select></div>
        <div class="form-field"><label>Priority</label><select name="priority" class="field-select">${["high", "medium", "low"].map((p) => `<option value="${p}" ${(item.priority || "medium") === p ? "selected" : ""}>${p}</option>`).join("")}</select></div>
        <div class="form-field"><label>Target Price ($)</label><input type="number" step="0.01" name="target_price" class="field-input" value="${esc(item.target_price ?? "")}"></div>
        <div class="form-field"><label>Current Market Price ($)</label><input type="number" step="0.01" name="current_price" class="field-input" value="${esc(item.current_price ?? "")}"></div>
        <div class="form-field"><label>Status</label><select name="status" class="field-select">${["seeking", "monitoring", "ordered", "acquired", "shelved"].map((s) => `<option value="${s}" ${(item.status || "seeking") === s ? "selected" : ""}>${s}</option>`).join("")}</select></div>
        <div class="form-field span-full"><label>Primary Store URL</label><input type="url" name="store_url" class="field-input mono" placeholder="https://..." value="${esc(item.store_url || "")}"></div>
        <div class="form-field span-full"><label>Additional Retailer Links (JSON)</label><input type="text" name="retailer_links_json" class="field-input mono" placeholder='{"eBay": "https://..."}' value="${esc(item.retailer_links_json || "")}"></div>
        ${item.retailers?.length ? `<div class="form-field span-full"><label>Quick-Launch Retailer Links</label><div class="toolbar">${renderRetailerPills(item.retailers, 8)}</div></div>` : ""}
        <div class="form-field span-full"><label>Notes</label><textarea name="notes" class="field-textarea">${esc(item.notes || "")}</textarea></div>
      </div>
      <div class="toolbar" style="justify-content:flex-end;"><button type="submit" class="primary-btn">Save Wishlist Item</button></div>
    </form>`,
    (body, dlg) => {
      body.querySelector("#wishEditForm").addEventListener("submit", async (e) => {
        e.preventDefault();
        const payload = Object.fromEntries(new FormData(e.target).entries());
        if (isEdit) payload.id = item.id;
        await apiFetch("/api/wishlist", { method: "POST", body: payload });
        dlg.close(); showToast("Wishlist updated"); state.catalog.cacheByKey = {}; refreshCurrentView();
      });
    }
  );
}

export function openPantryEditor(item) {
  openEditorModal(
    `Edit Pantry Item: ${item.name}`,
    `<form id="pantryEditForm" class="stack-list">
      <div class="form-grid">
        <div class="form-field span-full"><label>Name</label><input name="name" class="field-input" required value="${esc(item.name)}"></div>
        <div class="form-field"><label>Location</label><select name="location" class="field-select">${["Fridge", "Freezer", "Pantry"].map((l) => `<option value="${l}" ${item.location === l ? "selected" : ""}>${l}</option>`).join("")}</select></div>
        <div class="form-field"><label>Category</label><input name="category" class="field-input" value="${esc(item.category || "")}"></div>
        <div class="form-field"><label>Quantity</label><input type="number" step="0.25" name="quantity" class="field-input" value="${esc(item.quantity ?? 1)}"></div>
        <div class="form-field"><label>Unit</label><input name="unit" class="field-input" value="${esc(item.unit || "count")}"></div>
        <div class="form-field"><label>Stock Status</label><select name="stock_status" class="field-select">${["In Stock", "Low Stock", "Depleted"].map((s) => `<option value="${s}" ${(item.stock_status || "In Stock") === s ? "selected" : ""}>${s}</option>`).join("")}</select></div>
        <div class="form-field"><label>Waste Tier</label><select name="waste_tier" class="field-select">${["Tier 1 Expiring", "Tier 2 - Core Staple", "Tier 3 - Freezer Stock", "Tier 4 - Depleted"].map((w) => `<option value="${w}" ${(item.waste_tier || "") === w ? "selected" : ""}>${w}</option>`).join("")}</select></div>
        <div class="form-field"><label>Expiry Date</label><input type="text" name="expiry_date" class="field-input" placeholder="YYYY-MM-DD" value="${esc(item.expiry_date || "")}"></div>
        <div class="form-field span-full"><label>Notes</label><textarea name="notes" class="field-textarea">${esc(item.notes || "")}</textarea></div>
      </div>
      <div class="toolbar" style="justify-content:flex-end;"><button type="submit" class="primary-btn">Save Pantry Item</button></div>
    </form>`,
    (body, dlg) => {
      body.querySelector("#pantryEditForm").addEventListener("submit", async (e) => {
        e.preventDefault();
        await apiFetch(`/api/kitchen/pantry/${item.id}`, { method: "PATCH", body: Object.fromEntries(new FormData(e.target).entries()) });
        dlg.close(); showToast("Pantry item updated"); refreshCurrentView();
      });
    }
  );
}

export function openCutEditor(cut) {
  const isEdit = Boolean(cut && cut.cut_id);
  const channelSet = new Set();
  for (const ch of (state.summary?.channels || [])) if (ch?.channel_id) channelSet.add(ch.channel_id);
  for (const ch of (state.accounts?.content_production?.channels || [])) if (ch?.id) channelSet.add(ch.id);
  if (cut?.channel_id) channelSet.add(cut.channel_id);
  const channelOptions = [...channelSet];
  const defaultChannel = cut?.channel_id || channelOptions[0] || "";
  openEditorModal(
    isEdit ? `Edit Cut: ${cut.project_title}` : "New Workstation Production Cut",
    `<form id="cutEditForm" class="stack-list">
      <div class="form-grid">
        <div class="form-field span-full"><label>Project Title</label><input name="project_title" class="field-input" required value="${esc(cut.project_title || "")}"></div>
        <div class="form-field"><label>Channel</label><input name="channel_id" list="cutChannelOptions" class="field-input" placeholder="@Channel" value="${esc(defaultChannel)}"><datalist id="cutChannelOptions">${channelOptions.map((c) => `<option value="${esc(c)}">`).join("")}</datalist></div>
        <div class="form-field"><label>Series</label><input name="series" class="field-input" value="${esc(cut.series || "General")}"></div>
        <div class="form-field"><label>Current Stage</label><input name="current_stage" class="field-input" value="${esc(cut.current_stage || "Main Cut Started")}"></div>
        <div class="form-field"><label>Status</label><select name="status" class="field-select">${["active", "planned", "completed", "published", "archived"].map((s) => `<option value="${s}" ${(cut.status || "active") === s ? "selected" : ""}>${s}</option>`).join("")}</select></div>
        <div class="form-field span-full"><label>Notes / Pending Checklist</label><textarea name="notes" class="field-textarea">${esc(cut.notes || "")}</textarea></div>
      </div>
      <div class="toolbar" style="justify-content:space-between;">${isEdit ? `<button type="button" class="danger-btn" id="delCutBtn">Delete Cut</button>` : `<span></span>`}<button type="submit" class="primary-btn">Save &amp; Sync Buffers</button></div>
    </form>`,
    (body, dlg) => {
      body.querySelector("#cutEditForm").addEventListener("submit", async (e) => {
        e.preventDefault();
        const payload = Object.fromEntries(new FormData(e.target).entries());
        if (isEdit) payload.cut_id = cut.cut_id;
        await apiFetch("/api/production/cuts", { method: "POST", body: payload });
        dlg.close(); showToast("Saved cut and recalculated channel buffers"); refreshCurrentView();
      });
      body.querySelector("#delCutBtn")?.addEventListener("click", async () => {
        await apiFetch(`/api/production/cuts/${encodeURIComponent(cut.cut_id)}`, { method: "DELETE" });
        dlg.close(); showToast("Deleted cut"); refreshCurrentView();
      });
    }
  );
}

export function openDraftEditor(draft) {
  openEditorModal(
    `[${draft.agent_name}] ${draft.title}`,
    `<form id="draftEditForm" class="stack-list">
      <div class="form-grid">
        <div class="form-field"><label>Title</label><input name="title" class="field-input" value="${esc(draft.title)}"></div>
        <div class="form-field"><label>Status</label><select name="status" class="field-select">${["staged", "approved", "published", "rejected"].map((s) => `<option value="${s}" ${draft.status === s ? "selected" : ""}>${s}</option>`).join("")}</select></div>
        <div class="form-field span-full"><label>Markdown Content</label><textarea name="content_md" class="field-textarea" style="min-height:280px;">${esc(draft.content_md || "")}</textarea></div>
      </div>
      <div class="toolbar" style="justify-content:flex-end;"><button type="submit" class="primary-btn">Save Draft</button></div>
    </form>`,
    (body, dlg) => {
      body.querySelector("#draftEditForm").addEventListener("submit", async (e) => {
        e.preventDefault();
        await apiFetch(`/api/production/drafts/${draft.id}`, { method: "PATCH", body: Object.fromEntries(new FormData(e.target).entries()) });
        dlg.close(); showToast("Draft updated"); refreshCurrentView();
      });
    }
  );
}

export function openGenericRowEditor(tableName, columns, primaryKeys, row) {
  const isEdit = Boolean(row);
  const fieldsHtml = (columns || []).map((c) => {
    const val = isEdit ? row[c.name] ?? "" : "", isPk = (primaryKeys || []).includes(c.name);
    const isLong = String(val).length > 90 || c.name.endsWith("_json") || c.name.endsWith("_md") || c.name === "extra_metadata" || c.name === "notes";
    return `<div class="form-field ${isLong ? "span-full" : ""}"><label>${esc(c.name)} <span class="mono" style="font-weight:400;">(${esc(c.type)}${isPk ? " · PK" : ""})</span></label>${isLong ? `<textarea name="${esc(c.name)}" class="field-textarea" ${isEdit && isPk ? "readonly" : ""}>${esc(val)}</textarea>` : `<input name="${esc(c.name)}" class="field-input" value="${esc(val)}" ${isEdit && isPk ? "readonly" : ""}>`}</div>`;
  }).join("");
  openEditorModal(
    isEdit ? `Edit ${tableName} Row` : `Insert into ${tableName}`,
    `<form id="genericRowForm" class="stack-list"><div class="form-grid">${fieldsHtml}</div><div class="toolbar" style="justify-content:space-between;">${isEdit && primaryKeys?.length ? `<button type="button" class="danger-btn" id="genericDeleteBtn">Delete Row</button>` : `<span></span>`}<button type="submit" class="primary-btn">${isEdit ? "Update Row" : "Insert Row"}</button></div></form>`,
    (body, dlg) => {
      body.querySelector("#genericRowForm").addEventListener("submit", async (e) => {
        e.preventDefault();
        const rawEntries = Object.fromEntries(new FormData(e.target).entries());
        if (isEdit) {
          const pkObj = Object.fromEntries(primaryKeys.map((k) => [k, row[k]])), updates = {};
          columns.forEach((c) => { if (!primaryKeys.includes(c.name)) updates[c.name] = rawEntries[c.name] === "" && row[c.name] === null ? null : rawEntries[c.name]; });
          await apiFetch(`/api/db/table/${encodeURIComponent(tableName)}`, { method: "PUT", body: { pk: pkObj, updates } });
          showToast(`Updated ${tableName} row`);
        } else {
          await apiFetch(`/api/db/table/${encodeURIComponent(tableName)}`, { method: "POST", body: { row: rawEntries } });
          showToast(`Inserted row into ${tableName}`);
        }
        dlg.close(); refreshCurrentView();
      });
      body.querySelector("#genericDeleteBtn")?.addEventListener("click", async () => {
        const pkObj = Object.fromEntries(primaryKeys.map((k) => [k, row[k]]));
        await apiFetch(`/api/db/table/${encodeURIComponent(tableName)}`, { method: "DELETE", body: { pk: pkObj } });
        dlg.close(); showToast(`Deleted row from ${tableName}`); refreshCurrentView();
      });
    }
  );
}

export function openOmniSearch() {
  const dlg = document.getElementById("omniDialog"), inp = document.getElementById("omniInput");
  if (dlg && !dlg.open) dlg.showModal();
  inp?.focus(); inp?.select();
}

export async function executeOmniSearch(q) {
  const box = document.getElementById("omniResults");
  if (!box) return;
  if (!q.trim()) { box.innerHTML = `<p class="muted-hint">Type any title, platform, ingredient, series, or draft keyword.</p>`; return; }
  const res = await apiFetch(`/api/search?q=${encodeURIComponent(q)}&limit=6`);
  const groups = res.groups || {}, entries = Object.entries(groups);
  if (!entries.length) { box.innerHTML = `<p class="muted-hint">No matches found for "${esc(q)}".</p>`; return; }
  box.innerHTML = entries.map(([groupName, items]) => {
    const rows = items.map((it, idx) => {
      const title = it.title || it.name || it.product_name || it.project_title || it.series_name || it.id;
      const meta = it.platform || it.console_name || it.location || it.plex_library || it.author || it.channel_id || it.status || "";
      return `<div class="list-row" style="cursor:pointer;" data-omni-group="${esc(groupName)}" data-omni-idx="${idx}"><div class="list-row-main"><div class="list-row-title">${esc(title)}</div><div class="list-row-sub">${esc(meta)}</div></div><span class="badge badge-status badge-neutral">${esc(groupName)}</span></div>`;
    }).join("");
    return `<div style="margin-bottom:12px;"><div class="kpi-label" style="margin-bottom:4px;">${esc(groupName)} (${items.length})</div><div class="stack-list">${rows}</div></div>`;
  }).join("");
  box.querySelectorAll("[data-omni-group]").forEach((rowEl) => rowEl.addEventListener("click", () => {
    const grp = rowEl.dataset.omniGroup, item = groups[grp][Number(rowEl.dataset.omniIdx)];
    document.getElementById("omniDialog")?.close();
    if (grp === "games") openGameEditor(item);
    else if (grp === "wishlist") openWishlistEditor(item);
    else if (grp === "pantry") openPantryEditor(item);
    else if (grp === "production") openCutEditor(item);
    else if (grp === "prices") { state.catalog.domain = "scout"; apiFetch(`/api/scout?title=${encodeURIComponent(item.product_name)}&platform=${encodeURIComponent(item.console_name)}`).then((sr) => { state.catalog.scoutResult = sr; switchView("catalog"); }); }
    else if (grp === "media" || grp === "books") { state.catalog.domain = grp; state.catalog.q = item.title; switchView("catalog"); }
    else switchView("production");
  }));
}

export function openPreferencesModal() {
  const dlg = document.getElementById("prefsDialog"), body = document.getElementById("prefsBody");
  if (!dlg || !body) return;
  const net = state.network || {};
  const curTheme = document.documentElement.dataset.theme === "light" ? "light" : "dark";
  const curDensity = document.documentElement.dataset.density || "compact";
  const pinBadgeCls = state.pinEnabled ? "badge-green" : "badge-neutral";
  const phoneUrls = [
    ...(net.https_urls || []).map((u) => ({ label: "Tailscale HTTPS (Recommended)", url: u, secure: true })),
    ...(net.tailscale_urls || []).map((u) => ({ label: "Tailscale HTTP", url: u, secure: false })),
    ...(net.lan_urls || []).map((u) => ({ label: "Home Wi-Fi / LAN", url: u, secure: false })),
    ...(net.hostname_url ? [{ label: "Local Hostname", url: net.hostname_url, secure: false }] : []),
  ];
  const originsList = buildCompanionOriginsList();
  body.innerHTML = `<div class="stack-list">
    <div class="card" style="background:var(--bg-card-alt);">
      <div class="card-head"><h3 class="card-title">Install on Phone &amp; Desktop</h3><div class="toolbar">${getDeferredInstallPrompt() ? `<button type="button" class="primary-btn" id="prefsInstallAppBtn">📲 Install App Now</button>` : ""}<button type="button" class="ghost-btn" id="prefsEnableHttpsBtn">🔒 Enable Tailscale HTTPS</button></div></div>
      <div class="stack-list">${phoneUrls.length ? phoneUrls.map((p) => `<div class="list-row"><div class="list-row-main"><div class="list-row-title mono">${esc(p.url)}</div><div class="list-row-sub">${esc(p.label)}</div></div><div class="toolbar">${p.secure ? `<span class="badge badge-status badge-green">HTTPS</span>` : ""}<button type="button" class="ghost-btn" data-copy-url="${esc(p.url)}">Copy URL</button></div></div>`).join("") : `<div class="list-row"><span class="mono">${esc(window.location.origin)}</span></div>`}</div>
      <div class="toolbar"><button type="button" class="primary-btn" data-copy-url="${esc(originsList)}">1. Copy Companion Origins</button><button type="button" class="ghost-btn" data-copy-url="chrome://flags/#unsafely-treat-insecure-origin-as-secure">2. Copy Chrome Flag URL</button></div>
    </div>
    <div class="form-grid">
      <div class="form-field"><label>Color Theme</label><select id="prefThemeSelect" class="field-select"><option value="dark" ${curTheme === "dark" ? "selected" : ""}>Dark (Deep Slate)</option><option value="light" ${curTheme === "light" ? "selected" : ""}>Light</option><option value="system">Match System</option></select></div>
      <div class="form-field"><label>Interface Density</label><select id="prefDensitySelect" class="field-select"><option value="compact" ${curDensity === "compact" ? "selected" : ""}>Compact</option><option value="default" ${curDensity === "default" ? "selected" : ""}>Default</option><option value="comfortable" ${curDensity === "comfortable" ? "selected" : ""}>Comfortable</option></select></div>
    </div>
    <form id="pinConfigForm" class="card" style="background:var(--bg-card-alt);">
      <div class="card-head"><h3 class="card-title">Optional Remote Device PIN</h3><div class="toolbar"><button type="button" class="chip-btn" id="prefsOpenAccountsBtn">🔑 Open Accounts &amp; API Keys</button><span class="badge badge-status ${pinBadgeCls}">${state.pinEnabled ? "PIN ACTIVE" : "NO PIN"}</span></div></div>
      <div class="toolbar"><input type="password" id="newPinInput" class="field-input grow" placeholder="Set new PIN (or leave blank to disable)"><button type="submit" class="primary-btn">Update PIN</button></div>
    </form>
  </div>`;
  body.querySelectorAll("[data-copy-url]").forEach((btn) => btn.addEventListener("click", async () => { const ok = await copyTextToClipboard(btn.dataset.copyUrl); showToast(ok ? `Copied ${btn.dataset.copyUrl}` : "Clipboard unavailable"); }));
  body.querySelector("#prefsOpenAccountsBtn")?.addEventListener("click", () => { dlg.close(); state.settings.subtab = "accounts"; switchView("settings"); });
  body.querySelector("#prefsInstallAppBtn")?.addEventListener("click", () => triggerPwaInstallPrompt());
  body.querySelector("#prefsEnableHttpsBtn")?.addEventListener("click", () => requestEnableTailscaleHttps(() => openPreferencesModal()));
  body.querySelector("#prefThemeSelect").addEventListener("change", async (e) => {
    const resolved = e.target.value === "system" ? applyColorSchemeMeta("system") : applyThemeMode(e.target.value);
    await savePreferences({ theme: resolved }, "global").catch(() => {});
    await saveConfigSettings({ themeMode: resolved }).catch(() => {});
    showToast(`Theme set to ${resolved}`);
  });
  body.querySelector("#prefDensitySelect").addEventListener("change", async (e) => {
    const resolved = applyDensityAttr(e.target.value);
    await savePreferences({ density: resolved }, "device").catch(() => {});
    await saveConfigSettings({ density: resolved }).catch(() => {});
    showToast(`Density set to ${resolved}`);
  });
  body.querySelector("#pinConfigForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const res = await apiFetch("/api/auth/pin", { method: "POST", body: { new_pin: body.querySelector("#newPinInput").value.trim(), device_id: state.deviceId } });
    state.pinEnabled = Boolean(res.pin_enabled); showToast(state.pinEnabled ? "Companion PIN updated" : "Companion PIN disabled"); openPreferencesModal();
  });
  if (!dlg.open) dlg.showModal();
}
