<script>
  import { fetchMeasureList, cachedMeasureList, fetchMeasureTextSearch, fetchMeasureTopicSearch } from "./api.js";
  import MultiSelectFilter from "./MultiSelectFilter.svelte";

  let { onselect } = $props();

  // Stale-while-revalidate (see fetchMeasureList/cachedMeasureList's own
  // docstrings): a cached copy, if any, populates `measures` synchronously
  // on first render -- Name-mode search and the filters are usable
  // immediately, not blocked behind a slow/proxied round trip -- while the
  // real fetch below quietly confirms or replaces it. Only a true first-
  // ever visit (no cache yet) sees `measuresLoading` with nothing to show.
  const cached = cachedMeasureList();
  let measures = $state(cached ?? []);
  let measuresLoading = $state(cached === null);
  let measuresError = $state(null);

  $effect(() => {
    fetchMeasureList()
      .then((m) => {
        measures = m;
        measuresError = null;
      })
      .catch((e) => {
        // A failed background revalidation still leaves stale cached
        // results on screen -- only surface the error if there was
        // nothing to fall back on.
        if (measures.length === 0) measuresError = e.message;
      })
      .finally(() => {
        measuresLoading = false;
      });
  });

  /** @type {"name" | "text" | "topic"} */
  let mode = $state("name");
  // Shared across both modes -- switching modes keeps whatever's typed
  // and just changes how it's used, which reads as one continuous
  // search rather than two separate ones. Same for the two filters
  // below: an edition/portfolio picked while in one mode stays applied
  // when switching to another, since "measures in the 2024-25 Budget"
  // is a filter on the result set, not something specific to how the
  // query itself is matched.
  let query = $state("");
  let selectedPortfolios = $state([]);
  let selectedEditions = $state([]);

  const RESULT_CAP = 100;
  const TEXT_SEARCH_DEBOUNCE_MS = 300;

  let isServerMode = $derived(mode === "text" || mode === "topic");

  function portfolioOptions(measures) {
    // A handful of measures (2017-18 MYEFO, ~78 of them) carry an empty-
    // string portfolio -- filtered here rather than upstream, since it's
    // a real gap in that source data (not something to silently paper
    // over server-side), but showing it as a selectable blank pip in
    // this dropdown would be confusing either way.
    // localeCompare, not the default sort(): a few portfolio names are
    // still ALL CAPS (a genuinely different, older era's own name, not
    // a duplicate -- see _canon_portfolio's own docstring in views.py),
    // and a plain case-sensitive sort scatters those away from their
    // alphabetical neighbours.
    return [...new Set(measures.flatMap((m) => m.portfolios))]
      .filter(Boolean)
      .sort((a, b) => a.localeCompare(b));
  }

  // Most-recent-first, not alphabetical -- someone filtering by budget
  // round almost always wants a recent one, and alphabetical sort
  // scatters "2024-25 Budget" and "2024-25 MYEFO" away from each other
  // once there are 10+ years in the list. Within one fiscal year, MYEFO
  // (December) sorts after its own Budget (May); 2022-23's one-off
  // second Budget (October, following the May change of government)
  // sorts after the March one the same way.
  function editionSortKey(edition) {
    const year = parseInt(edition.match(/(\d{4})-\d{2}/)?.[1] ?? "0", 10);
    const subOrder = /myefo/i.test(edition) ? 2 : /october/i.test(edition) ? 1 : 0;
    return year * 10 + subOrder;
  }

  function editionOptions(measures) {
    return [...new Set(measures.map((m) => m.edition))].sort((a, b) => editionSortKey(b) - editionSortKey(a));
  }

  function matchesFilters(m, selectedPortfolios, selectedEditions) {
    if (selectedPortfolios.length && !selectedPortfolios.some((p) => m.portfolios.includes(p))) return false;
    if (selectedEditions.length && !selectedEditions.includes(m.edition)) return false;
    return true;
  }

  function filterByName(measures, query, selectedPortfolios, selectedEditions) {
    const q = query.trim().toLowerCase();
    return measures.filter((m) => {
      if (q && !m.measure_name.toLowerCase().includes(q)) return false;
      return matchesFilters(m, selectedPortfolios, selectedEditions);
    });
  }

  function filterServerResults(results, selectedPortfolios, selectedEditions) {
    return results.filter((m) => matchesFilters(m, selectedPortfolios, selectedEditions));
  }

  // Text and topic search are both a server round-trip per query (see
  // fetchMeasureTextSearch's/fetchMeasureTopicSearch's own docstrings
  // for why, unlike name search which filters the one fetched list
  // client-side) -- debounced so it fires once typing pauses, not on
  // every keystroke. Shared state: only one of the two is ever active
  // at a time (mode is a single toggle), so there's nothing to keep
  // separate between them.
  const SERVER_SEARCH_FETCHERS = { text: fetchMeasureTextSearch, topic: fetchMeasureTopicSearch };
  let serverResults = $state([]);
  let serverLoading = $state(false);
  let serverError = $state(null);

  $effect(() => {
    const q = query.trim();
    const fetcher = SERVER_SEARCH_FETCHERS[mode];
    if (!fetcher || !q) {
      serverResults = [];
      serverLoading = false;
      serverError = null;
      return;
    }
    serverLoading = true;
    serverError = null;
    const handle = setTimeout(async () => {
      try {
        serverResults = await fetcher(q);
      } catch (e) {
        serverError = e.message;
      } finally {
        serverLoading = false;
      }
    }, TEXT_SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(handle);
  });
</script>

<div class="page">
  <header>
    <h1>Measures</h1>
    <p class="section-note">
      Covers every ingested Budget/MYEFO edition, 2015-16 through 2025-26.
      Most editions have full per-agency $ impact/program data ("financial
      data" below); a few older/partial ones show the Budget Paper No. 2
      write-up alone (see KNOWN_GAPS.md).
    </p>
  </header>

  <div class="mode-toggle">
    <button class:active={mode === "name"} onclick={() => (mode = "name")}>
      Measure name
    </button>
    <button class:active={mode === "text"} onclick={() => (mode = "text")}>
      Measure text
    </button>
    <button class:active={mode === "topic"} onclick={() => (mode = "topic")}>
      Topic
    </button>
  </div>

  <div class="controls">
    <input
      class="query"
      type="text"
      placeholder={mode === "name"
        ? "Search measure name…"
        : mode === "text"
          ? "Search measure text…"
          : "Search by topic, e.g. “child care”…"}
      bind:value={query}
    />
  </div>

  <div class="filters">
    <MultiSelectFilter
      label="Budget round"
      placeholder="Add a budget round…"
      options={editionOptions(measures)}
      bind:selected={selectedEditions}
    />
    <MultiSelectFilter
      label="Portfolio"
      placeholder="Add a portfolio…"
      options={portfolioOptions(measures)}
      bind:selected={selectedPortfolios}
    />
  </div>

  {#if mode === "topic"}
    <p class="section-note topic-note">
      Matches measures by what they're about, not just exact wording — e.g.
      "child care" also surfaces measures that only say "Early Childhood
      Education and Care (ECEC)".
    </p>
  {/if}

  {#if mode === "name" && measures.length === 0}
    {#if measuresLoading}
      <p class="status">Loading measures…</p>
    {:else if measuresError}
      <p class="status error">{measuresError}</p>
    {:else}
      <p class="status">No measures match.</p>
    {/if}
  {:else if isServerMode && !query.trim()}
    <p class="status">
      {mode === "text" ? "Type to search the measure write-ups themselves." : "Type a topic to search for."}
    </p>
  {:else}
    {@const displayResults =
      mode === "name"
        ? filterByName(measures, query, selectedPortfolios, selectedEditions)
        : filterServerResults(serverResults, selectedPortfolios, selectedEditions)}
    <p class="count">
      {#if isServerMode && serverLoading}
        Searching…
      {:else}
        {displayResults.length} measure{displayResults.length === 1 ? "" : "s"}
        {#if displayResults.length > RESULT_CAP}
          (showing first {RESULT_CAP} — refine your search)
        {/if}
      {/if}
    </p>
    {#if isServerMode && serverError}
      <p class="status error">{serverError}</p>
    {:else}
      <ul class="results">
        {#each displayResults.slice(0, RESULT_CAP) as m (m.measure_id)}
          <li>
            <button class="result" onclick={() => onselect(m.measure_id, m.measure_name, m.edition)}>
              <span class="name">{m.measure_name}</span>
              {#if m.snippet}
                <p class="snippet">{m.snippet}</p>
              {/if}
              <span class="meta">
                <span class="edition-badge">{m.edition}</span>
                {#if !m.has_financial_data}
                  <span class="text-only-badge">text only</span>
                {/if}
                {#if mode === "topic" && m.score != null}
                  <span class="score-badge">{Math.round(m.score * 100)}% match</span>
                {/if}
                {#each m.portfolios as p}
                  <span class="portfolio-badge">{p}</span>
                {/each}
              </span>
            </button>
          </li>
        {/each}
      </ul>
      {#if displayResults.length === 0 && !(isServerMode && serverLoading)}
        <p class="status">No measures match.</p>
      {/if}
    {/if}
  {/if}
</div>

<style>
  .page {
    max-width: 860px;
    margin: 0 auto;
    padding: 2rem 1.5rem 4rem;
  }
  header {
    margin-bottom: 1.5rem;
  }
  h1 {
    font-size: 1.6rem;
    margin: 0 0 0.4rem;
  }
  .section-note {
    font-size: 0.82rem;
    color: var(--text-muted);
    margin: 0;
  }
  .mode-toggle {
    display: inline-flex;
    gap: 0.2rem;
    padding: 0.2rem;
    margin-bottom: 0.75rem;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--surface-accent);
  }
  .mode-toggle button {
    font: inherit;
    font-size: 0.82rem;
    padding: 0.3rem 0.7rem;
    border: none;
    border-radius: 6px;
    background: none;
    color: var(--text-muted);
    cursor: pointer;
  }
  .mode-toggle button.active {
    background: var(--surface);
    color: var(--text-h);
    box-shadow: 0 1px 2px rgba(0, 0, 0, 0.08);
  }
  .controls {
    display: flex;
    gap: 0.6rem;
    margin-bottom: 0.75rem;
  }
  .filters {
    display: flex;
    flex-wrap: wrap;
    gap: 1.25rem;
    margin-bottom: 1rem;
  }
  .filters > :global(.multiselect) {
    flex: 1 1 220px;
  }
  .query {
    flex: 1;
    font: inherit;
    font-size: 0.95rem;
    padding: 0.5rem 0.75rem;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--surface);
    color: var(--text);
  }
  .query:focus {
    outline: 2px solid var(--text-h);
    outline-offset: -1px;
  }
  .count {
    font-size: 0.8rem;
    color: var(--text-muted);
    margin: 0 0 0.6rem;
  }
  .results {
    list-style: none;
    margin: 0;
    padding: 0;
    border-top: 1px solid var(--border-faint);
  }
  .result {
    display: flex;
    flex-direction: column;
    gap: 0.3rem;
    width: 100%;
    text-align: left;
    font: inherit;
    padding: 0.7rem 0.4rem;
    border: none;
    border-bottom: 1px solid var(--border-faint);
    background: none;
    cursor: pointer;
    color: var(--text);
    transition: background 0.15s;
  }
  .result:hover {
    background: var(--surface-accent);
  }
  .name {
    font-size: 0.95rem;
    color: var(--text-h);
  }
  .snippet {
    margin: 0;
    font-size: 0.82rem;
    font-style: italic;
    color: var(--text-muted);
    line-height: 1.4;
  }
  .meta {
    display: flex;
    flex-wrap: wrap;
    gap: 0.35rem;
  }
  .edition-badge {
    font-size: 0.7rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.03em;
    color: var(--text-muted);
  }
  .portfolio-badge {
    font-size: 0.72rem;
    padding: 0.1rem 0.5rem;
    border-radius: 999px;
    background: var(--surface-accent);
    color: var(--text-muted);
    border: 1px solid var(--border);
  }
  .text-only-badge {
    font-size: 0.7rem;
    font-style: italic;
    color: var(--text-muted);
  }
  .score-badge {
    font-size: 0.7rem;
    padding: 0.1rem 0.5rem;
    border-radius: 999px;
    background: var(--surface-accent);
    color: var(--text-muted);
    border: 1px solid var(--border);
  }
  .topic-note {
    margin-bottom: 0.9rem;
  }
  .status {
    padding: 3rem 0;
    text-align: center;
    color: var(--text-muted);
  }
  .status.error {
    color: #b91c1c;
  }
</style>
