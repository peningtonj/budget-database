// A "comparison set" a user builds up across BOTH SearchPage (checking
// results, or "select all") and MeasurePage (adding the measure being
// viewed, or one of its related-measures badges) -- so this lives as
// shared module state (Svelte 5 runes work outside .svelte files in a
// .svelte.js module), not local state owned by either page. Every
// importer sees the same array and reacts to the same changes.
//
// sessionStorage-backed, not localStorage: a comparison set is a
// same-session, ad hoc thing to build up and use once -- not a
// permanent bookmark (this page deliberately has no shareable URL of
// its own, see CombinedMeasuresPage.svelte/App.svelte).
const TRAY_CACHE_KEY = "measureTray.v1";

function loadInitial() {
  try {
    const raw = sessionStorage.getItem(TRAY_CACHE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

// Row shape: {measure_id, measure_name, edition, portfolios, agencies,
// has_financial_data} -- exactly what list/search-text/search-topic
// already return, so a tray pip renders with no extra lookup.
export const tray = $state(loadInitial());

function persist() {
  try {
    sessionStorage.setItem(TRAY_CACHE_KEY, JSON.stringify(tray));
  } catch {
    // Same as fetchMeasureList's own cache: a pure optimization, never
    // worth failing the actual add/remove over.
  }
}

export function isInTray(measureId) {
  return tray.some((m) => m.measure_id === measureId);
}

export function addToTray(row) {
  if (isInTray(row.measure_id)) return;
  tray.push(row);
  persist();
}

export function removeFromTray(measureId) {
  const idx = tray.findIndex((m) => m.measure_id === measureId);
  if (idx === -1) return;
  tray.splice(idx, 1);
  persist();
}

export function toggleInTray(row) {
  if (isInTray(row.measure_id)) {
    removeFromTray(row.measure_id);
  } else {
    addToTray(row);
  }
}

export function clearTray() {
  tray.splice(0, tray.length);
  persist();
}
