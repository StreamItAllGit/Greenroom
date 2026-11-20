/* @module static/js/tooltips.js
 * @purpose 2-line styled floating tooltip engine with 250ms hover delay and hotkey pill extraction.
 * @inputs DOM elements carrying title or data-tooltip attributes ("Title Case (Hotkey)|Sentence case explanation").
 * @outputs Viewport-clamped .gr-tooltip element with Line 1 Title Case + [Hotkey] and Line 2 Sentence case description. */

import { esc } from "./state.js";

const HOVER_DELAY_MS = 250;
let tooltipEl = null;
let hoverTimer = null;
let activeTarget = null;

const DEFAULT_SUBTITLES = {
  "toggle sidebar rail": "Collapse or expand the desktop navigation rail between 220px and 56px.",
  "queue next tick": "Enqueue the next scheduled telemetry check in the task queue.",
  "pause queue": "Pause or resume background task queue execution.",
  "suspend queue": "Temporarily suspend all background task dispatching.",
  "stop all jobs": "Cancel active jobs and clear all pending tasks in the queue.",
  "dry run mode": "Preview collector and media actions without mutating files or rows.",
  "save config snapshot": "Capture an instant rollback snapshot of your configuration.",
  "toggle live log drawer": "Open or close the docked bottom execution log console.",
  "keyboard shortcuts": "View all global keyboard shortcuts and workspace hotkeys.",
  "refresh active workspace": "Reload live data for the currently active workspace.",
  "select all visible rows": "Check every row currently displayed in the active table page.",
  "invert row selection": "Flip checked and unchecked states across visible rows.",
  "invert selection": "Flip checked and unchecked states across visible rows.",
  "export rows to csv": "Download a UTF-8 CSV file of selected or visible table records.",
  "export rows to json": "Download a formatted JSON file of selected or visible table records.",
};

function toTitleCase(str) {
  return String(str || "")
    .trim()
    .replace(/\b([a-z])/g, (m) => m.toUpperCase());
}

function toSentenceCase(str) {
  const s = String(str || "").trim();
  if (!s) return "";
  const first = s.charAt(0).toUpperCase() + s.slice(1);
  return /[.!?]$/.test(first) ? first : `${first}.`;
}

export function parseTwoLineTooltip(rawText) {
  const raw = String(rawText || "").trim();
  if (!raw) return null;

  let line1 = raw;
  let line2 = "";

  if (raw.includes("|")) {
    const parts = raw.split("|");
    line1 = parts[0].trim();
    line2 = parts.slice(1).join(" ").trim();
  } else if (raw.includes(" — ")) {
    const parts = raw.split(" — ");
    line1 = parts[0].trim();
    line2 = parts.slice(1).join(" — ").trim();
  } else if (raw.includes(": ")) {
    const parts = raw.split(": ");
    line1 = parts[0].trim();
    line2 = parts.slice(1).join(": ").trim();
  }

  let hotkey = "";
  const hkMatch = line1.match(/\((Ctrl\s*\+\s*[^)]+|\?|[1-8])\)|\[(Ctrl\s*\+\s*[^\]]+|\?|[1-8])\]/i);
  if (hkMatch) {
    hotkey = (hkMatch[1] || hkMatch[2] || "").trim();
    line1 = line1.replace(hkMatch[0], "").trim();
  }

  const numPrefix = line1.match(/^([1-8])\.\s+(.+)$/);
  if (numPrefix && !hotkey) {
    hotkey = numPrefix[1];
    line1 = numPrefix[2].trim();
  }

  if (!line2) {
    const lookupKey = line1.toLowerCase();
    line2 =
      DEFAULT_SUBTITLES[lookupKey] ||
      `Execute or inspect ${line1.toLowerCase()} in the current workspace.`;
  }

  return {
    title: toTitleCase(line1),
    hotkey,
    subtitle: toSentenceCase(line2),
  };
}

