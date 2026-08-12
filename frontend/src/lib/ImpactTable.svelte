<script>
  import { formatMillionsCell } from "./format.js";

  let { impacts } = $props();

  const DIRECTION_LABEL = {
    payment: "Payments",
    receipt: "Related receipts",
  };

  // One PBS-style table per direction: agency rows x fiscal-year columns,
  // in $m, with a Total row -- matching the layout of the measure tables
  // published in Budget Paper No. 2 / the PBS Table 1.2 itself.
  let tables = $derived.by(() => {
    const fiscalYears = [...new Set(impacts.map((i) => i.fiscal_year))].sort();
    const directions = [...new Set(impacts.map((i) => i.direction))].sort();

    return directions.map((direction) => {
      const rows = impacts.filter((i) => i.direction === direction);
      const agencies = [...new Set(rows.map((r) => r.agency))].sort();
      const matrix = agencies.map((agency) => ({
        agency,
        cells: fiscalYears.map(
          (fy) =>
            rows.find((r) => r.agency === agency && r.fiscal_year === fy)
              ?.amount_thousands ?? 0,
        ),
      }));
      const totals = fiscalYears.map((_, idx) =>
        matrix.reduce((sum, row) => sum + row.cells[idx], 0),
      );
      return { direction, fiscalYears, matrix, totals };
    });
  });
</script>

{#each tables as t}
  <div class="impact-table" class:receipts={t.direction === "receipt"}>
    <h3>{DIRECTION_LABEL[t.direction] ?? t.direction} ($m)</h3>
    <table>
      <thead>
        <tr>
          <th></th>
          {#each t.fiscalYears as fy}
            <th class="num">{fy}</th>
          {/each}
        </tr>
      </thead>
      <tbody>
        {#each t.matrix as row}
          <tr>
            <td>{row.agency}</td>
            {#each row.cells as cell}
              <td class="num">{formatMillionsCell(cell)}</td>
            {/each}
          </tr>
        {/each}
        <tr class="total-row">
          <td>Total – {DIRECTION_LABEL[t.direction] ?? t.direction}</td>
          {#each t.totals as total}
            <td class="num">{formatMillionsCell(total)}</td>
          {/each}
        </tr>
      </tbody>
    </table>
  </div>
{/each}

<style>
  .impact-table {
    margin-bottom: 1.5rem;
  }
  .impact-table.receipts {
    font-style: italic;
  }
  h3 {
    font-size: 0.85rem;
    font-weight: 600;
    margin: 0 0 0.4rem;
    color: var(--text-h);
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.88rem;
  }
  th {
    text-align: right;
    font-weight: 600;
    color: var(--text-muted);
    border-bottom: 1px solid var(--border);
    padding: 0.35rem 0.5rem;
  }
  th:first-child {
    text-align: left;
  }
  td {
    padding: 0.35rem 0.5rem;
    border-bottom: 1px solid var(--border-faint);
  }
  td.num,
  th.num {
    text-align: right;
    font-variant-numeric: tabular-nums;
  }
  .total-row td {
    font-weight: 600;
    border-top: 1px solid var(--border);
    border-bottom: none;
  }
</style>
