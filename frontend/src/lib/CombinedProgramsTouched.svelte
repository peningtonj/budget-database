<script>
  import { groupByAgency } from "./programGrouping.js";

  // measures: combined.measures from fetchMeasureCombined -- each with
  // its own programs[]. Tag every row with its originating measure
  // before grouping, so the shared groupByAgency() helper can also
  // accumulate which of the selected measures touch it (a program hit
  // by 3 of 5 selected measures shows once, not 3 times -- see that
  // helper's own docstring for the touchedBy mechanism).
  let { measures } = $props();

  let totalMeasures = $derived(measures.length);

  // One flat, agency-sorted table rather than a table per agency --
  // across several selected measures the agency count adds up fast, and
  // a single sortable list reads more like a real "programs touched"
  // summary than a wall of same-shaped mini tables.
  let rows = $derived(
    groupByAgency(
      measures.flatMap((m) =>
        m.programs.map((p) => ({ ...p, _measureId: m.measure_id, _measureName: m.measure_name })),
      ),
    )
      .flatMap((group) => group.programs)
      .sort(
        (a, b) =>
          a.agency.localeCompare(b.agency) ||
          (a.program_name ?? "").localeCompare(b.program_name ?? ""),
      ),
  );
</script>

{#if rows.length === 0}
  <p class="status">None of the selected measures touch any programs with $ data.</p>
{:else}
  <table>
    <thead>
      <tr>
        <th>Agency</th>
        <th>Program</th>
        <th>Outcome</th>
        <th>Direction</th>
        <th>Funding channel</th>
        <th>Touched by</th>
      </tr>
    </thead>
    <tbody>
      {#each rows as p}
        <tr>
          <td>{p.agency}</td>
          <td>
            <span class="program-number">{p.program_number}</span>
            {p.program_name ?? "(unresolved)"}
          </td>
          <td class="outcome-cell">
            {#if p.outcome_number}
              <span class="outcome-number">Outcome {p.outcome_number}</span>
            {/if}
            {#if p.outcome_description}
              <span class="outcome-desc">{p.outcome_description}</span>
            {/if}
          </td>
          <td class="direction">{p.direction}</td>
          <td>{p.channels.join(" + ")}</td>
          <td>
            {#if p.touchedBy.length > 1}
              <span class="touched-badge" title={p.touchedBy.map((t) => t.measure_name).join(", ")}>
                {p.touchedBy.length} of {totalMeasures} measures
              </span>
            {:else if p.touchedBy.length === 1}
              <span class="touched-single">{p.touchedBy[0].measure_name}</span>
            {/if}
          </td>
        </tr>
      {/each}
    </tbody>
  </table>
{/if}

<style>
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.88rem;
  }
  th {
    text-align: left;
    font-weight: 600;
    color: var(--text-muted);
    border-bottom: 1px solid var(--border);
    padding: 0.4rem 0.5rem;
  }
  td {
    padding: 0.4rem 0.5rem;
    border-bottom: 1px solid var(--border-faint);
    vertical-align: top;
  }
  .program-number {
    display: inline-block;
    min-width: 2.2rem;
    font-variant-numeric: tabular-nums;
    color: var(--text-muted);
    margin-right: 0.4rem;
  }
  .outcome-cell {
    display: flex;
    flex-direction: column;
    gap: 0.15rem;
  }
  .outcome-number {
    font-size: 0.72rem;
    font-weight: 600;
    color: var(--text-muted);
  }
  .outcome-desc {
    font-size: 0.8rem;
    color: var(--text-muted);
  }
  .direction {
    text-transform: capitalize;
  }
  .touched-badge {
    display: inline-block;
    font-size: 0.72rem;
    font-weight: 600;
    padding: 0.1rem 0.5rem;
    border-radius: 999px;
    background: var(--surface-accent);
    color: var(--text-h);
    border: 1px solid var(--border);
    white-space: nowrap;
    cursor: help;
  }
  .touched-single {
    font-size: 0.78rem;
    color: var(--text-muted);
  }
  .status {
    padding: 1rem 0;
    color: var(--text-muted);
    font-size: 0.85rem;
  }
</style>
