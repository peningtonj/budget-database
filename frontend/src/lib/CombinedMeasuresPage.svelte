<script>
  import * as d3 from "d3";
  import { untrack } from "svelte";
  import { fetchMeasureCombined } from "./api.js";
  import { removeFromTray } from "./measureTray.svelte.js";
  import { formatDollars, formatMillionsCell } from "./format.js";
  import AgencyProgramChart from "./AgencyProgramChart.svelte";
  import CombinedProgramsTouched from "./CombinedProgramsTouched.svelte";

  // ids: a snapshot of the tray's ids at the moment "Summarise" was
  // clicked (see SearchPage.svelte's own summarise button/App.svelte's
  // viewSet) -- fetched once here;
  // removing a measure below only filters the already-fetched response,
  // it doesn't change `ids` or trigger a re-fetch.
  let { ids, onselect, onBack, onDeepDive } = $props();

  let combinedPromise = $derived(fetchMeasureCombined(ids));

  // Deliberately a one-time snapshot, not live-bound to `ids` -- App.svelte
  // remounts this whole component (via {#key selectedSet}) on every new
  // "Summarise" click, so this only ever needs to capture its own initial
  // value once per mount.
  let currentIds = $state(untrack(() => [...ids]));

  function removeMeasure(id) {
    currentIds = currentIds.filter((i) => i !== id);
    removeFromTray(id);
    if (currentIds.length === 0) onBack();
  }

  // A single headline $ figure per measure card -- payments and receipts
  // netted together so every card, including receipt-only ones, always
  // has one number to show. The full payment/receipt breakdown by
  // fiscal year is still available on the measure's own page.
  function totalImpact(impacts) {
    return d3.sum(impacts, (i) => i.amount_thousands);
  }

  const DIRECTION_LABEL = { payment: "Payments", receipt: "Related receipts" };

  // One row per direction, fiscal-year columns, summed across every
  // selected measure and agency -- the same PBS-style $m convention
  // ImpactTable uses for one measure's own Total row, just with no
  // per-agency breakdown (that's what "Programs touched" is for).
  function combinedImpactTable(allImpacts) {
    const fiscalYears = [...new Set(allImpacts.map((i) => i.fiscal_year))].sort();
    const directions = [...new Set(allImpacts.map((i) => i.direction))].sort();
    const rows = directions.map((direction) => {
      const directionRows = allImpacts.filter((i) => i.direction === direction);
      const cells = fiscalYears.map((fy) =>
        d3.sum(directionRows.filter((r) => r.fiscal_year === fy), (r) => r.amount_thousands),
      );
      return { direction, cells };
    });
    return { fiscalYears, rows };
  }

  // The agency that received the largest combined $ (by absolute value,
  // so a large net cost-saving/receipt-side measure still counts as
  // "high impact") across every currently-selected measure -- the
  // prominent program chart below defaults to this agency's own
  // programs, per the page's own "biggest impact" requirement.
  function dominantAgency(allImpacts) {
    const byAgency = d3.rollup(
      allImpacts,
      (v) => d3.sum(v, (i) => i.amount_thousands),
      (i) => i.agency,
    );
    let best = null;
    let bestAbs = -Infinity;
    for (const [agency, total] of byAgency) {
      if (Math.abs(total) > bestAbs) {
        bestAbs = Math.abs(total);
        best = agency;
      }
    }
    return best;
  }
</script>

