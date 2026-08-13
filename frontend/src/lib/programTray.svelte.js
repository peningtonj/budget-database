// The set of programs a user has picked via the Portfolio -> Agency ->
// Outcome -> Program cascading selector, to view their combined measures
// on one shared graph -- same shared-module-state approach as
// measureTray.svelte.js (Svelte 5 runes work outside .svelte files), and
// same sessionStorage scope (an ad hoc, same-session set to build up and
// use once, not a permanent bookmark -- ProgramMeasuresPage has no
// shareable URL of its own either).
const PROGRAM_TRAY_CACHE_KEY = "programTray.v1";

function loadInitial() {
  try {
    const raw = sessionStorage.getItem(PROGRAM_TRAY_CACHE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

// Row shape: {program_name, portfolio, agency, outcome_number,
// outcome_description, program_number} -- one row from
// fetchProgramHierarchy(). Keyed by (portfolio, program_name), NOT
// program_name alone: confirmed in practice that the same program name
// can legitimately appear under two different portfolio eras (a real
// machinery-of-government rename, e.g. "Education and Training" ->
// "Education") that a user wants to treat as two distinct selections --
// keying on program_name alone made the second one look "already
// added" and block-able. portfolio is part of the identity everywhere
// this tray is read, mirroring measures_by_program()'s own (program_
// name, portfolio) key on the backend.
export const programTray = $state(loadInitial());

function trayKey(portfolio, programName) {
  return `${portfolio}␟${programName}`;
}

function persist() {
  try {
    sessionStorage.setItem(PROGRAM_TRAY_CACHE_KEY, JSON.stringify(programTray));
  } catch {
    // Pure optimization, same as measureTray's own -- never worth
    // failing the actual add/remove over.
  }
}

export function isInProgramTray(portfolio, programName) {
  const key = trayKey(portfolio, programName);
  return programTray.some((p) => trayKey(p.portfolio, p.program_name) === key);
}

export function addToProgramTray(row) {
  if (isInProgramTray(row.portfolio, row.program_name)) return;
  programTray.push(row);
  persist();
}

export function removeFromProgramTray(portfolio, programName) {
  const key = trayKey(portfolio, programName);
  const idx = programTray.findIndex((p) => trayKey(p.portfolio, p.program_name) === key);
  if (idx === -1) return;
  programTray.splice(idx, 1);
  persist();
}

export function clearProgramTray() {
  programTray.splice(0, programTray.length);
  persist();
}
