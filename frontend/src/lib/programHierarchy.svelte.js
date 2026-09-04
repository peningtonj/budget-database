// Shared across every consumer of program-hierarchy (ProgramPicker.svelte
// and ProgramMeasuresPage.svelte's own "suggest same-name programs"
// feature) -- same shared-module-state approach as programTray.svelte.js/
// measureTray.svelte.js (Svelte 5 runes work outside .svelte files). One
// fetch per session, not one per component mount: program-hierarchy has
// shown more trouble reaching some networks than any other endpoint in
// the app, and firing it redundantly from more than one place at once
// only doubles exposure to whatever's causing that -- confirmed a real
// bug, not a deliberate design, when ProgramMeasuresPage's own effect
// turned out to duplicate exactly what ProgramPicker already fetches on
// every "By program" visit.
import { fetchProgramHierarchy, cachedProgramHierarchy } from "./api.js";

const cached = cachedProgramHierarchy();

// One shared reactive object, mutated in place and never reassigned --
// every consumer imports this same instance by reference, so there's no
// question of whether reassigning an exported binding stays reactive
// across module boundaries (it does in Svelte 5, but mutating one
// shared object is simpler to reason about and matches this file's own
// sibling stores).
export const programHierarchyState = $state({
  programs: cached?.programs ?? [],
  outcomes: cached?.outcomes ?? {},
  loading: cached === null,
  error: null,
});

function run() {
  fetchProgramHierarchy()
    .then((data) => {
      programHierarchyState.programs = data.programs;
      programHierarchyState.outcomes = data.outcomes;
      programHierarchyState.error = null;
    })
    .catch((e) => {
      // A failed background revalidation still leaves stale cached
      // results in place -- only surface the error if there was
      // nothing to fall back on.
      if (programHierarchyState.programs.length === 0) programHierarchyState.error = e.message;
    })
    .finally(() => {
      programHierarchyState.loading = false;
    });
}

let started = false;

// Idempotent: whichever component mounts first this session actually
// kicks off the fetch; every later call (from the same or a different
// component) is a no-op, since programHierarchyState already reflects
// whatever that one fetch resolved to (or is still resolving to).
export function loadProgramHierarchyOnce() {
  if (started) return;
  started = true;
  run();
}

// Distinct from loadProgramHierarchyOnce -- an explicit "Retry" click
// re-runs even though a fetch already started, since the whole point of
// Retry is to try again after a real failure.
export function retryProgramHierarchy() {
  programHierarchyState.loading = true;
  programHierarchyState.error = null;
  run();
}
