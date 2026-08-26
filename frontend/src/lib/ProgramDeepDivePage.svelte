<script>
  import * as d3 from "d3";
  import { fetchProgramEstimateHistory, fetchRelatedPrograms } from "./api.js";
  import { formatDollars } from "./format.js";
  import { adjustAmount, isInflationAdjustEnabled, CURRENT_FY } from "./inflation.svelte.js";

  // programName + portfolio: a single program's stable identity within
  // one portfolio era (see program_estimate_history()'s own docstring
  // for why portfolio -- unlike agency -- is part of the key: the same
  // name can mean a genuinely different program under a different
  // portfolio, e.g. a machinery-of-government transfer).
  // onViewPrograms: same callback App.svelte already passes to
  // ProgramPicker's own "Summarise" flow -- the "combine" suggestion
  // below reuses that exact multi-program comparison page rather than
  // building a second way to view more than one program at once.
  let { programName, portfolio, onViewPrograms } = $props();

  // Bumped by the "Retry" button in the {:catch} block below to force
  // estimatePromise to re-run after a transient failure (a "Failed to
  // fetch" that outlasted fetchWithRetry's own retries).
  let retryToken = $state(0);
  let estimatePromise = $derived.by(() => {
    retryToken;
    return fetchProgramEstimateHistory(programName, portfolio);
  });
  // Independent of estimatePromise (a missing/empty related list is the
  // common case, not an error worth failing the whole page over) --
  // never blocks or fails the main chart if this lookup has an issue.
  let relatedPromise = $derived(fetchRelatedPrograms(programName, portfolio));

  function combineWithRelated(related) {
    onViewPrograms([
      { program_name: programName, portfolio },
      ...related.map((r) => ({ program_name: r.program_name, portfolio: r.portfolio })),
    ]);
  }

  // Vintage lines are ordered/sequential (which Budget round), not an
  // unordered category -- a fixed categorical hue set (capped ~8) would
  // both run out (a well-established program can have 10+ ingested
  // editions, e.g. Child Care Subsidy) and imply an identity relationship
  // between colors that isn't there. A two-hue interpolation by recency
  // reads "earliest round -> latest round" at a glance instead, the same
  // technique the reference vintage chart itself uses. Both anchors are
  // already-established program-line colors elsewhere in the app.
  const VINTAGE_COLOR_OLD = "#0891b2";
  const VINTAGE_COLOR_NEW = "#b45309";
  const vintageColor = d3.interpolateRgb(VINTAGE_COLOR_OLD, VINTAGE_COLOR_NEW);
  const ACTUAL_COLOR = "var(--text-h)";

  const width = 760;
  const height = 380;
  const margin = { top: 24, right: 16, bottom: 52, left: 68 };

  let hovered = $state(null);

  function buildChart(rawVintages, rawActualSeries) {
    const adjustSeries = (series) =>
      series.map((d) => ({ ...d, amount_thousands: adjustAmount(d.amount_thousands, d.fiscal_year) }));
    const vintages = rawVintages.map((v) => ({ ...v, series: adjustSeries(v.series) }));
    const actualSeries = adjustSeries(rawActualSeries);

    const fiscalYears = [
      ...new Set([
        ...vintages.flatMap((v) => v.series.map((d) => d.fiscal_year)),
        ...actualSeries.map((d) => d.fiscal_year),
      ]),
    ].sort();

    const x = d3
      .scalePoint()
      .domain(fiscalYears)
      .range([margin.left, width - margin.right])
      .padding(0.5);

    const allValues = [
      ...vintages.flatMap((v) => v.series.map((d) => d.amount_thousands)),
      ...actualSeries.map((d) => d.amount_thousands),
    ];
    const [lo, hi] = d3.extent([0, ...allValues]);
    const y = d3.scaleLinear().domain([lo, hi]).nice().range([height - margin.bottom, margin.top]);
    const lineGen = d3.line().x((d) => x(d.fiscal_year)).y((d) => y(d.amount_thousands));

    const vintageLines = vintages.map((v, i) => {
      const t = vintages.length > 1 ? i / (vintages.length - 1) : 1;
      const points = v.series.map((d) => ({ ...d, series_label: v.edition }));
      return {
        edition: v.edition,
        color: vintageColor(t),
        path: lineGen(points),
        points,
      };
    });

    const actualPoints = actualSeries.map((d) => ({ ...d, series_label: "Actual" }));

    return {
      fiscalYears,
      x,
      y,
      vintageLines,
      actual: { path: lineGen(actualPoints), points: actualPoints },
    };
  }
