<script>
  import { fetchMeasureDetail } from "./api.js";
  import MeasureChart from "./MeasureChart.svelte";
  import AgencyProgramChart from "./AgencyProgramChart.svelte";
  import DrilldownChart from "./DrilldownChart.svelte";
  import ImpactTable from "./ImpactTable.svelte";
  import ProgramsTouched from "./ProgramsTouched.svelte";
  import MeasureText from "./MeasureText.svelte";

  let { name, edition, onselect } = $props();

  // null means there's no measure_impacts/measure_programs row for this
  // (name, edition) -- true for 18 of the 21 ingested BP2 editions (see
  // measure_list()'s own docstring); not an error, just "no $ breakdown
  // to show", handled below by falling back to a text-only layout.
  let detailPromise = $derived(fetchMeasureDetail(name, edition));

  /**
   * @typedef {{type: "portfolio", portfolio: string}
   *   | {type: "agency", agency: string, portfolio: string | undefined}} Selection
   */

  // Clicking a portfolio or agency badge replaces whichever drilldown
  // chart is showing -- only one at a time, directly under the badges.
  /** @type {Selection | null} */
  let selected = $state(null);

  /** @param {string} portfolio */
  function selectPortfolio(portfolio) {
    selected = { type: "portfolio", portfolio };
  }

  // An agency badge needs its portfolio too (agency_outcome_profile scopes
  // by both -- a bare agency abbreviation isn't safe to trust alone, see
  // the backend's own docstring) -- looked up from whichever of
  // detail.programs/detail.impacts mentions that agency first.
  /**
   * @param {string} agency
   * @param {{programs: {agency: string, portfolio: string}[], impacts: {agency: string, portfolio: string}[]}} detail
   */
  function selectAgency(agency, detail) {
    const hit =
      detail.programs.find((p) => p.agency === agency) ??
      detail.impacts.find((i) => i.agency === agency);
    selected = { type: "agency", agency, portfolio: hit?.portfolio };
  }
</script>

<div class="page">
  {#await detailPromise}
    <p class="status">Loading…</p>
  {:then detail}
    {#if detail}
      <header>
        <span class="edition-badge">{detail.edition}</span>
        <h1>{detail.measure_name}</h1>
        <div class="badges">
          {#each detail.portfolios as p}
            <button
              class="badge portfolio"
              class:active={selected?.type === "portfolio" && selected.portfolio === p}
              onclick={() => selectPortfolio(p)}
            >
              {p}
            </button>
          {/each}
          {#each detail.agencies as a}
            <button
              class="badge agency"
              class:active={selected?.type === "agency" && selected.agency === a}
              onclick={() => selectAgency(a, detail)}
            >
              {a}
            </button>
          {/each}
        </div>
      </header>

      <div class="columns">
        <div class="col-text">
          <section>
            <h2>Impact by fiscal year</h2>
            <ImpactTable impacts={detail.impacts} />
          </section>

          <MeasureText {name} edition={detail.edition} {onselect} />

          <section>
            <h2>Programs touched</h2>
            <ProgramsTouched programs={detail.programs} />
          </section>
        </div>

        <div class="col-graphs">
          {#if selected}
            <section>
              <h2>
                {#if selected.type === "portfolio"}
                  {selected.portfolio} — agency profiles
                {:else}
                  {selected.agency} — outcome profiles
                {/if}
              </h2>
              <p class="section-note">
                Estimated actuals for past years, budget/forward estimates from the
                latest Budget for years still ahead. A renamed or restructured
                agency shows as a shorter series rather than a guessed-at
                continuous one.
              </p>
              {#if selected.type === "portfolio"}
                <DrilldownChart mode="portfolio" portfolio={selected.portfolio} edition={detail.edition} />
              {:else if selected.portfolio}
                <DrilldownChart
                  mode="agency"
                  agency={selected.agency}
                  portfolio={selected.portfolio}
                  edition={detail.edition}
                />
              {:else}
                <p class="status">Couldn't determine {selected.agency}'s portfolio.</p>
              {/if}
            </section>
          {/if}

          <section>
            <h2>Program profiles</h2>
            <p class="section-note">
              Estimated actuals for past years, budget/forward estimates from the
              latest Budget for years still ahead. Solid squares mark the
              measure's own contribution.
            </p>
            <AgencyProgramChart programs={detail.programs} impacts={detail.impacts} />
          </section>
        </div>
      </div>
    {:else}
      <!-- No measure_impacts/measure_programs row for this (name, edition)
           -- a BP2-only edition (18 of 21 ingested, see measure_list()'s
           own docstring). Text-only layout: no $ breakdown, no charts,
           no drilldown (those all need PBS agency data this measure
           doesn't have). -->
      <header>
        <span class="edition-badge">{edition}</span>
        <h1>{name}</h1>
      </header>
      <div class="col-text text-only">
        <MeasureText {name} {edition} hasFinancialData={false} {onselect} />
      </div>
    {/if}
  {:catch error}
    <p class="status error">{error.message}</p>
  {/await}
</div>

<style>
  .page {
    max-width: 860px;
    margin: 0 auto;
    padding: 2rem 1.5rem 4rem;
  }
  header {
    margin-bottom: 2rem;
  }
  .edition-badge {
    display: inline-block;
    font-size: 0.75rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--text-muted);
    margin-bottom: 0.5rem;
  }
  h1 {
    font-size: 1.6rem;
    line-height: 1.3;
    margin: 0 0 0.75rem;
  }
  .badges {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
  }
  .badge {
    font: inherit;
    font-size: 0.8rem;
    padding: 0.2rem 0.6rem;
    border-radius: 999px;
    border: 1px solid var(--border);
    background: var(--surface);
    color: var(--text);
    cursor: pointer;
    transition: background 0.15s, border-color 0.15s;
  }
  .badge:hover {
    border-color: var(--text-muted);
  }
  .badge.portfolio {
    background: var(--surface-accent);
  }
  .badge.active {
    background: var(--text-h);
    border-color: var(--text-h);
    color: var(--bg);
  }
  section {
    margin-bottom: 2.5rem;
  }
  .section-note {
    font-size: 0.82rem;
    color: var(--text-muted);
    margin: -0.4rem 0 1rem;
  }
  h2 {
    font-size: 1rem;
    text-transform: uppercase;
    letter-spacing: 0.03em;
    color: var(--text-muted);
    margin-bottom: 0.75rem;
  }
  .status {
    padding: 3rem 0;
    text-align: center;
    color: var(--text-muted);
  }
  .status.error {
    color: #b91c1c;
  }
  .columns {
    display: block;
  }
  /* No second column of charts to balance against for a text-only
     (BP2-only, no PBS data) measure -- stays at the single-column
     width rather than stretching to the two-column page's 1280px. */
  .text-only {
    max-width: 860px;
  }

  /* "At least a laptop" -- below this, stacked single column reads better
     than two cramped ones. */
  @media (min-width: 1024px) {
    .page {
      max-width: 1280px;
    }
    .columns {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
      gap: 0 2.5rem;
      align-items: start;
    }
  }
</style>
