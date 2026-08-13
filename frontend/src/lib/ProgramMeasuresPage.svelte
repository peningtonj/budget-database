<script>
  import * as d3 from "d3";
  import { untrack } from "svelte";
  import { fetchMeasuresByProgram, fetchProgramProfile } from "./api.js";
  import { removeFromProgramTray } from "./programTray.svelte.js";
  import { formatDollars, formatMillionsCell } from "./format.js";

  // programSelections: a snapshot of the program tray's own
  // {program_name, portfolio} pairs at the moment "Summarise" was
  // clicked -- see CombinedMeasuresPage's own `ids` prop for the
  // identical reasoning (no URL of its own, a one-time snapshot not
  // live-bound to the tray). portfolio travels alongside program_name,
  // not just the name alone: the same name can mean two different
  // things under two different portfolio eras (see programTray.svelte.js
  // and measures_by_program()'s own docstrings), and each needs its own
  // independent fetch/display here.
  let { programSelections, onselect, onBack, onDeepDive } = $props();

  let currentSelections = $state(untrack(() => [...programSelections]));

  const PROGRAM_LINE_COLORS = [
    "#0f766e",
    "#7c3aed",
    "#0891b2",
    "#be185d",
    "#b45309",
    "#2563eb",
    "#65a30d",
    "#c026d3",
  ];
  const DIRECTION_LABEL = { payment: "Payments", receipt: "Related receipts" };

  // One fetch per selected program (each is its own independent
  // "find every measure touching this program" query -- see
  // measures_by_program()'s own docstring) -- not batched into one
  // request the way CombinedMeasuresPage's measure ids are, since each
  // program's own reverse-index lookup is already a single fast call
  // once the server-side index is warm (see _build_program_reverse_index).
  //
  // Once that resolves, fetch each distinct program_name's own actuals
  // profile (program_profile()/fetchProgramProfile) -- the chart's own
  // data source. Deliberately deduped by program_name ALONE, not the
  // (program_name, portfolio) pair the measures fetch above uses: the
  // same program can be selected twice under two different portfolio
  // eras (see programTray.svelte.js), but it's still one continuous
  // real program with one spending history, so it gets one actuals
  // line, not two identical overlapping ones. program_profile() itself
  // already stitches across portfolio/agency spelling changes over
  // time (see its own docstring), so a plain program_name lookup is
  // exactly the right key here.
  let dataPromise = $derived(
    Promise.all(
      currentSelections.map((sel) =>
        fetchMeasuresByProgram(sel.program_name, sel.portfolio).catch(() => ({
          program_name: sel.program_name,
          portfolio: sel.portfolio,
          measures: [],
        })),
      ),
    ).then(async (programs) => {
      const distinctNames = [...new Set(programs.map((p) => p.program_name))];
      const profiles = await Promise.all(
        distinctNames.map((name) => fetchProgramProfile(name).catch(() => null)),
      );
      const profileByName = new Map();
      distinctNames.forEach((name, i) => {
        if (profiles[i]) profileByName.set(name, profiles[i]);
      });
      return { programs, profileByName };
    }),
  );

  function removeProgram(programName, portfolio) {
    currentSelections = currentSelections.filter(
      (s) => !(s.program_name === programName && s.portfolio === portfolio),
    );
    removeFromProgramTray(portfolio, programName);
    if (currentSelections.length === 0) onBack();
  }

  function programImpactTable(program) {
    const allImpacts = program.measures.flatMap((m) => m.impacts);
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

  const width = 720;
  const height = 340;
  const margin = { top: 24, right: 16, bottom: 36, left: 68 };

  let hovered = $state(null);
  let isProjected = (d) => d.estimate_type !== "estimated_actual";

  // One line per distinct program_name (see dataPromise's own note on
  // why profileByName is deduped that way, not by selection). Solid
  // segment + filled circle for reported actuals, dashed segment +
  // hollow circle for forward estimates -- the same convention
  // AgencyProgramChart.svelte uses for a program's own profile line.
  function buildChart(profileByName) {
    const profiles = [...profileByName.values()];
    const fiscalYears = [...new Set(profiles.flatMap((p) => p.series.map((d) => d.fiscal_year)))].sort();
    const x = d3
      .scalePoint()
      .domain(fiscalYears)
      .range([margin.left, width - margin.right])
      .padding(0.5);

    const allValues = profiles.flatMap((p) => p.series.map((d) => d.amount_thousands));
    const [lo, hi] = d3.extent([0, ...allValues]);
    const y = d3.scaleLinear().domain([lo, hi]).nice().range([height - margin.bottom, margin.top]);
    const lineGen = d3.line().x((d) => x(d.fiscal_year)).y((d) => y(d.amount_thousands));

    const lines = [...profileByName.entries()].map(([name, profile], i) => {
      const series = profile.series;
      const splitIndex = series.findIndex(isProjected);
      const segments =
        splitIndex === -1
          ? [{ dashed: false, path: lineGen(series) }]
          : [
              { dashed: false, path: lineGen(series.slice(0, splitIndex)) },
              { dashed: true, path: lineGen(series.slice(Math.max(splitIndex - 1, 0))) },
            ];
      return {
        label: name,
        color: PROGRAM_LINE_COLORS[i % PROGRAM_LINE_COLORS.length],
        segments,
        points: series.map((d) => ({ ...d, series_label: name })),
      };
    });

    return { fiscalYears, x, y, lines };
  }

  // Exact $m figures behind the chart's own lines -- one row per
  // distinct program, same pairing convention as AgencyProgramChart's
  // "Measure's own $ impact" table.
  function programActualsTable(profileByName, fiscalYears) {
    return [...profileByName.entries()].map(([name, profile]) => ({
      name,
      cells: fiscalYears.map((fy) => profile.series.find((d) => d.fiscal_year === fy)?.amount_thousands),
    }));
  }

  function directionTotal(impacts, direction) {
    return d3.sum(impacts.filter((i) => i.direction === direction), (i) => i.amount_thousands);
  }
</script>

<div class="page">
  {#await dataPromise}
    <p class="status">Loading…</p>
  {:then data}
    {@const programs = data.programs}
    {@const profileByName = data.profileByName}
    <header>
      <h1>Summarising {programs.length} program{programs.length === 1 ? "" : "s"}</h1>
      <p class="section-note">
        Each program's own actual and budgeted spending history, as reported in the Budget papers.
        Every measure touching each selected program is listed further down.
        {#if profileByName.size < programs.length}
          Two or more selections share the same underlying program across different portfolio eras,
          so they're shown as a single continuous line below.
        {/if}
      </p>
    </header>

    <section>
      <h2>Program actuals</h2>
      {#if profileByName.size === 0}
        <p class="status">No actuals data found for any of the selected programs.</p>
      {:else}
        {@const c = buildChart(profileByName)}
        <div class="chart-wrap">
          <svg viewBox="0 0 {width} {height}" role="img" aria-label="Actual and budgeted spending per selected program">
            <line x1={margin.left} x2={width - margin.right} y1={c.y(0)} y2={c.y(0)} stroke="var(--border)" />
            {#each c.lines as line}
              {#each line.segments as seg}
                <path
                  d={seg.path}
                  fill="none"
                  stroke={line.color}
                  stroke-width="2.5"
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
            {#each c.lines as line}
              <span class="legend-item">
                <span class="swatch" style="background: {line.color}"></span>
                <button type="button" class="legend-link" onclick={() => onDeepDive(line.label)}>
                  {line.label}
                </button>
              </span>
            {/each}
          </div>
          <p class="section-note deep-dive-hint">Click a program above to see how its Budget estimate has moved with each round.</p>
          {#if hovered}
            <div class="tooltip">
              <strong>{hovered.series_label}</strong> · {hovered.fiscal_year}:
              {formatDollars(hovered.amount_thousands)}
              {#if isProjected(hovered)}(forward estimate){/if}
            </div>
          {/if}
        </div>

        {@const table = programActualsTable(profileByName, c.fiscalYears)}
        <table class="program-impact actuals-table">
          <thead>
            <tr>
              <th></th>
              {#each c.fiscalYears as fy}
                <th class="num">{fy}</th>
              {/each}
            </tr>
          </thead>
          <tbody>
            {#each table as row}
              <tr>
                <td>{row.name} ($m)</td>
                {#each row.cells as cell}
                  <td class="num">{cell === undefined ? "-" : formatMillionsCell(cell)}</td>
                {/each}
              </tr>
            {/each}
          </tbody>
        </table>
      {/if}
    </section>

    {#each programs as program (program.portfolio + '␟' + program.program_name)}
      <section class="program-section">
        <div class="program-heading">
          <h2>{program.program_name} <span class="section-portfolio">({program.portfolio})</span></h2>
          <button
            type="button"
            class="remove"
            onclick={() => removeProgram(program.program_name, program.portfolio)}
            aria-label={`Remove ${program.program_name} (${program.portfolio})`}
          >
            × Remove
          </button>
        </div>

        {#if program.measures.length === 0}
          <p class="status">No measures found touching this program.</p>
        {:else}
          {@const table = programImpactTable(program)}
          <p class="section-note">
            {program.measures.length} measure{program.measures.length === 1 ? "" : "s"} touch this program.
          </p>
          <table class="program-impact">
            <thead>
              <tr>
                <th></th>
                {#each table.fiscalYears as fy}
                  <th class="num">{fy}</th>
                {/each}
              </tr>
            </thead>
            <tbody>
              {#each table.rows as row}
                <tr class:receipts={row.direction === "receipt"}>
                  <td>{DIRECTION_LABEL[row.direction] ?? row.direction} ($m)</td>
                  {#each row.cells as cell}
                    <td class="num">{formatMillionsCell(cell)}</td>
                  {/each}
                </tr>
              {/each}
            </tbody>
          </table>

          <details>
            <summary>
              <span class="disclosure">▸</span> Show the {program.measures.length} measure{program.measures.length === 1 ? "" : "s"}
            </summary>
            <ul class="measure-list">
              {#each program.measures as m (m.measure_id)}
                <li>
                  <button type="button" class="measure-name" onclick={() => onselect(m.measure_id, m.measure_name, m.edition)}>
                    {m.measure_name}
                  </button>
                  <span class="edition-badge">{m.edition}</span>
                  <span class="totals">
                    {formatDollars(directionTotal(m.impacts, "payment"))} payments
                    {#if m.impacts.some((i) => i.direction === "receipt")}
                      · {formatDollars(directionTotal(m.impacts, "receipt"))} receipts
                    {/if}
                  </span>
                </li>
              {/each}
            </ul>
          </details>
        {/if}
      </section>
    {/each}
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
  .legend-link {
    font: inherit;
    font-size: inherit;
    color: inherit;
    background: none;
    border: none;
    padding: 0;
    cursor: pointer;
    text-decoration: underline;
    text-decoration-color: transparent;
    transition: text-decoration-color 0.15s;
  }
  .legend-link:hover {
    color: var(--text-h);
    text-decoration-color: var(--text-muted);
  }
  .deep-dive-hint {
    margin-top: 0.4rem;
    margin-bottom: 0;
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
  .actuals-table {
    margin-top: 1.25rem;
  }
  .program-section {
    padding-top: 1.5rem;
    border-top: 1px solid var(--border-faint);
  }
  .program-heading {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 0.75rem;
  }
  .program-heading h2 {
    color: var(--text-h);
    text-transform: none;
    letter-spacing: normal;
    font-size: 1.05rem;
    font-weight: 600;
  }
  .section-portfolio {
    font-size: 0.85rem;
    font-weight: 400;
    color: var(--text-muted);
  }
  .remove {
    flex-shrink: 0;
    font: inherit;
    font-size: 0.78rem;
    padding: 0.2rem 0.5rem;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: none;
    color: var(--text-muted);
    cursor: pointer;
    white-space: nowrap;
  }
  .remove:hover {
    color: var(--text-h);
    border-color: var(--text-muted);
  }
  .program-impact {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.85rem;
    margin-bottom: 1rem;
  }
  .program-impact th {
    text-align: right;
    font-weight: 600;
    color: var(--text-muted);
    border-bottom: 1px solid var(--border);
    padding: 0.3rem 0.45rem;
  }
  .program-impact th:first-child {
    text-align: left;
  }
  .program-impact td {
    padding: 0.3rem 0.45rem;
    border-bottom: 1px solid var(--border-faint);
  }
  .program-impact td.num,
  .program-impact th.num {
    text-align: right;
    font-variant-numeric: tabular-nums;
  }
  .program-impact tr.receipts {
    font-style: italic;
  }
  .measure-list {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .measure-list li {
    display: flex;
    align-items: baseline;
    flex-wrap: wrap;
    gap: 0.5rem;
    padding: 0.5rem 0.2rem;
    border-bottom: 1px solid var(--border-faint);
  }
  .measure-name {
    font: inherit;
    font-size: 0.9rem;
    color: var(--text-h);
    background: none;
    border: none;
    padding: 0;
    text-align: left;
    cursor: pointer;
    text-decoration: underline;
    text-decoration-color: transparent;
    transition: text-decoration-color 0.15s;
  }
  .measure-name:hover {
    text-decoration-color: var(--text-muted);
  }
  .edition-badge {
    font-size: 0.7rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.03em;
    color: var(--text-muted);
  }
  .totals {
    font-size: 0.78rem;
    color: var(--text-muted);
    margin-left: auto;
  }
  details summary {
    cursor: pointer;
    list-style: none;
    font-size: 0.82rem;
    color: var(--text-muted);
    margin-bottom: 0.5rem;
  }
  details summary::-webkit-details-marker {
    display: none;
  }
  .disclosure {
    display: inline-block;
    font-size: 0.7em;
    transition: transform 0.15s;
  }
  details[open] .disclosure {
    transform: rotate(90deg);
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
