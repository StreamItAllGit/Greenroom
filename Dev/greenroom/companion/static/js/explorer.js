/* @module static/js/explorer.js
 * @purpose View 7: Universal SQLite Database Explorer with sorting, filtering, CSV/JSON export, and row CRUD.
 * @inputs state.db and /api/db/table/* REST endpoints.
 * @outputs Rendered Database Explorer table view and atomic snapshot controls. */

import { state, esc, fmtNum, showToast, yieldToMain, tableLabelStyle, dbCacheKey, exportRowsAsCsv, exportRowsAsJson, refreshCurrentView } from "./state.js";
import { apiFetch } from "./api.js";
import { openGenericRowEditor } from "./modals.js";
import { refreshTooltips } from "./tooltips.js";

export async function loadDbTablePage() {
  const key = dbCacheKey();
  const qs = new URLSearchParams({
    q: state.db.q,
    sort_by: state.db.sort_by,
    sort_dir: state.db.sort_dir,
    limit: String(state.db.limit),
    offset: String(state.db.offset),
    include_tables: state.db.tables.length ? "0" : "1",
  });
  const res = await apiFetch(`/api/db/table/${encodeURIComponent(state.db.selectedTable)}?${qs}`);
  if (res.tables?.length) state.db.tables = res.tables;
  state.db.cacheByKey[key] = res;
  state.db.page = res;
}

