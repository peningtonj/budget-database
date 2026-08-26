<script>
  import { fetchProgramOutcomeAudit } from "./api.js";

  // 2014-15 .. 2025-26 -- matches program_outcome_audit()'s own docstring
  // intent: a fixed, wide review window regardless of which years actually
  // have data (a program's own coverage starting later is itself
  // informative, not something to hide by only showing populated columns).
  const YEARS = Array.from({ length: 12 }, (_, i) => {
    const y = 2014 + i;
    return `${y}-${String((y + 1) % 100).padStart(2, "0")}`;
  });

  let query = $state("");
  let gapsOnly = $state(false);
  let expandedKey = $state(null);

  const dataPromise = fetchProgramOutcomeAudit();

  function rowKey(g) {
    return `${g.outcome_number}␟${g.program_name}`;
  }

  function toggle(g) {
    const k = rowKey(g);
    expandedKey = expandedKey === k ? null : k;
  }

  function filtered(groups) {
    const q = query.trim().toLowerCase();
    let out = groups;
    if (gapsOnly) {
      out = out.filter((g) => g.gap_years.length > 0);
    }
    if (q) {
      out = out.filter(
        (g) =>
          (g.program_name ?? "").toLowerCase().includes(q) ||
          (g.outcome_description ?? "").toLowerCase().includes(q) ||
          g.portfolios.some((p) => p.toLowerCase().includes(q)) ||
          g.agencies.some((a) => a.toLowerCase().includes(q)) ||
          g.program_numbers.some((n) => n.toLowerCase().includes(q)),
      );
    }
    return out;
  }

  function fmt(n) {
    return n == null ? "" : n.toLocaleString();
  }
</script>

<div class="page">
  <header>
    <h1>Program data audit</h1>
    <p class="sub">
      Every outcome/program combination across every ingested Budget edition, with every
      portfolio and agency spelling that ever reported it folded into one row -- so a program
      that just moved portfolios or had its portfolio's name drift doesn't look broken. Click a
      row to see the raw records behind it.
    </p>
    <div class="controls">
      <input
        class="query"
        type="text"
        placeholder="Filter by program, outcome, portfolio or agency…"
        bind:value={query}
      />
      <label class="gaps-toggle">
        <input type="checkbox" bind:checked={gapsOnly} />
        Only show programs with a gap in actuals
      </label>
    </div>
  </header>

  {#await dataPromise}
    <p class="status">Loading…</p>
  {:then groups}
    {@const rows = filtered(groups)}
    <p class="count">{rows.length} of {groups.length} programs</p>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th class="col-outcome">Outcome</th>
            <th class="col-prognum">Program #</th>
            <th class="col-program">Program Name</th>
            <th class="col-portfolios">Portfolios</th>
            <th class="col-agencies">Agencies</th>
            {#each YEARS as y (y)}
              <th class="col-year">{y}</th>
            {/each}
          </tr>
        </thead>
        <tbody>
          {#each rows as g (rowKey(g))}
            <tr class="data-row" onclick={() => toggle(g)}>
              <td class="col-outcome"
                ><span class="outcome-num">{g.outcome_number ?? "—"}</span> {g.outcome_description ??
                  ""}</td
              >
              <td class="col-prognum">{g.program_numbers.join(", ")}</td>
              <td class="col-program">{g.program_name ?? "(unnamed program)"}</td>
              <td class="col-portfolios">{g.portfolios.join(", ")}</td>
              <td class="col-agencies">{g.agencies.join(", ")}</td>
              {#each YEARS as y (y)}
                <td class="col-year amount" class:gap-cell={g.gap_years.includes(y)}>{fmt(g.years[y])}</td>
              {/each}
            </tr>
            {#if expandedKey === rowKey(g)}
              <tr class="detail-row">
                <td colspan={5 + YEARS.length}>
                  <table class="detail">
                    <thead>
                      <tr>
                        <th>Edition</th>
                        <th>Portfolio (raw)</th>
                        <th>Agency (raw)</th>
                        <th>Program #</th>
                        <th>Fiscal Year</th>
                        <th>Type</th>
                        <th>Amount ($'000)</th>
                      </tr>
                    </thead>
                    <tbody>
                      {#each g.rows as r, i (i)}
                        <tr>
                          <td>{r.edition}</td>
                          <td>{r.portfolio}</td>
                          <td>{r.agency}</td>
                          <td>{r.program_number}</td>
                          <td>{r.fiscal_year}</td>
                          <td>{r.estimate_type}</td>
                          <td class="amount">{fmt(r.amount_thousands)}</td>
                        </tr>
                      {/each}
                    </tbody>
                  </table>
                </td>
              </tr>
            {/if}
          {/each}
        </tbody>
      </table>
    </div>
  {:catch error}
    <p class="status error">{error.message}</p>
  {/await}
</div>

<style>
  .page {
    max-width: 1700px;
    margin: 0 auto;
    padding: 2rem 1.5rem 4rem;
  }
  header {
    margin-bottom: 1.5rem;
  }
  h1 {
    font-size: 1.6rem;
    margin: 0 0 0.5rem;
  }
  .sub {
    color: var(--text-muted);
    max-width: 75ch;
    margin: 0 0 1rem;
  }
  .controls {
    display: flex;
    align-items: center;
    gap: 1.25rem;
    flex-wrap: wrap;
  }
  .query {
    width: 100%;
    max-width: 480px;
    padding: 0.5rem 0.75rem;
    font: inherit;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--surface);
    color: inherit;
  }
  .gaps-toggle {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    font-size: 0.85rem;
    color: var(--text-muted);
    cursor: pointer;
    white-space: nowrap;
  }
  .gap-cell {
    background: rgba(217, 119, 6, 0.14);
  }
  .count {
    color: var(--text-muted);
    font-size: 0.85rem;
    margin: 0 0 0.75rem;
  }
  .table-wrap {
    overflow-x: auto;
    border: 1px solid var(--border);
    border-radius: 8px;
    max-height: 78vh;
    overflow-y: auto;
  }
  table {
    border-collapse: collapse;
    width: 100%;
    font-size: 0.82rem;
  }
  th,
  td {
    padding: 0.4rem 0.6rem;
    border-bottom: 1px solid var(--border-faint);
    text-align: left;
    white-space: nowrap;
  }
  thead th {
    position: sticky;
    top: 0;
    background: var(--surface);
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: 0.03em;
    color: var(--text-muted);
    z-index: 1;
  }
  .col-outcome {
    white-space: normal;
    max-width: 320px;
  }
  .col-prognum {
    white-space: normal;
    max-width: 90px;
    color: var(--text-muted);
  }
  .outcome-num {
    display: inline-block;
    min-width: 1.4em;
    font-weight: 600;
    color: var(--text-muted);
  }
  .col-program,
  .col-portfolios,
  .col-agencies {
    white-space: normal;
    max-width: 260px;
  }
  .col-year,
  .amount {
    text-align: right;
    font-variant-numeric: tabular-nums;
  }
  .data-row {
    cursor: pointer;
  }
  .data-row:hover {
    background: var(--border-faint);
  }
  .detail-row td {
    background: var(--border-faint);
    padding: 0.75rem 1rem;
  }
  table.detail {
    font-size: 0.78rem;
    width: auto;
  }
  table.detail th,
  table.detail td {
    white-space: nowrap;
  }
  .status {
    padding: 3rem 0;
    text-align: center;
    color: var(--text-muted);
  }
  .status.error {
    color: #c0392b;
  }
</style>
