// VITE_API_BASE is baked in at build time (Vite only ever exposes
// import.meta.env.VITE_*-prefixed vars, and only as of the build that
// reads them -- a static-site deploy can't pick up a changed env var
// without rebuilding). Render: set on the frontend static site to the
// backend web service's own URL + "/api", e.g.
// "https://budget-api.onrender.com/api" -- see render.yaml.
const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000/api";

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

  const res = await fetch(url);
  if (res.status === 404) {
    return null;
  }
  if (!res.ok) {
    throw new Error(`Request failed: ${res.status}`);
  }
  return res.json();
}

export async function fetchProgramProfile(programName) {
  const url = new URL(`${API_BASE}/measures/program-profile/`);
  url.searchParams.set("program_name", programName);

  const res = await fetch(url);
  if (res.status === 404) {
    throw new Error(`No program profile found for "${programName}"`);
  }
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

  const res = await fetch(url);
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

  const res = await fetch(url);
  if (res.status === 404) {
    throw new Error(`No outcome profile found for "${agency}"`);
  }
  if (!res.ok) {
    throw new Error(`Request failed: ${res.status}`);
  }
  return res.json();
}

// Only ingested for 2020-21 through 2025-26 so far (see build_bp2_db.py
// / KNOWN_GAPS.md), so a (measure, edition) pair can genuinely have no
// write-up -- that's not an error state, so a 404 resolves to null
// rather than throwing.
export async function fetchMeasureText(name, edition) {
  const url = new URL(`${API_BASE}/measures/text/`);
  url.searchParams.set("name", name);
  url.searchParams.set("edition", edition);

  const res = await fetch(url);
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
  const res = await fetch(`${API_BASE}/measures/list/`);
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

  const res = await fetch(url);
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
// one blocking fetch, so viewing a 10+ measure comparison set costs one
// round trip instead of N. See measure_combined()'s own docstring for the
// exact response shape, including `not_found` for any stale/typo'd id.
export async function fetchMeasureCombined(ids) {
  const url = new URL(`${API_BASE}/measures/combined/`);
  url.searchParams.set("ids", ids.join(","));

  const res = await fetch(url);
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

  const res = await fetch(url, { signal });
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

  const res = await fetch(url, { signal });
  if (!res.ok) {
    throw new Error(`Request failed: ${res.status}`);
  }
  return res.json();
}
