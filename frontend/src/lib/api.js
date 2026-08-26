// VITE_API_BASE is baked in at build time (Vite only ever exposes
// import.meta.env.VITE_*-prefixed vars, and only as of the build that
// reads them -- a static-site deploy can't pick up a changed env var
// without rebuilding). Render: set on the frontend static site to the
// backend web service's own URL + "/api", e.g.
// "https://budget-api.onrender.com/api" -- see render.yaml.
const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000/api";

// fetch() itself throws (a "Failed to fetch" TypeError, before any HTTP
// response even exists) on things like a dropped connection, a DNS
// blip, or -- the common case on Render's free tier -- the backend
// still spinning up from a cold start when the very first request
// after a period of inactivity lands. That's worth a short, bounded
// retry with backoff; an actual HTTP response (even a 4xx/5xx) is
// returned as-is on the first try and left to each caller's own status
// handling below, since that's a real answer from the server, not a
// transient failure to reach it at all.
const RETRY_ATTEMPTS = 3;
const RETRY_BASE_DELAY_MS = 500;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function fetchWithRetry(input, options = {}) {
  for (let attempt = 0; ; attempt++) {
    try {
      return await fetch(input, options);
    } catch (err) {
      // An aborted request (e.g. a superseded search-as-you-type query)
      // should fail immediately, not retry -- retrying it would just
      // race the very request that superseded it.
      if (options.signal?.aborted || attempt >= RETRY_ATTEMPTS - 1) throw err;
      await sleep(RETRY_BASE_DELAY_MS * 2 ** attempt);
    }
  }
}

// A measure_id can resolve to a measure with no measure_impacts/
// measure_programs row at all -- 18 of the 21 ingested BP2 editions
// have no PBS Table 1.2 data behind them (see measure_list()'s own
// docstring). A 404 there isn't an error, just "no $ breakdown for
// this one" -- resolves to null so the caller can render a text-only
// page instead of an error state.
export async function fetchMeasureDetail(name, edition) {
  const url = new URL(`${API_BASE}/measures/detail/`);
  url.searchParams.set("name", name);
  url.searchParams.set("edition", edition);

  const res = await fetchWithRetry(url);
  if (res.status === 404) {
    return null;
  }
  if (!res.ok) {
    throw new Error(`Request failed: ${res.status}`);
  }
  return res.json();
}

// portfolio is required, not just program_name: the same program name
// can legitimately mean two different things under two different
// portfolio eras (a machinery-of-government transfer, e.g. National
// Disability Insurance Scheme moving from Social Services to Health,
// Disability and Ageing at the 2026-27 Budget), and each needs its own
// independent profile -- see program_profile()'s own docstring.
export async function fetchProgramProfile(programName, portfolio) {
  const url = new URL(`${API_BASE}/measures/program-profile/`);
  url.searchParams.set("program_name", programName);
  url.searchParams.set("portfolio", portfolio);

  const res = await fetchWithRetry(url);
  if (res.status === 404) {
    throw new Error(`No program profile found for "${programName}" (${portfolio})`);
  }
  if (!res.ok) {
    throw new Error(`Request failed: ${res.status}`);
  }
  return res.json();
}

// Every ingested Budget edition's own raw multi-year estimate for this
// program (one "vintage" per edition, un-stitched -- see
// program_estimate_history()'s own docstring), plus the realised
// actual_series -- ProgramDeepDivePage's own chart data, showing how
// the Budget's own forecast for this program has moved with each round.
// portfolio required for the same reason fetchProgramProfile's is.
export async function fetchProgramEstimateHistory(programName, portfolio) {
  const url = new URL(`${API_BASE}/measures/program-estimate-history/`);
  url.searchParams.set("program_name", programName);
  url.searchParams.set("portfolio", portfolio);

  const res = await fetchWithRetry(url);
  if (res.status === 404) {
    throw new Error(`No estimate history found for "${programName}" (${portfolio})`);
  }
  if (!res.ok) {
    throw new Error(`Request failed: ${res.status}`);
  }
  return res.json();
}

// Curated "these might be the same program under a different name" list
// (see backend/measures/related_programs.py) -- ProgramDeepDivePage's
// suggestion box. Empty related list is the common case (most programs
// aren't part of any known succession), not an error.
export async function fetchRelatedPrograms(programName, portfolio) {
  const url = new URL(`${API_BASE}/measures/related-programs/`);
  url.searchParams.set("program_name", programName);
  url.searchParams.set("portfolio", portfolio);

  const res = await fetchWithRetry(url);
  if (!res.ok) {
    throw new Error(`Request failed: ${res.status}`);
  }
  return res.json();
}

