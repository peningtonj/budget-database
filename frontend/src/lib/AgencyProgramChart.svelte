<script>
  import * as d3 from "d3";
  import { fetchProgramProfile } from "./api.js";
  import { formatDollars, formatMillionsCell } from "./format.js";

  // defaultAgency: which agency's programs to show first (e.g. the
  // agency that received the largest combined $ across a selected set
  // of measures -- CombinedMeasuresPage's own use). Falls back to the
  // plain alphabetical-first agency (MeasurePage's existing behavior,
  // unchanged) when omitted or when it doesn't actually appear in this
  // measure's own agencies list.
  let { programs, impacts, defaultAgency = null } = $props();

  // Program lines get a fixed categorical palette (assigned by index, never
  // cycled/reused for a different program); the measure's own line(s) use
  // the same payment/receipt colors as the measure chart elsewhere on the
  // page, so "this is the measure, not a program" reads consistently
  // across the whole page.
  const PROGRAM_COLORS = ["#0f766e", "#7c3aed", "#0891b2", "#be185d", "#b45309"];
  const DIRECTION_COLOR = { payment: "#2563eb", receipt: "#d97706" };
  const DIRECTION_LABEL = { payment: "Payments", receipt: "Related receipts" };

  // Union of both sources, not just programs: a measure's $ impact on an
  // agency (measure_impacts) and the agency's own touched-programs list
  // (measure_programs) aren't guaranteed to cover the same agencies --
  // the source PBS filing for a cross-portfolio measure can record one
  // agency's program-level breakdown but only the other's headline $
  // figure (see agencyPrograms' own note below). Building the picker
  // from programs alone would silently drop that second agency as an
  // option entirely, with no way to see even its own $ impact here.
  let agencies = $derived(
    [...new Set([...programs.map((p) => p.agency), ...impacts.map((i) => i.agency)])].sort(),
  );
  let selectedAgency = $state(null);
  $effect(() => {
    if (selectedAgency === null && agencies.length) {
      selectedAgency = agencies.includes(defaultAgency) ? defaultAgency : agencies[0];
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

  // True for an agency that only has a measure_impacts row, not a
  // measure_programs one -- the source filing gave a $ figure for it
  // but no program-level breakdown. The chart below still renders (with
  // just the measure's own line, no program context) and the table
  // still shows its exact $ figures either way; this only adds an
  // explanatory note for why no program lines/legend entries appear.
  let hasProgramBreakdown = $derived(agencyPrograms.length > 0);

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

  // Deliberately ONE shared y-axis with the programs, not a second
  // independently-scaled panel: whether the measure is a sliver next to
  // its program or a real chunk of it is itself the point of this
  // chart, so it has to share the program's own scale to show that
  // honestly. measureRows is summed by (direction, fiscal_year) via
  // d3.rollup before plotting -- a no-op for a single measure
  // (measure_impacts already has one row per agency/direction/fiscal_
  // year), but required once several measures' own rows can share a
  // (direction, fiscal_year), as they can on the combined-measures page.
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

    const directions = [...new Set(measureRows.map((d) => d.direction))].sort();
    const summed = directions.map((direction) => {
      const byYear = d3.rollup(
        measureRows.filter((d) => d.direction === direction),
        (rows) => d3.sum(rows, (d) => d.amount_thousands),
        (d) => d.fiscal_year,
      );
      const points = [...byYear.entries()]
        .map(([fiscal_year, amount_thousands]) => ({
          fiscal_year,
          amount_thousands,
          series_label: `Measure (${direction})`,
        }))
        .sort((a, b) => (a.fiscal_year > b.fiscal_year ? 1 : -1));
      return { direction, points };
    });

    const allValues = [
      ...profiles.flatMap((p) => p.series.map((d) => d.amount_thousands)),
      ...summed.flatMap((d) => d.points.map((p) => p.amount_thousands)),
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

    const measureLines = summed.map((d) => ({
      direction: d.direction,
      color: DIRECTION_COLOR[d.direction] ?? "#6b6375",
      path: lineGen(d.points),
      points: d.points,
    }));

    return { fiscalYears, x, y, programLines, measureLines, measureByDirection: summed };
  }

  // One row per direction, fiscal-year columns, in $m -- the exact
  // numbers behind the measure's own line on the chart above, for
  // whichever case the chart itself can't show at a glance: a measure
  // too small to read off the chart, or one where the precise figure
  // matters more than its shape.
  function measureImpactTable(measureByDirection) {
    return measureByDirection.map((d) => ({
      direction: d.direction,
      cells: d.points.map((p) => ({ fiscal_year: p.fiscal_year, amount_thousands: p.amount_thousands })),
    }));
  }

  const MIN_TABLE_YEARS = 4;

  // The chart's own x-axis spans every year across the program's own
  // (often decade-plus) stitched series, which the merged single-axis
  // chart genuinely needs -- but reusing that same span for the table
  // below means most of its columns are just "-" for years the measure
  // never touched. Windows the table to the measure's own first-to-last
  // *non-zero* reported year (falling back to its first-to-last
  // reported year at all if every one of them is $0 -- e.g. a
  // since-superseded measure -- rather than showing nothing), padded
  // out to at least MIN_TABLE_YEARS so a one- or two-year measure still
  // gets a readable window instead of a near-empty single column.
  // allFiscalYears is the chart's own full set (chronological) -- the
  // padding is sliced from it, never invented, so the table never shows
  // a year the underlying data doesn't actually have a slot for.
  function measureImpactYears(measureRows, allFiscalYears) {
    const reportedYears = [...new Set(measureRows.map((d) => d.fiscal_year))].sort();
    if (reportedYears.length === 0) return [];

    const nonZeroYears = reportedYears.filter((fy) =>
      measureRows.some((d) => d.fiscal_year === fy && d.amount_thousands !== 0),
    );
    const first = nonZeroYears[0] ?? reportedYears[0];
    const last = nonZeroYears[nonZeroYears.length - 1] ?? reportedYears[reportedYears.length - 1];

    const startIdx = allFiscalYears.indexOf(first);
    const lastIdx = allFiscalYears.indexOf(last);
    const paddedEndIdx = Math.min(startIdx + MIN_TABLE_YEARS - 1, allFiscalYears.length - 1);
    const endIdx = Math.max(lastIdx, paddedEndIdx);
    return allFiscalYears.slice(startIdx, endIdx + 1);
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

  {#if !hasProgramBreakdown}
    <p class="section-note">
      No program-level breakdown is available for {selectedAgency} in this measure's source
      data -- only its own $ impact, shown below.
    </p>
  {/if}

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

      {#if c.measureByDirection.length > 0}
        {@const table = measureImpactTable(c.measureByDirection)}
        {@const tableYears = measureImpactYears(measureSeries, c.fiscalYears)}
        <h3 class="panel-heading">Measure's own $ impact for {selectedAgency}</h3>
        <table class="measure-impact">
          <thead>
            <tr>
              <th></th>
              {#each tableYears as fy}
                <th class="num">{fy}</th>
              {/each}
            </tr>
          </thead>
          <tbody>
            {#each table as row}
              <tr class:receipts={row.direction === "receipt"}>
                <td>{DIRECTION_LABEL[row.direction] ?? row.direction} ($m)</td>
                {#each tableYears as fy}
                  {@const cell = row.cells.find((v) => v.fiscal_year === fy)}
                  <td class="num">{cell ? formatMillionsCell(cell.amount_thousands) : "-"}</td>
                {/each}
              </tr>
            {/each}
          </tbody>
        </table>
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
  .section-note {
    font-size: 0.82rem;
    color: var(--text-muted);
    margin: -0.4rem 0 1rem;
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
    display: block;
  }
  .panel-heading {
    font-size: 0.78rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.03em;
    color: var(--text-muted);
    margin: 1.5rem 0 0.5rem;
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
  .measure-impact {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.85rem;
  }
  .measure-impact th {
    text-align: right;
    font-weight: 600;
    color: var(--text-muted);
    border-bottom: 1px solid var(--border);
    padding: 0.3rem 0.45rem;
  }
  .measure-impact th:first-child {
    text-align: left;
  }
  .measure-impact td {
    padding: 0.3rem 0.45rem;
    border-bottom: 1px solid var(--border-faint);
  }
  .measure-impact td.num,
  .measure-impact th.num {
    text-align: right;
    font-variant-numeric: tabular-nums;
  }
  .measure-impact tr.receipts {
    font-style: italic;
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
