<script>
  // A small, consistently-styled button dropped into every chart's own
  // legend/toolbar row -- `rows` is already-built CSV rows (see
  // downloadTable.js's seriesToCsvRows), so this component itself knows
  // nothing about any particular chart's own data shape.
  import { downloadCsv } from "./downloadTable.js";

  let { filename, rows } = $props();
</script>

<button
  type="button"
  class="download-table-btn"
  onclick={() => downloadCsv(filename, rows)}
  title="Download this chart's data as a CSV table"
>
  ⬇ Table
</button>

<style>
  .download-table-btn {
    font: inherit;
    font-size: 0.78rem;
    padding: 0.25rem 0.6rem;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--surface);
    color: var(--text-muted);
    cursor: pointer;
    white-space: nowrap;
  }
  .download-table-btn:hover {
    color: var(--text-h);
    border-color: var(--text-muted);
  }
</style>