// '2024-25 MYEFO' / '2024-25 Budget' -> '2024-25' -- program_expenses only
// has Budget editions, so a MYEFO measure's drilldown still needs the
// underlying budget year to query against.
export function budgetYear(edition) {
  const m = edition.match(/(\d{4}-\d{2})/);
  return m ? m[1] : edition;
}

export async function fetchPortfolioProfile(portfolio, editionOrBudgetYear) {
  const url = new URL(`${API_BASE}/measures/portfolio-profile/`);
  url.searchParams.set("portfolio", portfolio);
  url.searchParams.set("budget_year", budgetYear(editionOrBudgetYear));

  const res = await fetchWithRetry(url);
  if (res.status === 404) {
    throw new Error(`No portfolio profile found for "${portfolio}"`);
  }
  if (!res.ok) {
    throw new Error(`Request failed: ${res.status}`);
  }
  return res.json();
}

export async function fetchAgencyOutcomeProfile(agency, portfolio, editionOrBudgetYear) {
  const url = new URL(`${API_BASE}/measures/agency-outcome-profile/`);
  url.searchParams.set("agency", agency);
  url.searchParams.set("portfolio", portfolio);
  url.searchParams.set("budget_year", budgetYear(editionOrBudgetYear));

  const res = await fetchWithRetry(url);
  if (res.status === 404) {
    throw new Error(`No outcome profile found for "${agency}"`);
  }
  if (!res.ok) {
    throw new Error(`Request failed: ${res.status}`);
  }
  return res.json();
}

// Only ingested for 2020-21 through 2026-27 so far (see build_bp2_db.py
// / KNOWN_GAPS.md), so a (measure, edition) pair can genuinely have no
// write-up -- that's not an error state, so a 404 resolves to null
// rather than throwing.
export async function fetchMeasureText(name, edition) {
  const url = new URL(`${API_BASE}/measures/text/`);
  url.searchParams.set("name", name);
  url.searchParams.set("edition", edition);

  const res = await fetchWithRetry(url);
  if (res.status === 404) {
    return null;
  }
  if (!res.ok) {
    throw new Error(`Request failed: ${res.status}`);
  }
  return res.json();
}

// Every (measure_name, edition) pair across every ingested edition, each
// with the portfolios/agencies it touches and whether measure_detail can
// render a $ breakdown for it. Fetched once and filtered client-side by
// the search page -- see measure_list()'s own docstring for why. Gzipped
// server-side (see settings.py's GZipMiddleware) but still a sizeable
// payload on a slow connection, so also cached in localStorage:
// cachedMeasureList() returns instantly (possibly stale) while this
// function's own fresh fetch runs in the background -- see
// SearchPage.svelte's own stale-while-revalidate use of both.
const MEASURE_LIST_CACHE_KEY = "measureListCache.v1";

export async function fetchMeasureList() {
  const res = await fetchWithRetry(`${API_BASE}/measures/list/`);
  if (!res.ok) {
    throw new Error(`Request failed: ${res.status}`);
  }
  const data = await res.json();
  try {
    localStorage.setItem(MEASURE_LIST_CACHE_KEY, JSON.stringify(data));
  } catch {
    // Caching is a pure optimization (private browsing, a full/disabled
    // localStorage, a payload that's grown past the quota) -- never worth
    // failing the real fetch over.
  }
  return data;
}

