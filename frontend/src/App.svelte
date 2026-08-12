<script>
  import { onMount } from "svelte";
  import SearchPage from "./lib/SearchPage.svelte";
  import MeasurePage from "./lib/MeasurePage.svelte";
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
  // True while resolving a ?m=<id> URL (on initial load or browser
  // back/forward) -- avoids flashing the search page before the id
  // actually resolves, since that's now an async round-trip rather than
  // a synchronous read of the URL's own name+edition.
  let resolving = $state(!!readMeasureIdFromLocation());

  async function syncFromLocation() {
    const id = readMeasureIdFromLocation();
    if (!id) {
      selected = null;
      resolving = false;
      return;
    }
    resolving = true;
    const resolved = await resolveMeasureId(id);
    // A stale/unknown id (e.g. a link from before a rebuild) falls back
    // to the search page rather than showing an error.
    selected = resolved ? { name: resolved.measure_name, edition: resolved.edition } : null;
    resolving = false;
  }

  function selectMeasure(id, name, edition) {
    selected = { name, edition };
    history.pushState({ id }, "", `?${new URLSearchParams({ m: id })}`);
  }

  function backToSearch() {
    selected = null;
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
  <button class="back" onclick={backToSearch}>← Back to search</button>
  <MeasurePage name={selected.name} edition={selected.edition} onselect={selectMeasure} />
{:else}
  <SearchPage onselect={selectMeasure} />
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
  .status {
    padding: 3rem 0;
    text-align: center;
    color: var(--text-muted);
  }
</style>
