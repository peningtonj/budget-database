<script>
  import * as d3 from "d3";
  import { fetchProgramProfile } from "./api.js";
  import { formatDollars } from "./format.js";

  let { programs, impacts } = $props();

  // Program lines get a fixed categorical palette (assigned by index, never
  // cycled/reused for a different program); the measure's own line(s) use
  // the same payment/receipt colors as the measure chart elsewhere on the
  // page, so "this is the measure, not a program" reads consistently
  // across the whole page.
  const PROGRAM_COLORS = ["#0f766e", "#7c3aed", "#0891b2", "#be185d", "#b45309"];
  const DIRECTION_COLOR = { payment: "#2563eb", receipt: "#d97706" };

  let agencies = $derived([...new Set(programs.map((p) => p.agency))].sort());
  let selectedAgency = $state(null);
  $effect(() => {
    if (selectedAgency === null && agencies.length) {
      selectedAgency = agencies[0];
    }
  });

  let agencyPrograms = $derived(
    [
      ...new Map(
        programs
          .filter((p) => p.agency === selectedAgency && p.program_name)
          .map((p) => [p.program_name, p]),
      ).values(),
    ],
  );

  let profilesPromise = $derived(
    Promise.all(
      agencyPrograms.map((p) =>
        fetchProgramProfile(p.program_name).catch(() => null),
      ),
    ).then((results) => results.filter(Boolean)),
  );

  let measureSeries = $derived(
    impacts.filter((i) => i.agency === selectedAgency),
  );

  const width = 720;
  const height = 360;
  const margin = { top: 24, right: 16, bottom: 36, left: 68 };

  let hovered = $state(null);
  let isProjected = (d) => d.estimate_type !== "estimated_actual";

  function buildChart(profiles, measureRows) {
    const fiscalYears = [
      ...new Set([
        ...profiles.flatMap((p) => p.series.map((d) => d.fiscal_year)),
        ...measureRows.map((d) => d.fiscal_year),
      ]),
    ].sort();

    const x = d3
      .scalePoint()
      .domain(fiscalYears)
      .range([margin.left, width - margin.right])
      .padding(0.5);

    const allValues = [
      ...profiles.flatMap((p) => p.series.map((d) => d.amount_thousands)),
      ...measureRows.map((d) => d.amount_thousands),
    ];
    const [lo, hi] = d3.extent([0, ...allValues]);
    const y = d3.scaleLinear().domain([lo, hi]).nice().range([height - margin.bottom, margin.top]);

    const lineGen = d3.line().x((d) => x(d.fiscal_year)).y((d) => y(d.amount_thousands));

    const programLines = profiles.map((p, i) => {
      const series = p.series;
      const splitIndex = series.findIndex(isProjected);
      const segments =
        splitIndex === -1
          ? [{ dashed: false, path: lineGen(series) }]
          : [
              { dashed: false, path: lineGen(series.slice(0, splitIndex)) },
              { dashed: true, path: lineGen(series.slice(Math.max(splitIndex - 1, 0))) },
            ];
      return {
        program_name: p.program_name,
        color: PROGRAM_COLORS[i % PROGRAM_COLORS.length],
        segments,
        points: series.map((d) => ({ ...d, series_label: p.program_name })),
      };
    });

    const directions = [...new Set(measureRows.map((d) => d.direction))].sort();
    const measureLines = directions.map((direction) => {
      const points = measureRows
        .filter((d) => d.direction === direction)
        .map((d) => ({
          fiscal_year: d.fiscal_year,
          amount_thousands: d.amount_thousands,
          series_label: `Measure (${direction})`,
        }))
        .sort((a, b) => (a.fiscal_year > b.fiscal_year ? 1 : -1));
      return {
        direction,
        color: DIRECTION_COLOR[direction] ?? "#6b6375",
        path: lineGen(points),
        points,
      };
    });

    return { fiscalYears, x, y, programLines, measureLines };
  }

</script>

<div class="agency-chart">
  <label class="agency-picker">
    Agency
    <select bind:value={selectedAgency}>
      {#each agencies as a}
        <option value={a}>{a}</option>
      {/each}
    </select>
  </label>

  {#await profilesPromise}
    <p class="status">Loading…</p>
  {:then profiles}
    {@const c = buildChart(profiles, measureSeries)}
    <div class="chart-wrap">
      <svg viewBox="0 0 {width} {height}" role="img" aria-label="Program and measure profiles for {selectedAgency}">
        <line x1={margin.left} x2={width - margin.right} y1={c.y(0)} y2={c.y(0)} stroke="var(--border)" />

        {#each c.programLines as line}
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

        {#each c.measureLines as line}
          <path d={line.path} fill="none" stroke={line.color} stroke-width="3.5" />
          {#each line.points as p}
            <rect
              x={c.x(p.fiscal_year) - 5}
              y={c.y(p.amount_thousands) - 5}
              width="10"
              height="10"
              fill={line.color}
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
        {#each c.programLines as line}
          <span class="legend-item">
            <span class="swatch" style="background: {line.color}"></span>
            {line.program_name}
          </span>
        {/each}
        {#each c.measureLines as line}
          <span class="legend-item">
            <span class="swatch measure" style="background: {line.color}"></span>
            Measure ({line.direction})
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
  {/await}
</div>

<style>
  .agency-picker {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    font-size: 0.85rem;
    color: var(--text-muted);
    margin-bottom: 1rem;
  }
  select {
    font: inherit;
    padding: 0.3rem 0.5rem;
    border-radius: 6px;
    border: 1px solid var(--border);
    background: var(--surface);
    color: var(--text-h);
  }
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
  }
  .swatch.measure {
    border-radius: 1px;
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
</style>