// Synchronous, possibly-null, possibly-stale -- see fetchMeasureList's
// own docstring. No expiry check: this data only ever changes on a
// redeploy, and showing last-known results immediately while a fresh
// fetch quietly confirms/replaces them in the background is strictly
// better than a blank "Loading…" screen for however long that takes.
export function cachedMeasureList() {
  try {
    const raw = localStorage.getItem(MEASURE_LIST_CACHE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

// Resolves a short shareable-URL id (?m=<id>) back to {measure_name,
// edition} -- the lookup key measure_detail/measure_text actually use.
// Returns null on 404 (unknown/stale id) rather than throwing, so the
// caller can fall back to the search page instead of showing an error.
export async function resolveMeasureId(id) {
  const url = new URL(`${API_BASE}/measures/by-id/`);
  url.searchParams.set("id", id);

  const res = await fetchWithRetry(url);
  if (res.status === 404) {
    return null;
  }
  if (!res.ok) {
    throw new Error(`Request failed: ${res.status}`);
  }
  return res.json();
}

// Batched per-measure detail (the same shape fetchMeasureDetail returns
// for one measure) for several measures at once -- CombinedMeasuresPage's
// one blocking fetch, so viewing a 10+ measure summary set costs one
// round trip instead of N. See measure_combined()'s own docstring for the
// exact response shape, including `not_found` for any stale/typo'd id.
export async function fetchMeasureCombined(ids) {
  const url = new URL(`${API_BASE}/measures/combined/`);
  url.searchParams.set("ids", ids.join(","));

  const res = await fetchWithRetry(url);
  if (!res.ok) {
    throw new Error(`Request failed: ${res.status}`);
  }
  return res.json();
}

// Every (portfolio, agency, outcome, program) row across EVERY ingested
// edition's own program_expenses filing -- fetched once and filtered
// client-side as each level of the Portfolio -> Agency -> Outcome -> Program
// picker is chosen, the same stale-while-revalidate-free "just filter
// the one payload" pattern measure_list() already uses (a couple thousand
// rows, no need for four separate round trips per pick).
export async function fetchProgramHierarchy() {
  const res = await fetchWithRetry(`${API_BASE}/measures/program-hierarchy/`);
  if (!res.ok) {
    throw new Error(`Request failed: ${res.status}`);
  }
  return res.json();
}

// Every (outcome_number, program_name) combination across every ingested
// edition, portfolio/agency spellings folded together with one
// estimated_actual figure per year -- ProgramOutcomeAuditPage's manual
// data-quality review table. See program_outcome_audit()'s own docstring
// for why this groups differently (and more broadly) than every other
// program fetch here.
export async function fetchProgramOutcomeAudit() {
  const res = await fetchWithRetry(`${API_BASE}/measures/program-outcome-audit/`);
  if (!res.ok) {
    throw new Error(`Request failed: ${res.status}`);
  }
  return res.json();
}

// Every measure (any edition) whose own measure_programs rows resolve
// to this (program_name, portfolio) pair -- ProgramMeasuresPage's own
// per-program fetch, one call per selected program. portfolio is
// required, not just program_name: the same program name can
// legitimately mean two different things under two different
// portfolio eras (a machinery-of-government rename), and each needs
// its own measure set -- see measures_by_program()'s own docstring.
// Same per-measure shape fetchMeasureCombined returns.
export async function fetchMeasuresByProgram(programName, portfolio) {
  const url = new URL(`${API_BASE}/measures/by-program/`);
  url.searchParams.set("program_name", programName);
  url.searchParams.set("portfolio", portfolio);

  const res = await fetchWithRetry(url);
  if (!res.ok) {
    throw new Error(`Request failed: ${res.status}`);
  }
  return res.json();
}

// Server-side search over BP2 write-up text (intro/end prose and
// bulleted components), not the client-side-filtered name list --
// see measure_text_search()'s own docstring for why. Each result
// includes a snippet showing the match in context.
//
// Both this and fetchMeasureTopicSearch below take an optional
// AbortSignal -- SearchPage.svelte uses it to cancel a still-in-flight
// request when a newer query supersedes it (typing ahead) or when it's
// taking too long, so a slow response can't land late and overwrite
// fresher results, and a stuck request doesn't spin forever with no
// way out.
export async function fetchMeasureTextSearch(query, { signal } = {}) {
  const url = new URL(`${API_BASE}/measures/search-text/`);
  url.searchParams.set("q", query);

  const res = await fetchWithRetry(url, { signal });
  if (!res.ok) {
    throw new Error(`Request failed: ${res.status}`);
  }
  return res.json();
}

// Semantic search over BP2 write-ups via chromadb embeddings (see
// build_measure_embeddings.py / measure_topic_search()'s own docstring)
// -- surfaces measures whose text is *about* the query topic even when
// the exact words never appear (e.g. "child care" matching a measure
// that only ever says "Early Childhood Education and Care (ECEC)").
// Same result shape as fetchMeasureTextSearch, plus a `score` (0..1
// similarity) since there's no exact match position for a snippet.
// The heaviest of the three search modes -- an embedding model has to
// run server-side on every query, not just a DB lookup -- so it's the
// one most exposed to a cold/slow backend (see SearchPage's own
// timeout handling).
export async function fetchMeasureTopicSearch(query, { signal } = {}) {
  const url = new URL(`${API_BASE}/measures/search-topic/`);
  url.searchParams.set("q", query);

  const res = await fetchWithRetry(url, { signal });
  if (!res.ok) {
    throw new Error(`Request failed: ${res.status}`);
  }
  return res.json();
}
