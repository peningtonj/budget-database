<script>
  import { groupByAgency } from "./programGrouping.js";

  let { programs } = $props();

  let groups = $derived(groupByAgency(programs));
</script>

{#each groups as group}
  <div class="agency-group">
    <h3>{group.agency} <span class="portfolio-tag">{group.portfolio}</span></h3>
    <table>
      <thead>
        <tr>
          <th>Program</th>
          <th>Outcome</th>
          <th>Direction</th>
          <th>Funding channel</th>
        </tr>
      </thead>
      <tbody>
        {#each group.programs as p}
          <tr>
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
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
{/each}

<style>
  .agency-group {
    margin-bottom: 1.5rem;
  }
  .agency-group h3 {
    font-size: 0.95rem;
    margin: 0 0 0.5rem;
    color: var(--text-h);
    display: flex;
    align-items: baseline;
    gap: 0.5rem;
  }
  .portfolio-tag {
    font-size: 0.72rem;
    font-weight: 500;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.03em;
  }
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
</style>
