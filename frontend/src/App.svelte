<script>
  import { onMount } from "svelte";
  import SearchPage from "./lib/SearchPage.svelte";
  import MeasurePage from "./lib/MeasurePage.svelte";
  import CombinedMeasuresPage from "./lib/CombinedMeasuresPage.svelte";
  import { resolveMeasureId } from "./lib/api.js";

  const DEFAULT_TITLE = document.title;

  // Short shareable URL: ?m=<8-digit measure_id>, a query param (not a
  // path segment) so it works without any server-side rewrite rule --
  // the app is just served as Vite's single index.html.
  function readMeasureIdFromLocation() {
    return new URLSearchParams(window.location.search).get("m");
  }

  /** @type {{name: string, edition: string} | null} */
  let selected = $state(null);
  // A snapshot of ids being compared -- entered via SearchPage's own
  // "Compare selected measures" button, never reflected in the URL.
  // Unlike `selected`, this comparison set has no bookmarkable identity
  // of its own (it's an ad hoc set the user just built up, not a stable
  // thing worth sharing a link to) -- so it's plain in-memory state, the
  // same way MeasurePage's own drilldown toggle is, not address-bar state.
  let selectedSet = $state(null);
  // The comparison set (ids) a measure was navigated away from, e.g. by
  // clicking a card in CombinedMeasuresPage's own sidebar -- lets a
  // "back to comparison" button return there without rebuilding the
  // selection, since the summary view itself has no URL of its own to
  // fall back to (see selectedSet's own note). Persists across further
  // measure-to-measure navigation (e.g. following a related-measure
  // badge), so the breadcrumb back to the summary you started from
  // isn't lost after one hop.
  let cameFromSet = $state(null);
  // True while resolving a ?m=<id> URL (on initial load or browser
  // back/forward) -- avoids flashing the search page before the id
  // actually resolves, since that's now an async round-trip rather than
  // a synchronous read of the URL's own name+edition.
  let resolving = $state(!!readMeasureIdFromLocation());

  async function syncFromLocation() {
    const id = readMeasureIdFromLocation();
    if (!id) {
      selected = null;
      cameFromSet = null;
      resolving = false;
      return;
    }
    resolving = true;
    const resolved = await resolveMeasureId(id);
    // A stale/unknown id (e.g. a link from before a rebuild) falls back
    // to the search page rather than showing an error.
    selected = resolved ? { name: resolved.measure_name, edition: resolved.edition } : null;
    selectedSet = null;
    resolving = false;
  }

  function selectMeasure(id, name, edition) {
    // Leaving the comparison view for a single measure -- remember it so
    // a "back to comparison" button can return without rebuilding the
    // selection. Already-set cameFromSet (mid-breadcrumb, e.g. hopping
    // between related measures) is left alone rather than cleared here.
    if (selectedSet) {
      cameFromSet = selectedSet;
    }
    selected = { name, edition };
    selectedSet = null;
    history.pushState({ id }, "", `?${new URLSearchParams({ m: id })}`);
  }

  function viewSet(ids) {
    selectedSet = ids;
    selected = null;
    cameFromSet = null;
  }

  function backToComparison() {
    selectedSet = cameFromSet;
    selected = null;
    history.pushState({}, "", window.location.pathname);
  }

  function backToSearch() {
    selected = null;
    selectedSet = null;
    cameFromSet = null;
    history.pushState({}, "", window.location.pathname);
  }

  onMount(() => {
    syncFromLocation();
    window.addEventListener("popstate", syncFromLocation);
    return () => window.removeEventListener("popstate", syncFromLocation);
  });

  $effect(() => {
    document.title = selected ? `${selected.name} — ${DEFAULT_TITLE}` : DEFAULT_TITLE;
  });
</script>

{#if resolving}
  <p class="status">Loading…</p>
{:else if selected}
  <div class="back-row">
    {#if cameFromSet}
      <button class="back" onclick={backToComparison}>← Back to comparison</button>
    {/if}
    <button class="back" onclick={backToSearch}>← Back to search</button>
  </div>
  <MeasurePage name={selected.name} edition={selected.edition} onselect={selectMeasure} />
{:else if selectedSet}
  <button class="back" onclick={backToSearch}>← Back to search</button>
  {#key selectedSet}
    <!-- Remounts on every new "Compare" click (viewSet always assigns a
         fresh array) so CombinedMeasuresPage's own currentIds/opened
         state -- captured once at mount, not live-bound to the ids prop
         -- always starts fresh for the newly compared set, including
         when the user is already on this page and adds more measures
         before comparing again. -->
    <CombinedMeasuresPage ids={selectedSet} onselect={selectMeasure} onBack={backToSearch} />
  {/key}
{:else}
  <SearchPage onselect={selectMeasure} onViewSet={viewSet} />
{/if}

<style>
  /* MeasurePage's own .page already handles width/centering/padding --
     this only adds the back button above it, aligned to the same
     left edge via matching horizontal padding. */
  .back-row {
    display: flex;
    gap: 1.25rem;
    max-width: 1280px;
    margin: 1rem auto 0;
    padding: 0 1.5rem;
  }
  .back-row .back {
    max-width: none;
    margin: 0;
    padding: 0;
  }
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
  .status {
    padding: 3rem 0;
    text-align: center;
    color: var(--text-muted);
  }
</style>
