<script>
  let { programs } = $props();

  // measure_programs has one row per (program, funding channel): the same
  // program often has a separate "Administered payment" line and its own
  // "Departmental payment" line in the source sheet (the department's own
  // running cost to administer the measure, alongside the money the
  // measure actually delivers) -- both genuinely touch the same program,
  // so merge them into one row with both channels tagged, rather than
  // showing what looks like an unexplained duplicate. Then group by
  // agency -- a flat one-row-per-program table reads as a wall of
  // repeated agency names once a measure touches more than one; grouping
  // makes "which agencies, and what does each one touch" scannable.
  function groupByAgency(programs) {
    const byProgramKey = new Map();
    for (const p of programs) {
      const key = [p.agency, p.direction, p.program_number, p.program_name].join("|");
      if (!byProgramKey.has(key)) {
        byProgramKey.set(key, { ...p, channels: new Set() });
      }
      byProgramKey.get(key).channels.add(p.is_departmental ? "Departmental" : "Administered");
    }
    const merged = [...byProgramKey.values()].map((p) => ({
      ...p,
      channels: [...p.channels].sort(),
    }));

    const byAgency = new Map();
    for (const p of merged) {
      if (!byAgency.has(p.agency)) {
        byAgency.set(p.agency, { agency: p.agency, portfolio: p.portfolio, programs: [] });
      }
      byAgency.get(p.agency).programs.push(p);
    }
    return [...byAgency.values()].sort((a, b) => a.agency.localeCompare(b.agency));
  }

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
