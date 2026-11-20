/* @module static/js/comparator_copies.js
 * @purpose Multi-copy mismatch rendering, copy reconciliation actions, and PriceCharting/DAT upload dialogs for Comparator.
 * @inputs Copy mismatch bucket rows, /api/catalog/games/reconcile-copies, /api/catalog/import-pricecharting-owned, and /api/dats/import.
 * @outputs Copy mismatch table rows HTML, inline reconciliation event handlers, and CSV/DAT import modal editors. */

import { state, esc, fmtMoney, showToast, refreshCurrentView } from "./state.js";
import { apiFetch } from "./api.js";
import { openEditorModal } from "./modals.js";

export function renderCopyMismatchBucketRows(rows = []) {
  return rows
    .map((r, idx) => {
      const counts = r.service_counts || r.serviceCounts || {};
      const leftCnt = r.left_copies ?? counts.infinitebacklog ?? 0;
      const rightCnt = r.right_copies ?? counts.pricecharting ?? 0;
      const maxCnt = Math.max(1, Number(leftCnt || 0), Number(rightCnt || 0), Number(r.max_copies || 1));
      const bkpCnt = Number(r.personal_backup_count ?? r.personalBackupCount ?? 0);
      return `<tr>
        <th scope="row">${esc(r.title)} ${r.version_edition ? `<span class="badge badge-neutral">${esc(r.version_edition)}</span>` : ""}</th>
        <td>${esc(r.platform)}</td>
        <td class="num"><span class="badge badge-blue">Set A ×${leftCnt}</span> vs <span class="badge badge-yellow">Set B ×${rightCnt}</span></td>
        <td class="num">${bkpCnt > 0 ? `<span class="badge badge-green">💾 Backup ×${bkpCnt}</span>` : `<span class="muted-hint">0</span>`}</td>
        <td class="num">${r.loose_price ? `Loose ${fmtMoney(r.loose_price)}` : "—"} ${r.cib_price ? `· CIB ${fmtMoney(r.cib_price)}` : ""}</td>
        <td class="no-row-open">
          <div class="toolbar" style="gap:4px;">
            <button type="button" class="primary-btn" data-reconcile-idx="${idx}" data-reconcile-count="${maxCnt}">✓ Sync to ×${maxCnt}</button>
            <button type="button" class="ghost-btn" data-reconcile-idx="${idx}" data-reconcile-count="${maxCnt + 1}">+1 Copy</button>
          </div>
        </td>
      </tr>`;
    })
    .join("");
}

export function bindCopyReconcileControls(root, bucketRows = []) {
  if (!root) return;
  root.querySelectorAll("[data-reconcile-idx]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const row = bucketRows[Number(btn.dataset.reconcileIdx)];
      if (!row) return;
      const targetCount = Math.max(1, Number(btn.dataset.reconcileCount || 1));
      const gameId = row.game_id || row.id || "";
      await apiFetch("/api/catalog/games/reconcile-copies", {
        method: "POST",
        body: { game_id: gameId, service: "infinitebacklog", copy_count: targetCount, sync_all_tracked: true },
      });
      state.catalog.cacheByKey = {};
      showToast(`Reconciled '${row.title}' copy counts to ×${targetCount}`);
      refreshCurrentView();
    });
  });
}

export function openImportPriceChartingOwnedModal() {
  openEditorModal(
    "Import PriceCharting Owned Collection CSV",
    `<form id="pcOwnedImportForm" class="stack-list">
      <p class="muted-hint">Imports games you own from PriceCharting CSV exports with multi-copy tracking.</p>
      <div class="form-field"><label>Select PriceCharting Collection CSV File</label><input type="file" id="pcCsvFileInput" accept=".csv,text/csv" class="field-input"></div>
      <div class="form-field"><label>Or Paste CSV Text Directly</label><textarea id="pcCsvTextarea" class="field-textarea mono" placeholder="id,console-name,product-name,condition..."></textarea></div>
      <div class="toolbar" style="justify-content:space-between;">
        <button type="button" class="ghost-btn" id="pcImportFromDropBtn">Scan drops/manual_csv/</button>
        <button type="submit" class="primary-btn">Import Owned Collection</button>
      </div>
    </form>`,
    (body, dlg) => {
      const fileInp = body.querySelector("#pcCsvFileInput"), textArea = body.querySelector("#pcCsvTextarea");
      fileInp?.addEventListener("change", async () => { const f = fileInp.files?.[0]; if (f) textArea.value = await f.text(); });
      body.querySelector("#pcImportFromDropBtn")?.addEventListener("click", async () => {
        const res = await apiFetch("/api/catalog/import-pricecharting-owned", { method: "POST", body: {} });
        dlg.close(); showToast(`Imported ${res.imported || 0} PriceCharting owned games`); state.catalog.cacheByKey = {}; refreshCurrentView();
      });
      body.querySelector("#pcOwnedImportForm").addEventListener("submit", async (e) => {
        e.preventDefault();
        const csvText = textArea.value.trim();
        if (!csvText) return showToast("Select a CSV file or paste CSV content first.");
        const res = await apiFetch("/api/catalog/import-pricecharting-owned", { method: "POST", body: { csv_text: csvText } });
        dlg.close(); showToast(`Imported ${res.imported || 0} PriceCharting owned games`); state.catalog.cacheByKey = {}; refreshCurrentView();
      });
    }
  );
}

export function openImportDatModal() {
  openEditorModal(
    "Import No-Intro / Redump .DAT or .XML Catalog",
    `<form id="datImportForm" class="stack-list">
      <p class="muted-hint">Upload any official No-Intro or Redump Logiqx XML or ClrMamePro <code>.dat</code> / <code>.xml</code> file.</p>
      <div class="form-field"><label>Select .DAT or .XML File</label><input type="file" id="datFileInput" accept=".dat,.xml,text/xml" class="field-input"></div>
      <div class="form-field"><label>Or Paste .DAT / XML Content</label><textarea id="datTextInput" class="field-textarea mono" style="min-height:180px;"></textarea></div>
      <div class="toolbar" style="justify-content:flex-end;"><button type="submit" class="primary-btn">Import Known-Good Hashes</button></div>
    </form>`,
    (body, dlg) => {
      let chosenFilename = "uploaded.dat";
      const fileInp = body.querySelector("#datFileInput"), textArea = body.querySelector("#datTextInput");
      fileInp?.addEventListener("change", async () => { const f = fileInp.files?.[0]; if (f) { chosenFilename = f.name; textArea.value = await f.text(); } });
      body.querySelector("#datImportForm").addEventListener("submit", async (e) => {
        e.preventDefault();
        const datText = textArea.value.trim();
        if (!datText) return showToast("Select a .dat/.xml file first.");
        const res = await apiFetch("/api/dats/import", { method: "POST", body: { filename: chosenFilename, dat_text: datText } });
        dlg.close(); showToast(`Imported ${res.entries_imported || 0} hashes for ${res.platform} (${res.dat_source})`); refreshCurrentView();
      });
    }
  );
}
