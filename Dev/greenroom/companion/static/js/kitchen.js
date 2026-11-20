/* @module static/js/kitchen.js
 * @purpose View 3: Kitchen, Pantry & Daily Macros workspace with barcode lookup, restock copy, and quick stock mutations.
 * @inputs state.kitchen and /api/kitchen/* REST endpoints.
 * @outputs Rendered Kitchen workspace DOM, domain-disabled guard card, and macro/pantry event handlers. */

import { state, esc, fmtNum, showToast, copyTextToClipboard, tableLabelStyle, switchView, refreshCurrentView } from "./state.js";
import { apiFetch } from "./api.js";
import { openPantryEditor } from "./modals.js";
import { refreshTooltips } from "./tooltips.js";

export function renderKitchen(root) {
  const k = state.kitchen.data;
  if (!k || !root) return;
  if (k.status === "domain_disabled") {
    root.innerHTML = `<section class="card"><div class="card-head"><h2 class="card-title">Kitchen Module Disabled</h2><span class="badge badge-status badge-neutral">DISABLED</span></div><p class="muted-hint">${esc(k.message || "Enable the Kitchen & Macro Planner module in Settings to track pantry stock and daily macros. Your existing database rows are preserved.")}</p><div class="toolbar"><button type="button" class="primary-btn" id="kitchenEnableModBtn">Open Modules &amp; Profiles</button></div></section>`;
    root.querySelector("#kitchenEnableModBtn")?.addEventListener("click", () => { state.settings.subtab = "modules"; switchView("settings"); });
    return;
  }

  const macro = k.macro_status || {};
  const targets = macro.targets || {};
  const targetCal = Number(targets.caloriesKcal ?? targets.calories ?? 2000);
  const targetPro = Number(targets.proteinGrams ?? targets.protein_g ?? 150);
  const logged = macro.consumed || macro.logged || {};
  const loggedCal = Number(logged.caloriesKcal ?? logged.calories ?? 0);
  const loggedPro = Number(logged.proteinGrams ?? logged.protein_g ?? 0);
  const runway = macro.remaining_runway || { calories: Math.max(0, targetCal - loggedCal), protein_g: Math.max(0, targetPro - loggedPro) };

  const locTabs = ["", "Fridge", "Freezer", "Pantry"]
    .map((loc) => `<button type="button" class="chip-btn ${state.kitchen.location === loc ? "active" : ""}" data-kit-loc="${loc}">${loc || "All Locations"}</button>`)
    .join("");

  const statusTabs = [["available", "Available"], ["In Stock", "In Stock"], ["Low Stock", "Low Stock"], ["Depleted", "Depleted"], ["", "All Items"]]
    .map(([val, label]) => `<button type="button" class="chip-btn ${state.kitchen.stock_status === val ? "active" : ""}" data-kit-status="${val}">${label}</button>`)
    .join("");

  const headers = ["Item", "Location", "Quantity", "Status", "Waste Tier", "Quick Actions"];
  const itemRows = (k.items || []).map((it) => {
    const st = it.stock_status || "In Stock";
    const badgeCls = st === "In Stock" ? "badge-green" : st === "Low Stock" ? "badge-yellow" : "badge-red";
    return `<tr data-pantry-id="${it.id}"><th scope="row">${esc(it.name)}</th><td>${esc(it.location)}</td><td class="num">${esc(it.quantity)} ${esc(it.unit || "")}</td><td><span class="badge badge-status ${badgeCls}">${esc(st)}</span></td><td>${esc(it.waste_tier || "—")}</td><td class="no-row-open"><div class="toolbar"><button type="button" class="ghost-btn" data-pantry-delta="-1" data-id="${it.id}" title="Consume One Unit|Subtract 1 unit from current pantry stock">−1</button><button type="button" class="ghost-btn" data-pantry-delta="1" data-id="${it.id}" title="Add One Unit|Add 1 unit to current pantry stock">+1</button><button type="button" class="ghost-btn" data-pantry-st="Low Stock" data-id="${it.id}">Mark Low</button><button type="button" class="ghost-btn" data-pantry-st="${st === "Depleted" ? "In Stock" : "Depleted"}" data-id="${it.id}">${st === "Depleted" ? "Restock" : "Deplete"}</button></div></td></tr>`;
  }).join("");

  root.innerHTML = `
    <div class="dash-grid">
      <section class="card">
        <div class="card-head">
          <h2 class="card-title">Daily Macro Runway (${esc(macro.date || "Today")})</h2>
          <span class="badge badge-status badge-blue">${esc(macro.schedule_type || "STANDARD DAILY")}</span>
        </div>
        <div class="kpi-grid">
          <div class="list-row"><div class="list-row-main"><div class="list-row-sub">Calories Left</div><strong class="num" style="font-size:20px;">${fmtNum(runway.calories)}</strong><div class="list-row-sub num">${fmtNum(loggedCal)} / ${fmtNum(targetCal)} kcal</div></div></div>
          <div class="list-row"><div class="list-row-main"><div class="list-row-sub">Protein Left</div><strong class="num" style="font-size:20px;">${fmtNum(runway.protein_g)}g</strong><div class="list-row-sub num">${fmtNum(loggedPro)}g / ${fmtNum(targetPro)}g</div></div></div>
        </div>
        <form id="macroLogForm" class="toolbar">
          <input type="number" step="1" name="calories" class="field-input" placeholder="Calories" style="width:92px;" required>
          <input type="number" step="0.5" name="protein_g" class="field-input" placeholder="Protein (g)" style="width:96px;">
          <input type="number" step="0.5" name="carbs_g" class="field-input" placeholder="Carbs (g)" style="width:92px;">
          <input type="number" step="0.5" name="fat_g" class="field-input" placeholder="Fat (g)" style="width:84px;">
          <input type="text" name="notes" class="field-input grow" placeholder="Meal notes (e.g. Lunch wrap)">
          <button type="submit" class="primary-btn">+ Log Macros</button>
        </form>
      </section>

      <section class="card">
        <div class="card-head">
          <h2 class="card-title">Add Pantry Item / Barcode Lookup</h2>
          <button type="button" class="ghost-btn" id="copyRestockBtn">📋 Copy Restock (${(k.restock_items || []).length})</button>
        </div>
        <form id="addPantryForm" class="toolbar">
          <input type="text" name="name" class="field-input grow" placeholder="Item name (optional if barcode scanned)">
          <input type="text" name="barcode" class="field-input" placeholder="UPC / Barcode (optional)" style="width:168px;">
          <select name="location" class="field-select"><option value="Fridge">Fridge</option><option value="Pantry">Pantry</option><option value="Freezer">Freezer</option></select>
          <input type="number" step="0.5" name="quantity" class="field-input" value="1" style="width:72px;">
          <button type="submit" class="primary-btn">+ Add Item</button>
        </form>
        <div class="list-row-sub">Restock preview: ${esc((k.restock_items || []).slice(0, 3).join(" · ") || "All staples are currently stocked.")}</div>
      </section>
    </div>

    <div class="toolbar" style="justify-content:space-between;">
      <div class="subtabs">${locTabs}</div>
      <div class="subtabs">${statusTabs}</div>
    </div>

    <form id="kitSearchForm" class="toolbar">
      <input type="search" id="kitSearchInput" class="field-input grow" placeholder="Search pantry items, categories, and notes..." value="${esc(state.kitchen.q)}">
      <button type="submit" class="ghost-btn">Filter Items</button>
    </form>

    <div tabindex="0" role="region" aria-label="Pantry Inventory" class="table-wrapper contain-panel">
      <table class="data-table" style="${esc(tableLabelStyle(headers))}">
        <thead><tr>${headers.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead>
        <tbody>${itemRows || `<tr><td colspan="6" class="muted-hint">No matching pantry items found.</td></tr>`}</tbody>
      </table>
    </div>`;

  refreshTooltips(root);
  root.querySelectorAll("[data-kit-loc]").forEach((b) => b.addEventListener("click", () => { state.kitchen.location = b.dataset.kitLoc; refreshCurrentView(); }));
  root.querySelectorAll("[data-kit-status]").forEach((b) => b.addEventListener("click", () => { state.kitchen.stock_status = b.dataset.kitStatus; refreshCurrentView(); }));
  root.querySelector("#kitSearchForm")?.addEventListener("submit", (e) => { e.preventDefault(); state.kitchen.q = root.querySelector("#kitSearchInput").value.trim(); refreshCurrentView(); });
  root.querySelector("#macroLogForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    await apiFetch("/api/kitchen/nutrition", { method: "POST", body: Object.fromEntries(new FormData(e.target).entries()) });
    showToast("Logged macro entry");
    refreshCurrentView();
  });
  root.querySelector("#addPantryForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const created = await apiFetch("/api/kitchen/pantry", { method: "POST", body: Object.fromEntries(new FormData(e.target).entries()) });
    showToast(`Added ${created.name}`);
    refreshCurrentView();
  });
  root.querySelector("#copyRestockBtn")?.addEventListener("click", async () => {
    const text = (k.restock_items || []).join("\n");
    if (!text) return showToast("Restock list is empty");
    const ok = await copyTextToClipboard(text);
    showToast(ok ? `Copied ${(k.restock_items || []).length} restock tasks to clipboard` : "Clipboard unavailable");
  });
  root.querySelectorAll("[data-pantry-delta]").forEach((btn) => btn.addEventListener("click", async (e) => {
    e.stopPropagation();
    await apiFetch(`/api/kitchen/pantry/${btn.dataset.id}`, { method: "PATCH", body: { quantity_delta: Number(btn.dataset.pantryDelta) } });
    refreshCurrentView();
  }));
  root.querySelectorAll("[data-pantry-st]").forEach((btn) => btn.addEventListener("click", async (e) => {
    e.stopPropagation();
    await apiFetch(`/api/kitchen/pantry/${btn.dataset.id}`, { method: "PATCH", body: { stock_status: btn.dataset.pantrySt } });
    showToast(`Updated status to ${btn.dataset.pantrySt}`);
    refreshCurrentView();
  }));
  root.querySelectorAll("tbody tr[data-pantry-id]").forEach((tr) => tr.addEventListener("click", (e) => {
    if (e.target.closest(".no-row-open")) return;
    const item = (k.items || []).find((x) => x.id === Number(tr.dataset.pantryId));
    if (item) openPantryEditor(item);
  }));
}
