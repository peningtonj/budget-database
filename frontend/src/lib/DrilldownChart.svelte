<script>
  import * as d3 from "d3";
  import { fetchPortfolioProfile, fetchAgencyOutcomeProfile } from "./api.js";
  import { formatDollars } from "./format.js";

  // mode 'portfolio': one line per agency in `portfolio` for `edition`'s year.
  // mode 'agency': one line per outcome in `agency` (within `portfolio`) for
  // `edition`'s year. Both are single-year snapshots of *which* agencies/
  // outcomes exist, each then showing its own multi-year history -- see
  // portfolio_profile()/agency_outcome_profile() in the backend for why
  // that history isn't bridged across an agency rename.
  let { mode, portfolio, agency = null, edition } = $props();

  const COLORS = [
    "#0f766e", "#7c3aed", "#0891b2", "#be185d", "#b45309",
    "#4338ca", "#15803d", "#a21caf", "#0369a1", "#b91c1c",
  ];

  let dataPromise = $derived(
    mode === "portfolio"
      ? fetchPortfolioProfile(portfolio, edition).then((d) =>
          d.agencies.map((a) => ({ label: a.agency, series: a.series })),
        )
      : fetchAgencyOutcomeProfile(agency, portfolio, edition).then((d) =>
          d.outcomes.map((o) => ({
            label: `Outcome ${o.outcome_number}${o.outcome_description ? `: ${o.outcome_description}` : ""}`,
            series: o.series,
          })),
        ),
  );

  const width = 720;
  const height = 380;
  const margin = { top: 24, right: 16, bottom: 36, left: 68 };

  let hovered = $state(null);
  let isProjected = (d) => d.estimate_type !== "estimated_actual";

  function buildChart(lines) {
    const fiscalYears = [
      ...new Set(lines.flatMap((l) => l.series.map((d) => d.fiscal_year))),
    ].sort();

    const x = d3
      .scalePoint()
      .domain(fiscalYears)
      .range([margin.left, width - margin.right])
      .padding(0.5);

    const allValues = lines.flatMap((l) => l.series.map((d) => d.amount_thousands));
    const [lo, hi] = d3.extent([0, ...allValues]);
    const y = d3.scaleLinear().domain([lo, hi]).nice().range([height - margin.bottom, margin.top]);

    const lineGen = d3.line().x((d) => x(d.fiscal_year)).y((d) => y(d.amount_thousands));

    const chartLines = lines.map((l, i) => {
      const series = l.series;
      const splitIndex = series.findIndex(isProjected);
      const segments =
        splitIndex === -1
          ? [{ dashed: false, path: lineGen(series) }]
          : [
              { dashed: false, path: lineGen(series.slice(0, splitIndex)) },
              { dashed: true, path: lineGen(series.slice(Math.max(splitIndex - 1, 0))) },
            ];
      return {
        label: l.label,
        color: COLORS[i % COLORS.length],
        segments,
        points: series.map((d) => ({ ...d, series_label: l.label })),
      };
    });

    return { fiscalYears, x, y, chartLines };
  }
</script>

<div class="chart-wrap">
  {#await dataPromise}
    <p class="status">Loading…</p>
  {:then lines}
    {#if lines.length === 0}
      <p class="status">No data.</p>
    {:else}
      {@const c = buildChart(lines)}
      <svg
        viewBox="0 0 {width} {height}"
        role="img"
        aria-label={mode === "portfolio" ? `Agency profiles for ${portfolio}` : `Outcome profiles for ${agency}`}
      >
        <line x1={margin.left} x2={width - margin.right} y1={c.y(0)} y2={c.y(0)} stroke="var(--border)" />

        {#each c.chartLines as line}
          {#each line.segments as seg}
            <path
              d={seg.path}
              fill="none"
              stroke={line.color}
              stroke-width="2"
              stroke-dasharray={seg.dashed ? "5 4" : "none"}
            />
          {/each}
          {#each line.points as p}
            <circle
              cx={c.x(p.fiscal_year)}
              cy={c.y(p.amount_thousands)}
              r={hovered === p ? 6 : 4}
              fill={isProjected(p) ? "var(--surface)" : line.color}
              stroke={line.color}
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
        {/each}

        {#each c.fiscalYears as fy}
          <text x={c.x(fy)} y={height - margin.bottom + 18} text-anchor="middle" class="axis-label">{fy}</text>
        {/each}
        {#each c.y.ticks(5) as tick}
          <text x={margin.left - 10} y={c.y(tick)} text-anchor="end" dominant-baseline="middle" class="axis-label">
            {formatDollars(tick)}
          </text>
        {/each}
      </svg>

      <div class="legend">
        {#each c.chartLines as line}
          <span class="legend-item">
            <span class="swatch" style="background: {line.color}"></span>
            {line.label}
          </span>
        {/each}
      </div>

      {#if hovered}
        <div class="tooltip">
          <strong>{hovered.series_label}</strong> · {hovered.fiscal_year}:
          {formatDollars(hovered.amount_thousands)}
        </div>
      {/if}
    {/if}
  {:catch error}
    <p class="status error">{error.message}</p>
  {/await}
</div>

<style>
  .chart-wrap {
    position: relative;
  }
  svg {
    width: 100%;
    height: auto;
  }
  .axis-label {
    font-size: 11px;
    fill: var(--text-muted);
  }
  .legend {
    display: flex;
    flex-wrap: wrap;
    gap: 1rem;
    margin-top: 0.5rem;
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
    flex-shrink: 0;
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
</style>