</script>

<div class="page">
  {#await estimatePromise}
    <p class="status">Loading…</p>
  {:then data}
    <header>
      <h1>{data.program_name} <span class="header-portfolio">({data.portfolio})</span></h1>
      {#if data.outcome_description}
        <p class="section-note">Outcome {data.outcome_number}: {data.outcome_description}</p>
      {/if}
    </header>

    {#await relatedPromise then relatedData}
      {#if relatedData.related.length > 0}
        <div class="related-box">
          <p class="related-note">{relatedData.note}</p>
          <p class="related-list">
            {#each relatedData.related as r, i (r.program_name)}
              {i > 0 ? " and " : ""}<strong>{r.program_name}</strong>
              <span class="related-range">({r.earliest_edition} – {r.latest_edition})</span>
            {/each}
          </p>
          <button class="combine-btn" onclick={() => combineWithRelated(relatedData.related)}>
            Combine and compare
          </button>
        </div>
      {/if}
    {/await}

    <section>
      <h2>How the Budget's own estimate has moved with each round</h2>
      <p class="section-note">
        One line per ingested Budget edition, showing exactly what that round's own Budget
        papers projected for this program -- its then-current year plus its own forward
        estimates. The bold line is the realised actual once each year is known.
      </p>
      {#if data.vintages.length === 0}
        <p class="status">No estimate history found for this program.</p>
      {:else}
        {@const c = buildChart(data.vintages, data.actual_series)}
        <div class="chart-wrap">
          {#if isInflationAdjustEnabled()}
            <span class="inflation-badge">Adjusted to {CURRENT_FY} dollars</span>
          {/if}
          <svg viewBox="0 0 {width} {height}" role="img" aria-label="Budget estimate history for {data.program_name}">
            <line x1={margin.left} x2={width - margin.right} y1={c.y(0)} y2={c.y(0)} stroke="var(--border)" />

            {#each c.vintageLines as line}
              <path d={line.path} fill="none" stroke={line.color} stroke-width="2" stroke-dasharray="1 3" stroke-linecap="round" />
              {#each line.points as p}
                <circle
                  cx={c.x(p.fiscal_year)}
                  cy={c.y(p.amount_thousands)}
                  r={hovered === p ? 5 : 3}
                  fill={line.color}
                  stroke="var(--surface)"
                  stroke-width="1"
                  role="button"
                  aria-label="{p.series_label} {p.fiscal_year}: {formatDollars(p.amount_thousands)}"
                  tabindex="0"
                  onmouseenter={() => (hovered = p)}
                  onmouseleave={() => (hovered = null)}
                  onfocus={() => (hovered = p)}
                  onblur={() => (hovered = null)}
                />
              {/each}
            {/each}

            <path d={c.actual.path} fill="none" stroke={ACTUAL_COLOR} stroke-width="3" />
            {#each c.actual.points as p}
              <circle
                cx={c.x(p.fiscal_year)}
                cy={c.y(p.amount_thousands)}
                r={hovered === p ? 7 : 5}
                fill={ACTUAL_COLOR}
                stroke="var(--surface)"
                stroke-width="1.5"
                role="button"
                aria-label="{p.series_label} {p.fiscal_year}: {formatDollars(p.amount_thousands)}"
                tabindex="0"
                onmouseenter={() => (hovered = p)}
                onmouseleave={() => (hovered = null)}
                onfocus={() => (hovered = p)}
                onblur={() => (hovered = null)}
              />
            {/each}

            {#each c.fiscalYears as fy}
              <text
                x={c.x(fy)}
                y={height - margin.bottom + 10}
                text-anchor="end"
                transform={`rotate(-45 ${c.x(fy)} ${height - margin.bottom + 10})`}
                class="axis-label"
              >{fy}</text>
            {/each}
            {#each c.y.ticks(5) as tick}
              <text x={margin.left - 10} y={c.y(tick)} text-anchor="end" dominant-baseline="middle" class="axis-label">
                {formatDollars(tick)}
              </text>
            {/each}
          </svg>

          <div class="legend">
            <span class="legend-item">
              <span class="swatch actual" style="background: {ACTUAL_COLOR}"></span>
              Actual
            </span>
            {#each c.vintageLines as line}
              <span class="legend-item">
                <span class="swatch" style="background: {line.color}"></span>
                {line.edition}
              </span>
            {/each}
          </div>

          {#if hovered}
            <div class="tooltip">
              <strong>{hovered.series_label}</strong> · {hovered.fiscal_year}:
              {formatDollars(hovered.amount_thousands)}
            </div>
          {/if}
        </div>
      {/if}
    </section>
  {:catch error}
    <p class="status error">{error.message}</p>
    <button type="button" class="retry-btn" onclick={() => retryToken++}>Retry</button>
  {/await}
</div>

<style>
  .page {
    max-width: 900px;
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
  .header-portfolio {
    font-size: 1rem;
    font-weight: 400;
    color: var(--text-muted);
  }
  .section-note {
    font-size: 0.82rem;
    color: var(--text-muted);
    margin: -0.4rem 0 1rem;
  }
  .related-box {
    background: var(--surface-accent, var(--border-faint));
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 0.9rem 1.1rem;
    margin: 0 0 2rem;
  }
  .related-note {
    margin: 0 0 0.5rem;
    font-size: 0.88rem;
  }
  .related-list {
    margin: 0 0 0.75rem;
    font-size: 0.85rem;
    color: var(--text-muted);
  }
  .related-range {
    font-size: 0.8rem;
  }
  .combine-btn {
    font: inherit;
    font-size: 0.85rem;
    padding: 0.4rem 0.9rem;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--surface);
    color: var(--text-h);
    cursor: pointer;
  }
  .combine-btn:hover {
    border-color: var(--text-muted);
  }
  h2 {
    font-size: 1rem;
    text-transform: uppercase;
    letter-spacing: 0.03em;
    color: var(--text-muted);
    margin: 0 0 0.75rem;
  }
  section {
    margin-bottom: 2.5rem;
  }
  .chart-wrap {
    position: relative;
  }
  svg {
    width: 100%;
    height: auto;
    display: block;
  }
  .inflation-badge {
    display: inline-block;
    font-size: 0.72rem;
    font-weight: 600;
    color: #0f766e;
    background: #ecfdf5;
    border: 1px solid #99f6e4;
    border-radius: 999px;
    padding: 0.15rem 0.6rem;
    margin-bottom: 0.4rem;
  }
  .axis-label {
    font-size: 11px;
    fill: var(--text-muted);
  }
  .legend {
    display: flex;
    flex-wrap: wrap;
    gap: 0.75rem 1rem;
    margin-top: 0.75rem;
    font-size: 0.82rem;
    color: var(--text-muted);
  }
  .legend-item {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
  }
  .swatch {
    width: 10px;
    height: 10px;
    border-radius: 2px;
    display: inline-block;
  }
  .swatch.actual {
    border-radius: 50%;
  }
  .tooltip {
    position: absolute;
    top: 0;
    right: 0;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0.4rem 0.6rem;
    font-size: 0.85rem;
    pointer-events: none;
  }
  .status {
    padding: 2rem 0;
    text-align: center;
    color: var(--text-muted);
  }
  .status.error {
    color: #b91c1c;
  }
  .retry-btn {
    display: block;
    margin: 0 auto;
    font: inherit;
    font-size: 0.85rem;
    padding: 0.4rem 0.9rem;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--surface);
    color: var(--text-h);
    cursor: pointer;
  }
  .retry-btn:hover {
    border-color: var(--text-muted);
  }
</style>
