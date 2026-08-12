<script>
  // A labeled multi-select: a dropdown that adds one value at a time
  // (already-selected values are excluded from its own option list, so
  // it always reads as "pick the next one to add"), rendered as
  // removable pips below it. `selected` is bindable so the parent's own
  // filtering logic can react to it directly, the same as any other
  // $state array -- this component owns no filtering logic itself.
  let { label, options, selected = $bindable([]), placeholder = "Add…" } = $props();

  function add(value) {
    if (!value || selected.includes(value)) return;
    selected = [...selected, value];
  }

  function remove(value) {
    selected = selected.filter((v) => v !== value);
  }

  function handleChange(e) {
    add(e.target.value);
    e.target.value = ""; // reset back to the placeholder option
  }
</script>

<div class="multiselect">
  <select class="add-select" onchange={handleChange} aria-label={label}>
    <option value="">{placeholder}</option>
    {#each options.filter((o) => !selected.includes(o)) as o}
      <option value={o}>{o}</option>
    {/each}
  </select>
  {#if selected.length}
    <div class="pips">
      {#each selected as value (value)}
        <span class="pip">
          {value}
          <button type="button" class="pip-remove" onclick={() => remove(value)} aria-label={`Remove ${value}`}>
            ×
          </button>
        </span>
      {/each}
    </div>
  {/if}
</div>

<style>
  .multiselect {
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
    min-width: 0;
  }
  .add-select {
    font: inherit;
    font-size: 0.9rem;
    padding: 0.5rem 0.6rem;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--surface);
    color: var(--text);
  }
  .add-select:focus {
    outline: 2px solid var(--text-h);
    outline-offset: -1px;
  }
  .pips {
    display: flex;
    flex-wrap: wrap;
    gap: 0.35rem;
  }
  .pip {
    display: inline-flex;
    align-items: center;
    gap: 0.25rem;
    font-size: 0.78rem;
    padding: 0.15rem 0.25rem 0.15rem 0.6rem;
    border-radius: 999px;
    background: var(--surface-accent);
    color: var(--text);
    border: 1px solid var(--border);
    white-space: nowrap;
  }
  .pip-remove {
    font: inherit;
    font-size: 0.95rem;
    line-height: 1;
    padding: 0.05rem 0.3rem;
    border: none;
    border-radius: 999px;
    background: none;
    color: var(--text-muted);
    cursor: pointer;
  }
  .pip-remove:hover {
    background: var(--border-faint);
    color: var(--text-h);
  }
</style>