<div class="page">
  {#await combinedPromise}
    <p class="status">Loading…</p>
  {:then combined}
    {@const visible = combined.measures.filter((m) => currentIds.includes(m.measure_id))}
    {@const editions = [...new Set(visible.map((m) => m.edition))]}
    {@const allImpacts = visible.flatMap((m) => m.impacts)}
    {@const mergedPrograms = visible.flatMap((m) => m.programs)}
    {@const agency = dominantAgency(allImpacts)}
    {@const impactTable = combinedImpactTable(allImpacts)}
    {@const sortedByImpact = [...visible].sort(
      (a, b) => Math.abs(totalImpact(b.impacts)) - Math.abs(totalImpact(a.impacts)),
    )}

    <header>
      <h1>Summarising {visible.length} measures</h1>
      {#if editions.length > 1}
        <p class="section-note">Spanning editions: {editions.join(", ")}</p>
      {/if}
      {#if combined.not_found.length}
        <p class="section-note error">
          {combined.not_found.length} selected measure{combined.not_found.length === 1 ? "" : "s"}
          couldn't be found and {combined.not_found.length === 1 ? "was" : "were"} left out.
        </p>
      {/if}
    </header>

    <div class="layout">
      <aside class="sidebar">
        <h2>Selected measures</h2>
        <p class="section-note">Sorted by absolute $ impact, largest first.</p>
        <div class="card-list">
          {#each sortedByImpact as m (m.measure_id)}
            <div class="measure-card">
              <button
                type="button"
                class="remove"
                onclick={() => removeMeasure(m.measure_id)}
                aria-label={`Remove ${m.measure_name} from summary`}
              >
                ×
              </button>
              <div class="impact">
                {m.has_financial_data ? formatDollars(totalImpact(m.impacts)) : "No $ data"}
              </div>
              <button
                type="button"
                class="name"
                onclick={() => onselect(m.measure_id, m.measure_name, m.edition)}
              >
                {m.measure_name}
              </button>
              <div class="round">{m.edition}</div>
              <div class="portfolios">
                {#each m.portfolios as p}
                  <span class="portfolio-badge">{p}</span>
                {/each}
              </div>
            </div>
          {/each}
        </div>
      </aside>

      <div class="main-col">
        <section>
          <h2>Combined $ impact</h2>
          <p class="section-note">Summed across all {visible.length} measures and their agencies.</p>
          {#if impactTable.rows.length === 0}
            <p class="status">No $ data for the selected measures.</p>
          {:else}
            <table class="combined-impact">
              <thead>
                <tr>
                  <th></th>
                  {#each impactTable.fiscalYears as fy}
                    <th class="num">{fy}</th>
                  {/each}
                </tr>
              </thead>
              <tbody>
                {#each impactTable.rows as row}
                  <tr class:receipts={row.direction === "receipt"}>
                    <td>{DIRECTION_LABEL[row.direction] ?? row.direction} ($m)</td>
                    {#each row.cells as cell}
                      <td class="num">{formatMillionsCell(cell)}</td>
                    {/each}
                  </tr>
                {/each}
              </tbody>
            </table>
          {/if}
        </section>

        <section>
          <h2>Program profiles — {agency}</h2>
          <p class="section-note">
            Defaulting to {agency}, which received the largest combined $ impact across your
            selected measures. Estimated actuals for past years, budget/forward estimates from
            the latest Budget for years still ahead.
          </p>
          <AgencyProgramChart programs={mergedPrograms} impacts={allImpacts} defaultAgency={agency} {onDeepDive} />
        </section>

        <section>
          <details>
            <summary><h2>Programs touched</h2></summary>
            <CombinedProgramsTouched measures={visible} />
          </details>
        </section>
      </div>
    </div>
  {:catch error}
    <p class="status error">{error.message}</p>
  {/await}
</div>

<style>
  .page {
    max-width: 1280px;
    margin: 0 auto;
    padding: 2rem 1.5rem 4rem;
  }
  header {
    margin-bottom: 2rem;
  }
  h1 {
    font-size: 1.6rem;
    line-height: 1.3;
    margin: 0 0 0.5rem;
  }
  .section-note {
    font-size: 0.82rem;
    color: var(--text-muted);
    margin: -0.4rem 0 1rem;
  }
  .section-note.error {
    color: #b91c1c;
  }
  h2 {
    font-size: 1rem;
    text-transform: uppercase;
    letter-spacing: 0.03em;
    color: var(--text-muted);
    margin-bottom: 0.75rem;
  }
  section {
    margin-bottom: 2.5rem;
  }
  details summary {
    cursor: pointer;
    list-style: none;
  }
  details summary::-webkit-details-marker {
    display: none;
  }
  details summary h2 {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
  }
  details summary h2::before {
    content: "▸";
    display: inline-block;
    font-size: 0.7em;
    transition: transform 0.15s;
  }
  details[open] summary h2::before {
    transform: rotate(90deg);
  }
  .combined-impact {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.88rem;
  }
  .combined-impact th {
    text-align: right;
    font-weight: 600;
    color: var(--text-muted);
    border-bottom: 1px solid var(--border);
    padding: 0.35rem 0.5rem;
  }
  .combined-impact th:first-child {
    text-align: left;
  }
  .combined-impact td {
    padding: 0.35rem 0.5rem;
    border-bottom: 1px solid var(--border-faint);
  }
  .combined-impact td.num,
  .combined-impact th.num {
    text-align: right;
    font-variant-numeric: tabular-nums;
  }
  .combined-impact tr.receipts {
    font-style: italic;
  }
  .status {
    padding: 3rem 0;
    text-align: center;
    color: var(--text-muted);
  }
  .status.error {
    color: #b91c1c;
  }

  .layout {
    display: block;
  }
  .main-col {
    min-width: 0;
  }

  /* Own scroll, not the page's -- so the measure set stays reachable
     without scrolling past the chart/table content to find it, and
     doesn't grow the page's overall height as more measures are added. */
  .sidebar {
    margin-bottom: 2.5rem;
    padding-bottom: 2rem;
    border-bottom: 1px solid var(--border);
  }
  .card-list {
    display: flex;
    flex-direction: column;
    gap: 0.6rem;
  }
  .measure-card {
    position: relative;
    padding: 0.7rem 1.8rem 0.7rem 0.8rem;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--surface);
  }
  .measure-card .remove {
    position: absolute;
    top: 0.3rem;
    right: 0.3rem;
    font: inherit;
    font-size: 1rem;
    line-height: 1;
    padding: 0.15rem 0.4rem;
    border: none;
    border-radius: 999px;
    background: none;
    color: var(--text-muted);
    cursor: pointer;
  }
  .measure-card .remove:hover {
    background: var(--border-faint);
    color: var(--text-h);
  }
  .measure-card .impact {
    font-size: 1.05rem;
    font-weight: 700;
    color: var(--text-h);
  }
  .measure-card .name {
    display: block;
    font: inherit;
    font-size: 0.85rem;
    text-align: left;
    padding: 0.15rem 0 0.3rem;
    border: none;
    background: none;
    color: var(--text);
    cursor: pointer;
    line-height: 1.35;
  }
  .measure-card .name:hover {
    color: var(--text-h);
    text-decoration: underline;
  }
  .measure-card .round {
    font-size: 0.7rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.03em;
    color: var(--text-muted);
    margin-bottom: 0.4rem;
  }
  .portfolios {
    display: flex;
    flex-wrap: wrap;
    gap: 0.3rem;
  }
  .portfolio-badge {
    font-size: 0.68rem;
    padding: 0.08rem 0.45rem;
    border-radius: 999px;
    background: var(--surface-accent);
    color: var(--text-muted);
    border: 1px solid var(--border);
  }

  @media (min-width: 1024px) {
    .layout {
      display: grid;
      grid-template-columns: minmax(280px, 360px) minmax(0, 1fr);
      gap: 0 2.5rem;
      align-items: start;
    }
    .sidebar {
      margin-bottom: 0;
      padding-bottom: 0;
      padding-right: 1.75rem;
      border-bottom: none;
      border-right: 1px solid var(--border);
      position: sticky;
      top: 1.5rem;
      max-height: calc(100vh - 3rem);
      overflow-y: auto;
    }
  }
</style>
