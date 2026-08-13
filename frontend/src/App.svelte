<script>
  import { onMount } from "svelte";
  import SearchPage from "./lib/SearchPage.svelte";
  import MeasurePage from "./lib/MeasurePage.svelte";
  import CombinedMeasuresPage from "./lib/CombinedMeasuresPage.svelte";
  import ProgramMeasuresPage from "./lib/ProgramMeasuresPage.svelte";
  import ProgramDeepDivePage from "./lib/ProgramDeepDivePage.svelte";
  import { resolveMeasureId } from "./lib/api.js";

  const DEFAULT_TITLE = document.title;

  // Short shareable URL: ?m=<8-digit measure_id>, a query param (not a
  // path segment) so it works without any server-side rewrite rule --
  // the app is just served as Vite's single index.html.
  function readMeasureIdFromLocation() {
    return new URLSearchParams(window.location.search).get("m");
  }

  // Every page navigated to since the last time the user was back at
  // search, in order -- a single generic "back" button always pops
  // exactly one entry, returning to the exact previous screen with the
  // exact data it had (however many hops deep: measure -> related
  // measure -> related measure -> ..., or search -> program summary ->
  // deep dive, or any mix). An empty stack means "show search". Each
  // entry is a plain {type, ...} descriptor -- see the push* functions
  // below for the shape per type. Like every other summary view in this
  // app, none of this is reflected in the URL except the topmost
  // measure entry (kept in sync for shareability -- see selectMeasure/
  // goBack below), the same "ad hoc, not a bookmark" treatment
  // selectedSet/selectedPrograms always used, just generalised to a
  // full stack instead of one-level-deep pairs.
  let viewStack = $state([]);
  let currentView = $derived(viewStack[viewStack.length - 1] ?? null);

  // True while resolving a ?m=<id> URL (on initial load or browser
  // back/forward) -- avoids flashing the search page before the id
  // actually resolves, since that's now an async round-trip rather than
  // a synchronous read of the URL's own name+edition.
  let resolving = $state(!!readMeasureIdFromLocation());

  async function syncFromLocation() {
    const id = readMeasureIdFromLocation();
    if (!id) {
      viewStack = [];
      resolving = false;
      return;
    }
    resolving = true;
    const resolved = await resolveMeasureId(id);
    // A stale/unknown id (e.g. a link from before a rebuild) falls back
    // to the search page rather than showing an error.
    viewStack = resolved
      ? [{ type: "measure", id, name: resolved.measure_name, edition: resolved.edition }]
      : [];
    resolving = false;
  }

  function selectMeasure(id, name, edition) {
    viewStack.push({ type: "measure", id, name, edition });
    history.pushState({ id }, "", `?${new URLSearchParams({ m: id })}`);
  }

  function viewSet(ids) {
    viewStack.push({ type: "set", ids });
  }

  function viewPrograms(selections) {
    viewStack.push({ type: "programs", selections });
  }

  function viewProgramDeepDive(programName) {
    viewStack.push({ type: "deepDive", programName });
  }

  // Pops exactly one level -- the single back button used everywhere.
  // Keeps the URL in sync with whatever's now on top (a measure's own
  // ?m=, or a clean URL for anything else/empty), the same invariant
  // selectMeasure's own push maintains, so address-bar state never goes
  // stale relative to what's actually on screen.
  function goBack() {
    viewStack.pop();
    const top = viewStack[viewStack.length - 1];
    if (top?.type === "measure") {
      history.pushState({ id: top.id }, "", `?${new URLSearchParams({ m: top.id })}`);
    } else {
      history.pushState({}, "", window.location.pathname);
    }
  }

  // What the back button's own label names -- the screen it's about to
  // return to, not the current one, so it reads like a real breadcrumb
  // ("← Back to search", "← Back to Child Care Subsidy") rather than a
  // generic "← Back" everywhere.
  function backLabel() {
    const prev = viewStack[viewStack.length - 2];
    if (!prev) return "search";
    if (prev.type === "measure") return prev.name;
    if (prev.type === "deepDive") return "program history";
    return "summary";
  }

  onMount(() => {
    syncFromLocation();
    window.addEventListener("popstate", syncFromLocation);
    return () => window.removeEventListener("popstate", syncFromLocation);
  });

  $effect(() => {
    document.title = currentView?.type === "measure" ? `${currentView.name} — ${DEFAULT_TITLE}` : DEFAULT_TITLE;
  });
</script>

{#if resolving}
  <p class="status">Loading…</p>
{:else}
  {#if currentView}
    <button class="back" onclick={goBack}>← Back to {backLabel()}</button>
  {/if}

  {#if currentView?.type === "measure"}
    {#key currentView}
      <MeasurePage
        name={currentView.name}
        edition={currentView.edition}
        onselect={selectMeasure}
        onDeepDive={viewProgramDeepDive}
      />
    {/key}
  {:else if currentView?.type === "set"}
    {#key currentView}
      <!-- Remounts on every new "Summarise" click (viewSet always pushes a
           fresh entry) so CombinedMeasuresPage's own currentIds/opened
           state -- captured once at mount, not live-bound to the ids prop
           -- always starts fresh for the newly summarised set, including
           when the user is already on this page and adds more measures
           before summarising again. -->
      <CombinedMeasuresPage
        ids={currentView.ids}
        onselect={selectMeasure}
        onBack={goBack}
        onDeepDive={viewProgramDeepDive}
      />
    {/key}
  {:else if currentView?.type === "programs"}
    {#key currentView}
      <!-- Remounts on every new "Summarise" click from ProgramPicker, same
           reasoning as CombinedMeasuresPage's own {#key} above. -->
      <ProgramMeasuresPage
        programSelections={currentView.selections}
        onselect={selectMeasure}
        onBack={goBack}
        onDeepDive={viewProgramDeepDive}
      />
    {/key}
  {:else if currentView?.type === "deepDive"}
    {#key currentView}
      <ProgramDeepDivePage programName={currentView.programName} />
    {/key}
  {/if}

  <!-- Always mounted, never torn down -- CSS-hidden (not {#if}'d away)
       whenever another view is on top, so its own state (mode, query
       text, filters, fetched results) survives a trip through any
       number of other pages and is exactly as left when the stack
       empties back out to it. Mounting it fresh every return, the way
       every other view here remounts via {#key}, is exactly what would
       lose that state -- this is the one view that's deliberately NOT
       remounted. -->
  <div class:hidden={!!currentView}>
    <SearchPage onselect={selectMeasure} onViewSet={viewSet} onViewPrograms={viewPrograms} />
  </div>
{/if}

<style>
  /* MeasurePage's own .page already handles width/centering/padding --
     this only adds the back button above it, aligned to the same
     left edge via matching horizontal padding. */
  .back {
    display: block;
    max-width: 1280px;
    margin: 1rem auto 0;
    padding: 0 1.5rem;
    font: inherit;
    font-size: 0.85rem;
    border: none;
    background: none;
    color: var(--text-muted);
    cursor: pointer;
  }
  .back:hover {
    color: var(--text-h);
  }
  .hidden {
    display: none;
  }
  .status {
    padding: 3rem 0;
    text-align: center;
    color: var(--text-muted);
  }
</style>