export function renderDatabaseExplorer(root, renderSkeletonFn) {
  const pg = state.db.page;
  if (!pg || !root) return;
  const cols = pg.columns || [];
  const visCols = cols.slice(0, 7);
  const headers = ["☑", ...visCols.map((c) => c.name)];

  const tableOptions = (state.db.tables || [])
    .map((t) => `<option value="${esc(t.name)}" ${t.name === state.db.selectedTable ? "selected" : ""}>[${esc(t.group)}] ${esc(t.label)} (${fmtNum(t.row_count)})</option>`)
    .join("");

  const sortOptions = cols
    .map((c) => `<option value="${esc(c.name)}" ${c.name === pg.sort_by ? "selected" : ""}>Sort: ${esc(c.name)}</option>`)
    .join("");

  const rowsHtml = (pg.rows || [])
    .map((row, rIdx) => {
      const checked = state.db.selectedIndices?.has(rIdx) ? "checked" : "";
      const cells = visCols
        .map((c, cIdx) => {
          const raw = row[c.name];
          const text = raw === null || raw === undefined ? "—" : String(raw).length > 72 ? `${String(raw).slice(0, 72)}…` : String(raw);
          return cIdx === 0 ? `<th scope="row" class="mono">${esc(text)}</th>` : `<td>${esc(text)}</td>`;
        })
        .join("");
      return `<tr data-db-idx="${rIdx}"><td class="no-row-open" style="width:32px;"><input type="checkbox" data-db-check="${rIdx}" ${checked}></td>${cells}</tr>`;
    })
    .join("");

  const startNum = pg.total === 0 ? 0 : pg.offset + 1;
  const endNum = Math.min(pg.total, pg.offset + pg.rows.length);

  const thCells = [
    `<th>☑</th>`,
    ...visCols.map((c) => {
      const isSorted = pg.sort_by === c.name;
      const arrow = isSorted ? (pg.sort_dir === "desc" ? "▼" : "▲") : "⇅";
      return `<th class="sortable-th" data-db-sort="${esc(c.name)}">${esc(c.name)} <span class="sort-indicator">${arrow}</span></th>`;
    }),
  ].join("");

  root.innerHTML = `
    <div class="toolbar" style="justify-content:space-between;">
      <div class="toolbar grow">
        <select id="dbTableSelect" class="field-select">${tableOptions}</select>
        <form id="dbFilterForm" class="toolbar grow">
          <input type="search" id="dbSearchInput" class="field-input grow" placeholder="Search ${esc(pg.table)}..." value="${esc(state.db.q)}">
          <select id="dbSortSelect" class="field-select">${sortOptions}</select>
          <select id="dbDirSelect" class="field-select">
            <option value="desc" ${pg.sort_dir === "desc" ? "selected" : ""}>DESC</option>
            <option value="asc" ${pg.sort_dir === "asc" ? "selected" : ""}>ASC</option>
          </select>
          <button type="submit" class="ghost-btn">Apply Filter</button>
        </form>
      </div>
      <div class="toolbar">
        <button type="button" class="ghost-btn" id="dbSelectAllBtn" title="Select All Visible Rows|Select all visible rows on this page">☑ All</button>
        <button type="button" class="ghost-btn" id="dbInvertSelBtn" title="Invert Selection|Flip checked rows on this page">⇄ Invert</button>
        <button type="button" class="ghost-btn" id="dbExportCsvBtn" title="Export Rows To CSV|Export rows to UTF-8 CSV">📥 CSV</button>
        <button type="button" class="ghost-btn" id="dbExportJsonBtn" title="Export Rows To JSON|Export rows to formatted JSON">📥 JSON</button>
        <button type="button" class="primary-btn" id="dbInsertBtn">+ Insert Row</button>
        <button type="button" class="ghost-btn" id="dbSnapshotBtn">🛡 Atomic Snapshot</button>
      </div>
    </div>

    <div tabindex="0" role="region" aria-labelledby="dbExplorerCaption" class="table-wrapper contain-panel">
      <table class="data-table" style="${esc(tableLabelStyle(headers))}">
        <caption id="dbExplorerCaption">${esc(pg.label)} (${esc(pg.table)}) — ${fmtNum(startNum)}–${fmtNum(endNum)} of ${fmtNum(pg.total)} rows · Click headers to sort or click a row to edit</caption>
        <thead><tr>${thCells}</tr></thead>
        <tbody>${rowsHtml || `<tr><td colspan="${Math.max(1, headers.length)}" class="muted-hint">Table has no matching rows.</td></tr>`}</tbody>
      </table>
    </div>

    <div class="toolbar" style="justify-content:space-between;">
      <span class="muted-hint num">${fmtNum(startNum)}–${fmtNum(endNum)} of ${fmtNum(pg.total)}</span>
      <div class="toolbar">
        <button type="button" class="ghost-btn" id="dbPrevBtn" ${pg.offset <= 0 ? "disabled" : ""}>← Prev</button>
        <button type="button" class="ghost-btn" id="dbNextBtn" ${pg.offset + pg.limit >= pg.total ? "disabled" : ""}>Next →</button>
      </div>
    </div>
  `;

  refreshTooltips(root);

  root.querySelectorAll("[data-db-sort]").forEach((th) => th.addEventListener("click", () => {
    const colName = th.dataset.dbSort;
    if (state.db.sort_by === colName) state.db.sort_dir = state.db.sort_dir === "asc" ? "desc" : "asc";
    else { state.db.sort_by = colName; state.db.sort_dir = "asc"; }
    state.db.offset = 0; refreshCurrentView();
  }));
  root.querySelectorAll("[data-db-check]").forEach((cb) => cb.addEventListener("change", () => {
    const idx = Number(cb.dataset.dbCheck);
    if (cb.checked) state.db.selectedIndices.add(idx); else state.db.selectedIndices.delete(idx);
  }));
  root.querySelector("#dbSelectAllBtn")?.addEventListener("click", () => { (pg.rows || []).forEach((_, idx) => state.db.selectedIndices.add(idx)); renderDatabaseExplorer(root, renderSkeletonFn); });
  root.querySelector("#dbInvertSelBtn")?.addEventListener("click", () => { (pg.rows || []).forEach((_, idx) => state.db.selectedIndices.has(idx) ? state.db.selectedIndices.delete(idx) : state.db.selectedIndices.add(idx)); renderDatabaseExplorer(root, renderSkeletonFn); });
  const getDbExportRows = () => (state.db.selectedIndices?.size > 0 ? (pg.rows || []).filter((_, idx) => state.db.selectedIndices.has(idx)) : pg.rows || []);
  root.querySelector("#dbExportCsvBtn")?.addEventListener("click", () => exportRowsAsCsv(`greenroom_${pg.table}`, getDbExportRows()));
  root.querySelector("#dbExportJsonBtn")?.addEventListener("click", () => exportRowsAsJson(`greenroom_${pg.table}`, getDbExportRows()));
  root.querySelector("#dbTableSelect").addEventListener("change", async (e) => {
    state.db.selectedTable = e.target.value; state.db.q = ""; state.db.sort_by = ""; state.db.offset = 0; state.db.selectedIndices.clear();
    const cached = state.db.cacheByKey[dbCacheKey()];
    if (cached) { state.db.page = cached; renderDatabaseExplorer(root, renderSkeletonFn); await yieldToMain(); refreshCurrentView({ background: true }).catch(() => {}); }
    else { if (renderSkeletonFn) renderSkeletonFn(root, `Loading ${state.db.selectedTable}...`); await yieldToMain(); refreshCurrentView({ background: false }); }
  });
  root.querySelector("#dbFilterForm").addEventListener("submit", (e) => {
    e.preventDefault();
    state.db.q = root.querySelector("#dbSearchInput").value.trim();
    state.db.sort_by = root.querySelector("#dbSortSelect").value;
    state.db.sort_dir = root.querySelector("#dbDirSelect").value;
    state.db.offset = 0; refreshCurrentView();
  });
  root.querySelector("#dbPrevBtn")?.addEventListener("click", () => { state.db.offset = Math.max(0, state.db.offset - state.db.limit); refreshCurrentView(); });
  root.querySelector("#dbNextBtn")?.addEventListener("click", () => { state.db.offset += state.db.limit; refreshCurrentView(); });
  root.querySelector("#dbSnapshotBtn").addEventListener("click", async () => { const snap = await apiFetch("/api/db/backup", { method: "POST" }); showToast(`Snapshot created: ${snap.backup_file}`); });
  root.querySelector("#dbInsertBtn").addEventListener("click", () => openGenericRowEditor(pg.table, pg.columns, pg.primary_keys, null));
  root.querySelectorAll("tbody tr[data-db-idx]").forEach((tr) => tr.addEventListener("click", (e) => {
    if (e.target.closest(".no-row-open")) return;
    const row = pg.rows[Number(tr.dataset.dbIdx)];
    if (row) openGenericRowEditor(pg.table, pg.columns, pg.primary_keys, row);
  }));
}
