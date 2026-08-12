<script>
  import * as d3 from "d3";
  import { formatDollars } from "./format.js";

  let { impacts } = $props();

  const DIRECTION_COLOR = {
    payment: "#2563eb",
    receipt: "#d97706",
  };

  const width = 640;
  const height = 320;
  const margin = { top: 24, right: 16, bottom: 36, left: 64 };

  let hovered = $state(null);

  // Sum across agencies -- the measure's combined profile, one value per
  // (fiscal_year, direction).
  let grouped = $derived.by(() => {
    const byYear = d3.rollup(
      impacts,
      (rows) => d3.sum(rows, (r) => r.amount_thousands),
      (r) => r.fiscal_year,
      (r) => r.direction,
    );
    const fiscalYears = Array.from(byYear.keys()).sort();
    const directions = Array.from(
      new Set(impacts.map((r) => r.direction)),
    ).sort(); // 'payment' before 'receipt', fixed order
    return { byYear, fiscalYears, directions };
  });

  let scales = $derived.by(() => {
    const { fiscalYears, directions, byYear } = grouped;
    const x = d3
      .scalePoint()
      .domain(fiscalYears)
      .range([margin.left, width - margin.right])
      .padding(0.5);

    const allValues = fiscalYears.flatMap((fy) =>
      directions.map((d) => byYear.get(fy)?.get(d) ?? 0),
    );
    const [lo, hi] = d3.extent([0, ...allValues]);
    const y = d3
      .scaleLinear()
      .domain([lo, hi])
      .nice()
      .range([height - margin.bottom, margin.top]);

    return { x, y };
  });

  let lines = $derived.by(() => {
    const { fiscalYears, directions, byYear } = grouped;
    const { x, y } = scales;
    const lineGen = d3
      .line()
      .x((d) => x(d.fiscalYear))
      .y((d) => y(d.value));

    return directions.map((direction) => {
      const points = fiscalYears.map((fy) => ({
        fiscalYear: fy,
        direction,
        value: byYear.get(fy)?.get(direction) ?? 0,
      }));
      return { direction, path: lineGen(points), points };
    });
  });
</script>

<div class="chart-wrap">
  <svg viewBox="0 0 {width} {height}" role="img" aria-label="Financial profile by fiscal year">
    <line
      x1={margin.left}
      x2={width - margin.right}
      y1={scales.y(0)}
      y2={scales.y(0)}
      stroke="var(--border)"
    />

    {#each lines as line}
      <path d={line.path} fill="none" stroke={DIRECTION_COLOR[line.direction]} stroke-width="2" />
      {#each line.points as p}
        <circle
          cx={scales.x(p.fiscalYear)}
          cy={scales.y(p.value)}
          r={hovered === p ? 6 : 4}
          fill={DIRECTION_COLOR[p.direction]}
          stroke="var(--surface)"
          stroke-width="1.5"
          role="button"
          aria-label="{p.fiscalYear} {p.direction}: {formatDollars(p.value)}"
          tabindex="0"
          onmouseenter={() => (hovered = p)}
          onmouseleave={() => (hovered = null)}
          onfocus={() => (hovered = p)}
          onblur={() => (hovered = null)}
        />
      {/each}
    {/each}

    {#each grouped.fiscalYears as fy}
      <text x={scales.x(fy)} y={height - margin.bottom + 18} text-anchor="middle" class="axis-label">
        {fy}
      </text>
    {/each}

    {#each scales.y.ticks(5) as tick}
      <text
        x={margin.left - 10}
        y={scales.y(tick)}
        text-anchor="end"
        dominant-baseline="middle"
        class="axis-label"
      >
        {formatDollars(tick)}
      </text>
    {/each}
  </svg>

  <div class="legend">
    {#each grouped.directions as direction}
      <span class="legend-item">
        <span class="swatch" style="background: {DIRECTION_COLOR[direction]}"></span>
        {direction}
      </span>
    {/each}
  </div>

  {#if hovered}
    <div class="tooltip">
      <strong>{hovered.fiscalYear}</strong> · {hovered.direction}:
      {formatDollars(hovered.value)}
    </div>
  {/if}
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
    gap: 1rem;
    margin-top: 0.5rem;
    font-size: 0.85rem;
    color: var(--text-muted);
  }
  .legend-item {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    text-transform: capitalize;
  }
  .swatch {
    width: 10px;
    height: 10px;
    border-radius: 2px;
    display: inline-block;
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
</style>
