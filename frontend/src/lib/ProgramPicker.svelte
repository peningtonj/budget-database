<script>
  import { fetchProgramHierarchy } from "./api.js";
  import {
    programTray,
    isInProgramTray,
    addToProgramTray,
    removeFromProgramTray,
    clearProgramTray,
  } from "./programTray.svelte.js";

  let { onSummarise } = $props();

  let hierarchy = $state([]);
  let loading = $state(true);
  let error = $state(null);

  $effect(() => {
    fetchProgramHierarchy()
      .then((rows) => {
        hierarchy = rows;
        error = null;
      })
      .catch((e) => {
        error = e.message;
      })
      .finally(() => {
        loading = false;
      });
  });

  // A cascading drill-down, one level revealed at a time, rather than
  // four dropdowns -- an outcome's own description (often a full
  // sentence or two) reads as a wrapped block of text you click, not a
  // single truncated <option> line. Each already-made choice collapses
  // to a breadcrumb chip above the next level's own list; clicking an
  // earlier chip jumps back to it and drops everything chosen after it.
  let selectedPortfolio = $state(null);
  let selectedAgency = $state(null);
  let selectedOutcomeNumber = $state(null);
  // Keyed by program_name, not program_number -- confirmed in practice
  // that a number gets reused for a genuinely different program once
  // the hierarchy spans every edition (e.g. within one outcome, "1.2"
  // is Child Care Benefit in one year and Child Care Subsidy in
  // another). Keying on the number alone meant .find() below could
  // silently resolve to the WRONG program -- whichever one happened to
  // come first in the list -- read from the outside as "the add button
  // is greyed out" (it was showing a different, already-added program)
  // or "my selection got replaced". program_name is the one identity
  // that's actually stable and unique here, same as everywhere else in
  // this app it's used as the lookup key.
  let selectedProgramName = $state(null);

  let portfolios = $derived([...new Set(hierarchy.map((r) => r.portfolio))].sort());

  let agencyOptions = $derived(
    selectedPortfolio
      ? [...new Set(hierarchy.filter((r) => r.portfolio === selectedPortfolio).map((r) => r.agency))].sort()
      : [],
  );

  let outcomeOptions = $derived(
    selectedAgency
      ? [
          ...new Map(
            hierarchy
              .filter((r) => r.portfolio === selectedPortfolio && r.agency === selectedAgency)
              .map((r) => [r.outcome_number, r]),
          ).values(),
        ].sort((a, b) => a.outcome_number - b.outcome_number)
      : [],
  );

  let programOptions = $derived(
    selectedOutcomeNumber != null
      ? hierarchy
          .filter(
            (r) =>
              r.portfolio === selectedPortfolio &&
              r.agency === selectedAgency &&
              r.outcome_number === selectedOutcomeNumber,
          )
          .sort((a, b) => a.program_number.localeCompare(b.program_number))
      : [],
  );

  let selectedProgramRow = $derived(
    programOptions.find((r) => r.program_name === selectedProgramName) ?? null,
  );

  let selectedOutcomeRow = $derived(
    outcomeOptions.find((r) => r.outcome_number === selectedOutcomeNumber) ?? null,
  );

  function pickPortfolio(p) {
    selectedPortfolio = p;
    selectedAgency = null;
    selectedOutcomeNumber = null;
    selectedProgramName = null;
  }

  function pickAgency(a) {
    selectedAgency = a;
    selectedOutcomeNumber = null;
    selectedProgramName = null;
  }

  function pickOutcome(n) {
    selectedOutcomeNumber = n;
    selectedProgramName = null;
  }

  function pickProgram(name) {
    selectedProgramName = name;
  }

  function startOver() {
    selectedPortfolio = null;
    selectedAgency = null;
    selectedOutcomeNumber = null;
    selectedProgramName = null;
  }

  function addProgram() {
    if (!selectedProgramRow) return;
    addToProgramTray(selectedProgramRow);
    startOver();
  }
</script>