function ensureTooltipElement() {
  if (tooltipEl && document.body.contains(tooltipEl)) return tooltipEl;
  tooltipEl = document.createElement("div");
  tooltipEl.className = "gr-tooltip";
  tooltipEl.id = "grFloatingTooltip";
  tooltipEl.setAttribute("role", "tooltip");
  tooltipEl.hidden = true;
  document.body.appendChild(tooltipEl);
  return tooltipEl;
}

function positionTooltip(target) {
  const el = ensureTooltipElement();
  const rect = target.getBoundingClientRect();
  const tipRect = el.getBoundingClientRect();
  const margin = 8;

  let top = rect.bottom + margin;
  if (top + tipRect.height > window.innerHeight - margin) {
    top = Math.max(margin, rect.top - tipRect.height - margin);
  }

  let left = rect.left + rect.width / 2 - tipRect.width / 2;
  left = Math.max(margin, Math.min(left, window.innerWidth - tipRect.width - margin));

  el.style.top = `${Math.round(top)}px`;
  el.style.left = `${Math.round(left)}px`;
}

function showTooltipFor(target) {
  const raw = target.getAttribute("data-tooltip") || target.getAttribute("data-gr-title") || target.getAttribute("title");
  if (!raw) return;
  if (target.hasAttribute("title")) {
    target.setAttribute("data-gr-title", target.getAttribute("title"));
    target.removeAttribute("title");
  }
  const parsed = parseTwoLineTooltip(raw);
  if (!parsed) return;

  const el = ensureTooltipElement();
  el.innerHTML = `
    <div class="gr-tooltip-title">
      <span>${esc(parsed.title)}</span>
      ${parsed.hotkey ? `<kbd class="tooltip-kbd">${esc(parsed.hotkey)}</kbd>` : ""}
    </div>
    <div class="gr-tooltip-sub">${esc(parsed.subtitle)}</div>
  `;
  el.hidden = false;
  positionTooltip(target);
}

export function hideTooltip() {
  clearTimeout(hoverTimer);
  hoverTimer = null;
  activeTarget = null;
  if (tooltipEl) {
    tooltipEl.hidden = true;
  }
}

export function refreshTooltips(root = document) {
  root.querySelectorAll("[title]").forEach((el) => {
    const t = el.getAttribute("title");
    if (t) {
      el.setAttribute("data-gr-title", t);
      if (!el.hasAttribute("data-tooltip")) {
        el.setAttribute("data-tooltip", t);
      }
      el.removeAttribute("title");
    }
  });
}

export function initTooltips() {
  ensureTooltipElement();
  refreshTooltips(document);

  const findTooltipTarget = (node) => {
    if (!node || typeof node.closest !== "function") return null;
    return node.closest("[data-tooltip], [data-gr-title], [title]");
  };

  document.addEventListener("pointerover", (e) => {
    const target = findTooltipTarget(e.target);
    if (!target) return;
    if (target.hasAttribute("title")) {
      refreshTooltips(target.parentElement || document);
    }
    if (target === activeTarget) return;
    clearTimeout(hoverTimer);
    activeTarget = target;
    hoverTimer = setTimeout(() => {
      if (activeTarget === target) showTooltipFor(target);
    }, HOVER_DELAY_MS);
  });

  document.addEventListener("pointerout", (e) => {
    if (!activeTarget) return;
    const related = e.relatedTarget;
    if (related && activeTarget.contains(related)) return;
    hideTooltip();
  });

  document.addEventListener("focusin", (e) => {
    const target = findTooltipTarget(e.target);
    if (!target) return;
    clearTimeout(hoverTimer);
    activeTarget = target;
    hoverTimer = setTimeout(() => {
      if (activeTarget === target) showTooltipFor(target);
    }, HOVER_DELAY_MS);
  });

  document.addEventListener("focusout", () => hideTooltip());
  document.addEventListener("pointerdown", () => hideTooltip());
  window.addEventListener("scroll", () => hideTooltip(), { passive: true });
}
