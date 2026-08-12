<script>
  import { fetchMeasureText } from "./api.js";

  // hasFinancialData: false when the page has no measure_impacts/
  // measure_programs row to show (18 of the 21 ingested BP2 editions --
  // see measure_list()'s own docstring) -- changes the framing text and
  // shows the portfolio here, since there's no PBS-derived portfolio
  // badge elsewhere on the page for this measure in that case.
  let { name, edition, hasFinancialData = true, onselect } = $props();

  let textPromise = $derived(fetchMeasureText(name, edition));

  // Components come back as a flat, ordinal-ordered list mixing prose
  // paragraphs (marker "text") with bullets ("dot"/"dash") in the exact
  // order they appear in the paper -- a level-2 row's parent_ordinal
  // points at its level-1 parent (the "Government will also:" case).
  // Nest dashes under their dot here purely for rendering; the API keeps
  // the flat shape since that's the actual source order.
  function nestComponents(components) {
    const byOrdinal = new Map(components.map((c) => [c.ordinal, { ...c, children: [] }]));
    const roots = [];
    for (const c of byOrdinal.values()) {
      if (c.parent_ordinal != null && byOrdinal.has(c.parent_ordinal)) {
        byOrdinal.get(c.parent_ordinal).children.push(c);
      } else {
        roots.push(c);
      }
    }
    return roots;
  }

  // Group consecutive bullet roots into one <ul> each, so two separate
  // bullet runs either side of a paragraph (rather than one continuous
  // list) still read as two lists, matching the paper's own layout.
  function groupBlocks(roots) {
    const blocks = [];
    let currentList = null;
    for (const r of roots) {
      if (r.marker === "text") {
        currentList = null;
        blocks.push({ type: "text", text: r.text });
      } else {
        if (!currentList) {
          currentList = { type: "list", items: [] };
          blocks.push(currentList);
        }
        currentList.items.push(r);
      }
    }
    return blocks;
  }
</script>

{#await textPromise then text}
  {#if text}
    <section class="measure-text">
      <h2>About this measure</h2>
      <p class="section-note">
        {#if hasFinancialData}
          From Budget Paper No. 2's own write-up ({text.edition}, p.{text.source_page}) --
          a separate source document from the agency PBS tables above.
        {:else}
          From Budget Paper No. 2's own write-up ({text.edition}, p.{text.source_page}) --
          the only source available for this measure; no agency PBS Table 1.2 data has
          been ingested for this edition (see KNOWN_GAPS.md).
        {/if}
      </p>
      {#if !hasFinancialData && text.portfolio}
        <p class="portfolio-line"><span class="portfolio-badge">{text.portfolio}</span></p>
      {/if}

      {#each groupBlocks(nestComponents(text.components)) as block}
        {#if block.type === "text"}
          <p class="prose">{block.text}</p>
        {:else}
          <ul class="components">
            {#each block.items as c}
              <li>
                {c.text}
                {#if c.children.length}
                  <ul class="subcomponents">
                    {#each c.children as sub}
                      <li>{sub.text}</li>
                    {/each}
                  </ul>
                {/if}
              </li>
            {/each}
          </ul>
        {/if}
      {/each}

      {#if text.related_measures.length}
        <div class="related">
          <span class="related-label">Related measures</span>
          <div class="related-list">
            {#each text.related_measures as r}
              <button
                class="related-badge"
                onclick={() => onselect(r.measure_id, r.measure_name, r.edition)}
              >
                {r.phrase}
              </button>
            {/each}
          </div>
        </div>
      {/if}

      {#if text.headline_financials.length}
        <details class="financials">
          <summary>Budget Paper No. 2 headline financials ($m)</summary>
          <table>
            <tbody>
              {#each text.headline_financials as row}
                <tr>
                  <td class="row-label">
                    {row.department_name}
                    {#if row.is_related}<span class="related-tag">related {row.impact_type.toLowerCase()}</span>{/if}
                  </td>
                  {#each row.values as v}
                    <td class="value-cell">{v.value_raw}</td>
                  {/each}
                </tr>
              {/each}
            </tbody>
          </table>
        </details>
      {/if}
    </section>
  {/if}
{/await}

<style>
  .measure-text {
    margin-bottom: 2.5rem;
  }
  h2 {
    font-size: 1rem;
    text-transform: uppercase;
    letter-spacing: 0.03em;
    color: var(--text-muted);
    margin-bottom: 0.75rem;
  }
  .section-note {
    font-size: 0.82rem;
    color: var(--text-muted);
    margin: -0.4rem 0 1rem;
  }
  .portfolio-line {
    margin: 0 0 1rem;
  }
  .portfolio-badge {
    display: inline-block;
    font-size: 0.72rem;
    padding: 0.1rem 0.5rem;
    border-radius: 999px;
    background: var(--surface-accent);
    color: var(--text-muted);
    border: 1px solid var(--border);
  }
  .prose {
    font-size: 0.92rem;
    line-height: 1.55;
    margin: 0 0 0.85rem;
  }
  .components {
    margin: 0 0 1.25rem;
    padding-left: 1.2rem;
    font-size: 0.92rem;
    line-height: 1.55;
  }
  .components > li {
    margin-bottom: 0.5rem;
  }
  .subcomponents {
    margin: 0.35rem 0 0;
    padding-left: 1.2rem;
    list-style-type: "– ";
  }
  .subcomponents li {
    margin-bottom: 0.3rem;
  }
  .related {
    margin-bottom: 1.25rem;
  }
  .related-label {
    display: block;
    font-size: 0.72rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.03em;
    color: var(--text-muted);
    margin-bottom: 0.4rem;
  }
  .related-list {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
  }
  .related-badge {
    font: inherit;
    font-size: 0.8rem;
    font-style: italic;
    padding: 0.2rem 0.6rem;
    border-radius: 999px;
    border: 1px solid var(--border);
    background: var(--surface);
    color: var(--text-muted);
    cursor: pointer;
    transition: background 0.15s, border-color 0.15s;
  }
  .related-badge:hover {
    border-color: var(--text-muted);
    color: var(--text-h);
  }
  .financials summary {
    font-size: 0.82rem;
    color: var(--text-muted);
    cursor: pointer;
    margin-bottom: 0.6rem;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.85rem;
  }
  td {
    padding: 0.35rem 0.5rem;
    border-bottom: 1px solid var(--border-faint);
  }
  .row-label {
    text-align: left;
  }
  .related-tag {
    display: inline-block;
    margin-left: 0.4rem;
    font-size: 0.7rem;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.02em;
  }
  .value-cell {
    text-align: right;
    font-variant-numeric: tabular-nums;
  }
</style>