<div class="program-picker">
  {#if loading}
    <p class="status">Loading programs…</p>
  {:else if error}
    <p class="status error">{error}</p>
  {:else}
    {#if selectedPortfolio || selectedAgency || selectedOutcomeNumber != null}
      <div class="breadcrumbs">
        <button type="button" class="crumb" onclick={startOver}>Portfolio</button>
        <span class="sep">›</span>
        <button type="button" class="crumb" class:current={!selectedAgency} onclick={() => pickPortfolio(selectedPortfolio)}>
          {selectedPortfolio}
        </button>
        {#if selectedAgency}
          <span class="sep">›</span>
          <button
            type="button"
            class="crumb"
            class:current={selectedOutcomeNumber == null}
            onclick={() => pickAgency(selectedAgency)}
          >
            {selectedAgency}
          </button>
        {/if}
        {#if selectedOutcomeNumber != null}
          <span class="sep">›</span>
          <button
            type="button"
            class="crumb"
            class:current={!selectedProgramName}
            onclick={() => pickOutcome(selectedOutcomeNumber)}
          >
            Outcome {selectedOutcomeNumber}
          </button>
        {/if}
        {#if selectedProgramRow}
          <span class="sep">›</span>
          <span class="crumb current">{selectedProgramRow.program_number} {selectedProgramRow.program_name}</span>
        {/if}
      </div>
    {/if}

    {#if !selectedPortfolio}
      <h3>Choose a portfolio</h3>
      <div class="option-grid">
        {#each portfolios as p}
          <button type="button" class="option-card" onclick={() => pickPortfolio(p)}>{p}</button>
        {/each}
      </div>
    {:else if !selectedAgency}
      <h3>Choose an agency in {selectedPortfolio}</h3>
      <div class="option-grid">
        {#each agencyOptions as a}
          <button type="button" class="option-card" onclick={() => pickAgency(a)}>{a}</button>
        {/each}
      </div>
    {:else if selectedOutcomeNumber == null}
      <h3>Choose an outcome for {selectedAgency}</h3>
      <div class="option-list">
        {#each outcomeOptions as o}
          <button type="button" class="option-block" onclick={() => pickOutcome(o.outcome_number)}>
            <span class="option-heading">Outcome {o.outcome_number}</span>
            <span class="option-detail">{o.outcome_description}</span>
          </button>
        {/each}
      </div>
    {:else if !selectedProgramName}
      <h3>Choose a program under Outcome {selectedOutcomeNumber}</h3>
      {#if selectedOutcomeRow}
        <p class="section-note">{selectedOutcomeRow.outcome_description}</p>
      {/if}
      <div class="option-list">
        {#each programOptions as p}
          <button type="button" class="option-block" onclick={() => pickProgram(p.program_name)}>
            <span class="option-heading">{p.program_number} {p.program_name}</span>
          </button>
        {/each}
      </div>
    {:else}
      <div class="confirm-block">
        <p>
          <strong>{selectedProgramRow.program_number} {selectedProgramRow.program_name}</strong>
          <span class="confirm-portfolio">({selectedProgramRow.portfolio})</span>
          {#if isInProgramTray(selectedProgramRow.portfolio, selectedProgramRow.program_name)}
            <span class="already-added">✓ already added</span>
          {/if}
        </p>
        <div class="confirm-actions">
          <button
            type="button"
            class="add-btn"
            disabled={isInProgramTray(selectedProgramRow.portfolio, selectedProgramRow.program_name)}
            onclick={addProgram}
          >
            + Add this program
          </button>
          <button type="button" class="change-btn" onclick={() => pickOutcome(selectedOutcomeNumber)}>
            Choose a different program
          </button>
        </div>
      </div>
    {/if}

    {#if programTray.length > 0}
      <div class="tray-section">
        <div class="tray-header">
          <h3>Selected programs</h3>
          <button type="button" class="clear-btn" onclick={clearProgramTray}>Clear</button>
        </div>
        <ul class="program-list">
          {#each programTray as p (p.portfolio + '␟' + p.program_name)}
            <li>
              <div class="program-info">
                <span class="program-name">{p.program_number} {p.program_name}</span>
                <span class="program-meta">
                  <span class="portfolio-badge">{p.portfolio}</span>
                  {p.agency} — Outcome {p.outcome_number}
                </span>
              </div>
              <button
                type="button"
                class="remove"
                onclick={() => removeFromProgramTray(p.portfolio, p.program_name)}
                aria-label={`Remove ${p.program_name} (${p.portfolio})`}
              >
                ×
              </button>
            </li>
          {/each}
        </ul>
        <button
          type="button"
          class="summarise-btn"
          onclick={() => onSummarise(programTray.map((p) => ({ program_name: p.program_name, portfolio: p.portfolio })))}
        >
          Summarise {programTray.length} program{programTray.length === 1 ? "" : "s"} →
        </button>
      </div>
    {/if}
  {/if}
</div>

<style>
  .program-picker {
    margin-bottom: 1rem;
  }
  .breadcrumbs {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.3rem;
    margin-bottom: 1rem;
    font-size: 0.82rem;
  }
  .sep {
    color: var(--text-muted);
  }
  .crumb {
    font: inherit;
    font-size: 0.82rem;
    padding: 0.2rem 0.55rem;
    border-radius: 999px;
    border: 1px solid var(--border);
    background: var(--surface);
    color: var(--text-muted);
    cursor: pointer;
    max-width: 260px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  button.crumb {
    cursor: pointer;
  }
  span.crumb {
    cursor: default;
  }
  .crumb.current {
    background: var(--surface-accent);
    color: var(--text-h);
    border-color: var(--text-muted);
    font-weight: 600;
  }
  h3 {
    font-size: 0.85rem;
    font-weight: 600;
    color: var(--text-h);
    margin: 0 0 0.6rem;
  }
  .option-grid {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    max-height: 320px;
    overflow-y: auto;
    padding-right: 0.2rem;
  }
  .option-card {
    font: inherit;
    font-size: 0.85rem;
    padding: 0.5rem 0.8rem;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--surface);
    color: var(--text);
    cursor: pointer;
    text-align: left;
  }
  .option-card:hover {
    border-color: var(--text-muted);
    background: var(--surface-accent);
  }
  .option-list {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    max-height: 380px;
    overflow-y: auto;
    padding-right: 0.2rem;
  }
  .option-block {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    font: inherit;
    text-align: left;
    padding: 0.65rem 0.85rem;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--surface);
    color: var(--text);
    cursor: pointer;
  }
  .option-block:hover {
    border-color: var(--text-muted);
    background: var(--surface-accent);
  }
  .option-heading {
    font-size: 0.88rem;
    font-weight: 600;
    color: var(--text-h);
  }
  .option-detail {
    font-size: 0.82rem;
    color: var(--text-muted);
    line-height: 1.4;
    white-space: normal;
  }
  .section-note {
    font-size: 0.82rem;
    color: var(--text-muted);
    margin: -0.3rem 0 0.75rem;
  }
  .confirm-block {
    padding: 0.9rem 1rem;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--surface-accent);
  }
  .confirm-block p {
    margin: 0 0 0.75rem;
    font-size: 0.92rem;
    color: var(--text-h);
  }
  .confirm-portfolio {
    font-size: 0.8rem;
    font-weight: 400;
    color: var(--text-muted);
  }
  .already-added {
    margin-left: 0.5rem;
    font-size: 0.78rem;
    font-weight: 600;
    color: var(--text-muted);
  }
  .confirm-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 0.6rem;
  }
  .add-btn {
    font: inherit;
    font-size: 0.85rem;
    font-weight: 600;
    padding: 0.5rem 0.9rem;
    border: 1px solid var(--text-h);
    border-radius: 6px;
    background: var(--text-h);
    color: var(--bg);
    cursor: pointer;
  }
  .add-btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  .change-btn {
    font: inherit;
    font-size: 0.85rem;
    padding: 0.5rem 0.9rem;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: none;
    color: var(--text-muted);
    cursor: pointer;
  }
  .change-btn:hover {
    color: var(--text-h);
    border-color: var(--text-muted);
  }
  .tray-section {
    margin-top: 1.25rem;
    padding-top: 1rem;
    border-top: 1px solid var(--border-faint);
  }
  .tray-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 0.5rem;
  }
  .tray-header h3 {
    font-size: 0.78rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.03em;
    color: var(--text-muted);
    margin: 0;
  }
  .clear-btn {
    font: inherit;
    font-size: 0.78rem;
    padding: 0.25rem 0.6rem;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: none;
    color: var(--text-muted);
    cursor: pointer;
  }
  .clear-btn:hover {
    color: var(--text-h);
    border-color: var(--text-muted);
  }
  .program-list {
    list-style: none;
    margin: 0 0 1rem;
    padding: 0;
  }
  .program-list li {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.6rem;
    padding: 0.55rem 0.2rem;
    border-bottom: 1px solid var(--border-faint);
  }
  .program-info {
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
    min-width: 0;
  }
  .program-name {
    font-size: 0.9rem;
    color: var(--text-h);
  }
  .program-meta {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    font-size: 0.78rem;
    color: var(--text-muted);
  }
  .portfolio-badge {
    font-size: 0.68rem;
    padding: 0.08rem 0.45rem;
    border-radius: 999px;
    background: var(--surface-accent);
    color: var(--text-muted);
    border: 1px solid var(--border);
  }
  .remove {
    flex-shrink: 0;
    font: inherit;
    font-size: 1rem;
    line-height: 1;
    padding: 0.1rem 0.4rem;
    border: none;
    border-radius: 999px;
    background: none;
    color: var(--text-muted);
    cursor: pointer;
  }
  .remove:hover {
    background: var(--border-faint);
    color: var(--text-h);
  }
  .summarise-btn {
    font: inherit;
    font-size: 0.85rem;
    font-weight: 600;
    padding: 0.5rem 0.9rem;
    border: 1px solid var(--text-h);
    border-radius: 6px;
    background: var(--text-h);
    color: var(--bg);
    cursor: pointer;
  }
  .status {
    padding: 1.5rem 0;
    text-align: center;
    color: var(--text-muted);
  }
  .status.error {
    color: #b91c1c;
  }
</style>
