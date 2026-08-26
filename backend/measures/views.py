import re
import sys
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path

from django.db.models import Q
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import (
    AgencyAlias,
    MeasureImpact,
    MeasureProgram,
    MeasureText,
    MeasureTextComponent,
    MeasureTextHeadlineFinancial,
    MeasureTextRelated,
    ProgramExpense,
)
from .serializers import MeasureImpactSerializer
from .related_programs import find_related

# Portfolio canonicalization (PORTFOLIO_ALIASES, canon_portfolio) lives in
# the repo-root portfolio_aliases module, not here -- build_db.py (a
# standalone script with no Django dependency) needs the exact same
# lookup for normalize_program_name_casing(), and this module's own
# Django-only imports rule out the reverse direction. The repo root isn't
# on sys.path by default under Django (only this app's own package is),
# so it's added explicitly -- backend/measures/views.py -> backend/
# measures -> backend -> repo root is parents[2].
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from portfolio_aliases import (
    PORTFOLIO_ALIASES as _PORTFOLIO_ALIASES,
    canon_portfolio as _canon_portfolio,
    strip_portfolio_prefix as _strip_portfolio_prefix,
)

BUDGET_YEAR_RE = re.compile(r"(\d{4}-\d{2})")

# The latest ingested Budget edition -- its own "budget" and "forward_estimate"
# rows are the government's most up to date prediction for years not yet
# covered by an estimated_actual. Update this each time a newer Budget is
# ingested into program_expenses.
LATEST_BUDGET_EDITION = "2026-27 Budget"

# 2022-23 October Budget is the one edition whose own estimated_actual is
# never used -- not preferred over another edition's for the same fiscal
# year, and not even used as a last-resort fallback when no other edition
# covers that year for a given program (confirmed superseded once normal
# annual reporting resumed with the 2023-24 Budget; if a program's own
# portfolio/outcome happened to change right at that boundary, a genuine
# blank for that year is more honest than borrowing a since-superseded
# figure). Shared by _stitch_series and program_estimate_history's own
# actual_series below -- keep both in sync with this, not a local copy.
EXCLUDED_ACTUAL_EDITIONS = {"2022-23 October Budget"}


_PORTFOLIO_VARIANTS = {}
for _raw, _canon in _PORTFOLIO_ALIASES.items():
    _PORTFOLIO_VARIANTS.setdefault(_canon, {_canon}).add(_raw)


def _portfolio_history(portfolio):
    """Every raw program_expenses.portfolio spelling that canonicalizes to
    the same real portfolio as `portfolio` -- e.g. given "Foreign Affairs
    and Trade", also returns "DFAT", "Foreign Affairs", "Foreign Affairs &
    Trade", "Foriegn Affairs & Trade". Same idea as _agency_history() above,
    but portfolio canonicalization is a static, hand-built dict rather than
    a per-file derived alias table, so no DB lookup is needed -- just the
    reverse of _PORTFOLIO_ALIASES itself.

    Needed because program_expenses.portfolio is stored as each edition's
    own raw folder-derived string, never canonicalized at ingest time
    (unlike agency, which is at least consistent within one edition/file).
    Without this, program_profile()/program_estimate_history()'s own
    portfolio filter -- an exact match against that raw column -- would
    silently drop every edition whose folder happened to spell the same
    real portfolio differently, since the picker that feeds them
    (program_hierarchy()) already shows one canonical spelling regardless
    of which edition's row was actually picked."""
    canon = _canon_portfolio(portfolio)
    return _PORTFOLIO_VARIANTS.get(canon, {canon, portfolio})


def _budget_year(edition):
    """'2024-25 MYEFO' / '2024-25 Budget' -> '2024-25' -- the year
    program_expenses.budget_year uses, to resolve a program's name/outcome
    for that specific year (numbers get reused for different programs
    across years, so the year matters)."""
    m = BUDGET_YEAR_RE.search(edition)
    return m.group(1) if m else edition


def _resolve_agencies(agencies, budget_year):
    """Map each of a measure's agency strings (typically the formal name,
    typed by hand into a Table 1.2 sheet -- "Services Australia") to the
    exact spelling program_expenses.agency uses for the same budget_year
    (typically filename-derived -- "SAUS", "SA", ...). Both spellings come
    from the agency's own single PBS workbook for that year, so
    agency_aliases (built by build_agency_aliases.py from each agency's
    Table 1.1 title) gives an exact answer, not a guess -- there is only
    one government-published PBS document set per year, so same-year
    agency identity should never need fuzzy string matching. (Cross-year
    drift, where the same real agency is renamed/restructured over time,
    is a different problem -- deliberately not addressed here; see
    program_profile() below, which joins on program_name alone instead.)

    Returns {original_agency: resolved_agency} -- resolved_agency is the
    original string unchanged if no alias applies (the common case where
    the two sides already agree, e.g. a measure with no separate agency
    row, seeded from the filing agency's own filename-derived name).
    """
    aliases = AgencyAlias.objects.filter(budget_year=budget_year).values(
        "formal_name", "short_name"
    )
    by_formal_name = {a["formal_name"].lower(): a["short_name"] for a in aliases}
    return {a: by_formal_name.get(a.lower(), a) for a in agencies}


def _agency_history(agency, portfolio):
    """Every known program_expenses.agency spelling for the same real
    agency within `portfolio`, across every ingested edition -- e.g.
    given "SAUS" (2024-25's spelling for Services Australia), also
    returns "SA", "SAus", "Budget SAus", "Services Australia", ... This
    is NOT a guess: agency_aliases pairs a formal name with a short form
    per (edition, file), each pairing individually verified from that
    year's own Table 1.1 title, so chaining every short form that shares
    a formal name (scoped to this portfolio, since a short abbreviation
    can be reused by an unrelated agency in a different portfolio -- see
    KNOWN_GAPS.md #4) reconstructs one real agency's full naming history
    deterministically. `agency` may be either a formal name or a short
    name -- both directions are checked. Falls back to just {agency}
    unchanged if it has no alias entry at all (e.g. a Defence-style file
    with no parseable Table 1.1).
    """
    formal_names = set(
        AgencyAlias.objects.filter(portfolio=portfolio, short_name=agency)
        .values_list("formal_name", flat=True)
    )
    formal_names |= set(
        AgencyAlias.objects.filter(
            portfolio=portfolio, formal_name__iexact=agency
        ).values_list("formal_name", flat=True)
    )
    if not formal_names:
        return {agency}
    short_names = set(
        AgencyAlias.objects.filter(
            portfolio=portfolio, formal_name__in=formal_names
        ).values_list("short_name", flat=True)
    )
    short_names.add(agency)
    return short_names


def _display_agency(agency, edition):
    """Cleans up a raw agency string for display -- same idea as
    _canon_portfolio() for portfolios. Most editions' measure_impacts/
    measure_programs.agency is already a formal, human-typed name (e.g.
    "Services Australia"), so this is a no-op for them. A few editions
    (e.g. 2022-23 October Budget, a rushed one-off with abbreviated PBS
    workbooks) instead stored the filename-derived short form directly
    (e.g. "OCT EDU") -- agency_aliases already has that exact pairing
    (short_name "OCT EDU" -> formal_name "Department of Education", from
    that agency's own Table 1.1 title), so look it up and use the formal
    name instead.

    Safe for the frontend to feed straight back into
    agency_outcome_profile() for a drilldown, unlike portfolio (see
    measure_detail's own docstring): _agency_history() above already
    checks both the short_name and formal_name direction, so either
    form resolves to the same result. Falls through to the raw value
    unchanged if no alias row matches.
    """
    formal_name = (
        AgencyAlias.objects.filter(edition=edition, short_name=agency)
        .values_list("formal_name", flat=True)
        .first()
    )
    return formal_name or agency


def _stitch_series(rows, latest_edition=LATEST_BUDGET_EDITION):
    """rows: dicts with fiscal_year, estimate_type, edition, amount_thousands
    -- possibly several rows sharing the same (fiscal_year, estimate_type,
    edition), e.g. one per program when aggregating a whole agency or
    outcome, which get summed first. Then, per fiscal year: prefer the
    estimated_actual figure (the most authoritative retrospective figure,
    reported by the Budget edition immediately following that year); for a
    year with no estimated_actual yet, use latest_edition's own
    budget/forward_estimate figure. Shared by program_profile,
    portfolio_profile, and agency_outcome_profile below.

    Callers MUST pass `rows` ordered by edition -- excluding
    EXCLUDED_ACTUAL_EDITIONS (module-level, above) from ever contributing
    an estimated_actual figure settles the one real case of two editions
    both claiming the same fiscal_year's estimated_actual, so there's
    nothing left to overwrite there; order still matters for the final
    budget/forward_estimate fallback pass (must land on latest_edition
    specifically), so every call site below keeps `.order_by("edition")`
    regardless.

    2022-23 October Budget is the one case where two editions both claim
    the same fiscal_year's estimated_actual (both it and 2022-23 March
    Budget restate 2021-22) -- see EXCLUDED_ACTUAL_EDITIONS above for why
    its own figure is never used at all, not even as a last resort.
    """
    summed = defaultdict(int)
    for r in rows:
        summed[(r["fiscal_year"], r["estimate_type"], r["edition"])] += r[
            "amount_thousands"
        ]

    by_fiscal_year = {}
    for (fy, etype, ed), amt in summed.items():
        if etype == "estimated_actual" and ed not in EXCLUDED_ACTUAL_EDITIONS:
            by_fiscal_year[fy] = {
                "fiscal_year": fy,
                "estimate_type": etype,
                "edition": ed,
                "amount_thousands": amt,
            }
    for (fy, etype, ed), amt in summed.items():
        if fy not in by_fiscal_year and ed == latest_edition:
            by_fiscal_year[fy] = {
                "fiscal_year": fy,
                "estimate_type": etype,
                "edition": ed,
                "amount_thousands": amt,
            }

    return sorted(by_fiscal_year.values(), key=lambda r: r["fiscal_year"])


@api_view(["GET"])
def measure_detail(request):
    """?name=<measure_name>&edition=<edition> -> one measure's full profile:
    its $ impact by portfolio/agency/direction/fiscal year, and the list of
    programs it touches (with program name/outcome resolved for that year
    where possible).

    portfolios below is deliberately raw -- NOT run through
    _canon_portfolio() the way measure_list's own is. The frontend feeds
    a badge from this exact list straight into portfolio_profile() as
    its `portfolio` query param (MeasurePage.svelte's selectPortfolio),
    which does an exact-string match against program_expenses.portfolio
    -- a different table, built by a different pipeline (build_db.py),
    whose own portfolio strings are independently messy in a way this
    endpoint's canonicalization has no visibility into (see KNOWN_GAPS.md
    #4). Cleaning up the display string here would silently break that
    join for any portfolio whose canonical form doesn't happen to match
    what program_expenses actually stored.
    """
    measure_name = request.query_params.get("name")
    edition = request.query_params.get("edition")
    if not measure_name or not edition:
        return Response(
            {"detail": "name and edition query params are required"}, status=400
        )

    detail = _build_measure_detail(measure_name, edition)
    if detail is None:
        return Response({"detail": "not found"}, status=404)

    return Response(detail)


def _build_measure_detail(measure_name, edition):
    """The shared body of measure_detail(): every $ impact and touched
    program for one (measure_name, edition), or None if neither
    measure_impacts nor measure_programs has a row for it (a BP2-only
    measure -- see measure_text() for that case instead). Factored out
    of measure_detail() so measure_combined() (below) can build the same
    per-measure shape for several measures in one request, without a
    frontend round trip per measure."""
    impacts = MeasureImpact.objects.filter(measure_name=measure_name, edition=edition)
    programs = MeasureProgram.objects.filter(measure_name=measure_name, edition=edition)

    if not impacts.exists() and not programs.exists():
        return None

    measure_id = (impacts.first() or programs.first()).measure_id
    budget_year = _budget_year(edition)
    agencies_touched = {p.agency for p in programs}
    # Alias chain per (agency, portfolio) touched, not a single-budget_year
    # _resolve_agencies lookup: that only succeeds when *this exact* year's
    # own Table 1.1 title spells out the long form -- some years' titles
    # already use the abbreviation program_expenses itself uses (e.g. the
    # Aged Care Quality and Safety Commission's 2025-26 filing), in which
    # case a single-year lookup finds nothing and every one of that
    # agency's programs falls through to "unresolved". _agency_history
    # checks both directions and every known spelling for the real agency
    # within that portfolio, so it resolves regardless of which form this
    # particular year happens to use.
    agency_name_sets = {}
    for p in programs:
        key = (p.agency, p.portfolio)
        if key not in agency_name_sets:
            agency_name_sets[key] = _agency_history(p.agency, p.portfolio)

    all_candidate_names = set().union(*agency_name_sets.values()) if agency_name_sets else set()

    program_rows = ProgramExpense.objects.filter(
        budget_year=budget_year, agency__in=all_candidate_names
    ).values(
        "agency",
        "program_number",
        "program_name",
        "outcome_number",
        "outcome_description",
    )
    program_lookup = {(r["agency"], r["program_number"]): r for r in program_rows}

    program_data = []
    for p in programs:
        resolved = None
        for candidate in agency_name_sets[(p.agency, p.portfolio)]:
            resolved = program_lookup.get((candidate, p.program_number))
            if resolved:
                break
        if resolved:
            program_name = resolved["program_name"]
            outcome_number = resolved["outcome_number"]
            outcome_description = resolved["outcome_description"]
        elif p.is_departmental:
            # "X.0" synthesized convention: this outcome's departmental
            # allocation as a whole, not a real numbered program.
            outcome_num = p.program_number.split(".")[0]
            program_name = f"Departmental (Outcome {outcome_num})"
            outcome_number = int(outcome_num) if outcome_num.isdigit() else None
            outcome_description = None
        else:
            program_name = None
            outcome_number = None
            outcome_description = None
        program_data.append(
            {
                "portfolio": p.portfolio,
                "agency": p.agency,
                "direction": p.direction,
                "program_number": p.program_number,
                "is_departmental": p.is_departmental,
                "program_name": program_name,
                "outcome_number": outcome_number,
                "outcome_description": outcome_description,
            }
        )

    portfolios = sorted({i.portfolio for i in impacts} | {p.portfolio for p in programs})

    # Cleaned up for display -- see _display_agency's own docstring for
    # why this is safe even for the badge that gets fed back into
    # agency_outcome_profile(), unlike portfolios above. One lookup per
    # distinct raw agency string, then applied consistently everywhere
    # it appears (the top-level list, each impact row, each program
    # row) so a badge, a chart's agency picker, and a table row all
    # agree -- and so downstream string matching (e.g. MeasurePage's own
    # selectAgency, which looks up a program by its agency string)
    # still finds the row it's looking for.
    raw_agencies = agencies_touched | {i.agency for i in impacts}
    agency_display = {a: _display_agency(a, edition) for a in raw_agencies}

    impacts_data = MeasureImpactSerializer(impacts, many=True).data
    for row in impacts_data:
        row["agency"] = agency_display.get(row["agency"], row["agency"])
    for row in program_data:
        row["agency"] = agency_display.get(row["agency"], row["agency"])

    agencies = sorted({agency_display.get(a, a) for a in raw_agencies})

    return {
        "measure_id": measure_id,
        "measure_name": measure_name,
        "edition": edition,
        "portfolios": portfolios,
        "agencies": agencies,
        "impacts": impacts_data,
        "programs": program_data,
    }


@api_view(["GET"])
def program_profile(request):
    """?program_name=<name>&portfolio=<portfolio> -> one program's
    long-run financial profile, stitched from every ingested Budget
    edition (2017-18 through the latest): the estimated_actual figure
    for every year that has one (the most authoritative retrospective
    figure available -- reported by the Budget edition immediately
    following that year), and for years with no estimated_actual yet,
    the latest Budget edition's own budget/forward_estimate figure.

    portfolio is required, not optional -- same reasoning as
    measures_by_program()'s own docstring: a program_name can legitimately
    mean two different things under two different portfolio eras (e.g. a
    machinery-of-government transfer, not just a spelling drift), and
    each needs its own independent profile rather than one merged/
    stitched line. Unlike portfolio, agency is deliberately NOT part of
    the key -- agency naming drifts heavily across calendar years for
    the exact same real program within one portfolio (e.g. this
    program's agency has been spelled "DET", "ESE", "Education, Skills
    and Employment DESE", and "Education" across ingested editions),
    and program_name is the stable identifier program_expenses was
    built around for exactly that case.

    The portfolio match itself is expanded via _portfolio_history() to
    every raw spelling of the same real portfolio (see its own docstring)
    -- program_hierarchy() (the picker this feeds) already shows one
    canonical portfolio name regardless of which edition's row was
    actually picked, so an exact-string match here would silently drop
    every edition whose folder spelled that same real portfolio
    differently (confirmed: "Promotion of Australia's export and other
    international economic interests" (DFAT) looked like it was missing
    most editions' data purely because its portfolio folder was named
    "Foreign Affairs", "DFAT", "Foreign Affairs & Trade" and "Foriegn
    Affairs & Trade" in different years).
    """
    program_name = request.query_params.get("program_name")
    portfolio = request.query_params.get("portfolio")
    if not program_name or not portfolio:
        return Response(
            {"detail": "program_name and portfolio query params are required"}, status=400
        )

    rows = list(
        ProgramExpense.objects.filter(
            program_name=program_name, portfolio__in=_portfolio_history(portfolio)
        ).values(
            "edition",
            "agency",
            "fiscal_year",
            "estimate_type",
            "amount_thousands",
            "outcome_number",
            "outcome_description",
        ).order_by("edition")
    )
    if not rows:
        return Response({"detail": "not found"}, status=404)

    series = _stitch_series(rows)
    meta = next(
        (r for r in rows if r["edition"] == LATEST_BUDGET_EDITION), rows[-1]
    )

    return Response(
        {
            "program_name": program_name,
            "portfolio": portfolio,
            "outcome_number": meta["outcome_number"],
            "outcome_description": meta["outcome_description"],
            "series": series,
        }
    )


@api_view(["GET"])
def program_estimate_history(request):
    """?program_name=<name>&portfolio=<portfolio> -> the raw material
    for a "how has the Budget's own forecast for this program moved
    with each round" chart: one "vintage" series per ingested edition
    (exactly what that Budget round itself reported for this program --
    its current year's budget figure plus its own forward estimates for
    the following years), alongside actual_series -- the realised
    estimated_actual figure for every year one exists, the same
    authoritative figures program_profile()'s own stitched series
    prefers.

    Unlike program_profile(), nothing here is stitched/deduplicated
    across editions -- every edition's own rows survive as their own
    line, which is the whole point (seeing e.g. the 2022-23 October
    Budget's forecast diverge from the 2022-23 March Budget's, or every
    edition's forward estimates gradually converging toward the eventual
    actual). portfolio is required, unscoped only by agency, for the
    same reason program_profile() is -- see its own docstring. The
    portfolio match is likewise expanded via _portfolio_history() -- see
    program_profile()'s own docstring for why an exact match would
    silently drop editions whose folder spelled the same real portfolio
    differently.
    """
    program_name = request.query_params.get("program_name")
    portfolio = request.query_params.get("portfolio")
    if not program_name or not portfolio:
        return Response(
            {"detail": "program_name and portfolio query params are required"}, status=400
        )

    rows = list(
        ProgramExpense.objects.filter(
            program_name=program_name, portfolio__in=_portfolio_history(portfolio)
        ).values(
            "edition",
            "budget_year",
            "fiscal_year",
            "estimate_type",
            "amount_thousands",
            "outcome_number",
            "outcome_description",
        )
    )
    if not rows:
        return Response({"detail": "not found"}, status=404)

    # Summed first, same reasoning as _stitch_series's own first pass --
    # a program spanning more than one agency within a single edition's
    # filing gets combined into one figure per (edition, fiscal_year,
    # estimate_type) before it becomes one point on that edition's line.
    summed = defaultdict(int)
    for r in rows:
        key = (r["edition"], r["budget_year"], r["fiscal_year"], r["estimate_type"])
        summed[key] += r["amount_thousands"]

    by_edition = defaultdict(list)
    budget_year_by_edition = {}
    for (edition, budget_year, fy, etype), amt in summed.items():
        budget_year_by_edition[edition] = budget_year
        by_edition[edition].append(
            {"fiscal_year": fy, "estimate_type": etype, "amount_thousands": amt}
        )

    vintages = [
        {
            "edition": edition,
            "budget_year": budget_year_by_edition[edition],
            "series": sorted(points, key=lambda p: p["fiscal_year"]),
        }
        for edition, points in by_edition.items()
    ]
    # (budget_year, edition) rather than budget_year alone -- two editions
    # can share one budget_year (a mid-year update, e.g. "2022-23 March
    # Budget" vs "2022-23 October Budget"), and need a stable, correctly
    # chronological order between them; sorting the edition string second
    # happens to already put "March" before "October" for every such pair
    # ingested so far.
    vintages.sort(key=lambda v: (v["budget_year"], v["edition"]))

    # Excludes EXCLUDED_ACTUAL_EDITIONS the same way _stitch_series does
    # (see its own docstring) -- this function doesn't call _stitch_series
    # itself (it needs the full per-edition vintages list too, not just
    # the stitched actual line), so the same exclusion has to be applied
    # here independently. Keep both in sync.
    actual_by_fy = {}
    for (edition, budget_year, fy, etype), amt in summed.items():
        if etype == "estimated_actual" and edition not in EXCLUDED_ACTUAL_EDITIONS:
            actual_by_fy[fy] = amt
    actual_series = [
        {"fiscal_year": fy, "amount_thousands": amt}
        for fy, amt in sorted(actual_by_fy.items())
    ]

    meta = next(
        (r for r in rows if r["edition"] == LATEST_BUDGET_EDITION), rows[-1]
    )

    return Response(
        {
            "program_name": program_name,
            "portfolio": portfolio,
            "outcome_number": meta["outcome_number"],
            "outcome_description": meta["outcome_description"],
            "vintages": vintages,
            "actual_series": actual_series,
        }
    )


@api_view(["GET"])
def related_programs(request):
    """?program_name=<name>&portfolio=<portfolio> -> other programs a human
    has confirmed are almost certainly the same real function under a
    different legal name (see related_programs.py's own module docstring
    for why this is a curated list, not something detected here) -- e.g.
    viewing "Administrative Review Tribunal" surfaces "Administrative
    Appeals Tribunal" and "...and Immigration Assessment Authority" as its
    predecessors. Feeds ProgramDeepDivePage's "these might be the same
    program" suggestion, which lets the user decide whether to view them
    combined (via the ordinary multi-program comparison page) rather than
    silently merging them anywhere identity is used for real.

    Each returned program includes its own earliest/latest edition
    (expanded across every raw portfolio spelling via _portfolio_history,
    same as program_profile() itself) so the suggestion can show *when*
    each one was in use, not just its name.
    """
    program_name = request.query_params.get("program_name")
    portfolio = request.query_params.get("portfolio")
    if not program_name or not portfolio:
        return Response(
            {"detail": "program_name and portfolio query params are required"}, status=400
        )

    note, others = find_related(program_name, _canon_portfolio(portfolio))
    if not others:
        return Response({"note": None, "related": []})

    related = []
    for other in others:
        editions = list(
            ProgramExpense.objects.filter(
                program_name=other["program_name"],
                portfolio__in=_portfolio_history(other["portfolio"]),
            ).values_list("edition", "budget_year").distinct()
        )
        if not editions:
            continue
        editions.sort(key=lambda e: (e[1], e[0]))
        related.append({
            "program_name": other["program_name"],
            "portfolio": other["portfolio"],
            "earliest_edition": editions[0][0],
            "latest_edition": editions[-1][0],
        })

    return Response({"note": note, "related": related})


_program_hierarchy_cache = None
_program_hierarchy_lock = threading.Lock()


@api_view(["GET"])
def program_hierarchy(request):
    """-> every (portfolio, agency, outcome, program) combination across
    EVERY ingested edition's own program_expenses filing -- the full tree
    for the Portfolio -> Agency -> Outcome -> Program picker
    (CombinedMeasuresPage's "start from a program instead of a measure"
    entry point). Returned flat, one row per program, rather than
    pre-nested: ~2,800 rows across every year is still small enough that
    the same client-side-cascading-filter pattern measure_list() already
    uses (fetch once, filter as each picker level is chosen) is simpler
    than four separate round trips per pick.

    Cached in-process after the first call (_program_hierarchy_cache):
    unlike every other view in this file, this one reads and reprocesses
    the WHOLE program_expenses table (~20k rows across every ingested
    edition, a ~500KB/1,600-row response) rather than something scoped to
    one measure/program/agency -- measured at ~0.4-0.6s recomputed from
    scratch on every request, next to ~15ms for a single-measure fetch.
    That's cheap on a fast dev machine but exactly the kind of per-request
    cost that can stretch past a timeout under a resource-constrained
    host's CPU throttling. The underlying data only ever changes via a
    full DB rebuild + redeploy, which always restarts the process (and
    so clears this module-level cache) anyway -- there's nothing to
    invalidate it for otherwise. Guarded by a lock since gunicorn's
    gthread workers mean several threads share this module: without it,
    two requests landing before the first finishes populating the cache
    would each redundantly repeat the full computation.
    """
    global _program_hierarchy_cache
    if _program_hierarchy_cache is not None:
        print("program_hierarchy: served from cache", flush=True)
        return Response(_program_hierarchy_cache)

    with _program_hierarchy_lock:
        if _program_hierarchy_cache is not None:
            print("program_hierarchy: served from cache (built while waiting for lock)", flush=True)
            return Response(_program_hierarchy_cache)
        print("program_hierarchy: cache miss, building...", flush=True)
        t0 = time.time()
        _program_hierarchy_cache = _build_program_hierarchy()
        print(
            f"program_hierarchy: cache built in {time.time() - t0:.2f}s "
            f"({len(_program_hierarchy_cache)} rows)",
            flush=True,
        )
    return Response(_program_hierarchy_cache)


def _build_program_hierarchy():
    """The actual computation behind program_hierarchy() -- split out so
    the caching/locking above reads cleanly as "check cache, else build
    once and cache it," not tangled up with the query/dedup logic itself.

    Deliberately NOT scoped to the latest edition alone (an earlier
    version was, and only showed the current year's own agencies/
    portfolios/programs -- a real gap: a portfolio/program since renamed
    or restructured out of the current filing was simply invisible to
    the picker, even though program_name -- what actually gets selected
    -- is a stable, cross-year identity everywhere else in this file, so
    there's no reason browsing should be limited to one year's snapshot).

    Both portfolio and agency are cleaned up for display -- portfolio via
    _canon_portfolio() (display only, same reasoning as measure_list()'s
    own use of it -- this feeds a picker, never an exact-match join), and
    agency via the exact same edition-scoped alias lookup _display_agency()
    already does for measure agency badges elsewhere (some editions, e.g.
    2022-23 October Budget, store the filename-derived short form
    directly -- "OCT EDU" -- rather than a formal name). Bulk-resolved
    here (one query for every AgencyAlias row, not one per program_expenses
    row) since this endpoint can touch a few thousand rows in one call,
    unlike _build_measure_detail's own per-measure use of the same lookup.

    Cross-year agency-spelling drift for the same real agency (see
    _agency_history's own docstring) is NOT bridged here -- the same
    department can still appear more than once under different historical
    spellings. Deliberately left as-is, consistent with this file's
    existing stance (program_profile()'s own docstring) that cross-year
    identity resolution is a different, harder problem than this picker
    needs to solve: it only has to let a program be found and selected by
    name, not present one canonical timeline per agency.
    """
    alias_lookup = {
        (a["edition"], a["short_name"]): a["formal_name"]
        for a in AgencyAlias.objects.values("edition", "short_name", "formal_name")
    }

    rows = ProgramExpense.objects.values(
        "portfolio",
        "agency",
        "edition",
        "outcome_number",
        "outcome_description",
        "program_number",
        "program_name",
    )

    resolved = [
        {
            "portfolio": _canon_portfolio(r["portfolio"]),
            "agency": alias_lookup.get((r["edition"], r["agency"]), r["agency"]),
            "edition": r["edition"],
            "outcome_number": r["outcome_number"],
            "outcome_description": r["outcome_description"],
            "program_number": r["program_number"],
            "program_name": r["program_name"],
        }
        for r in rows
    ]

    # An outcome's own wording can change year to year without its
    # number/identity actually changing (confirmed: Education's own
    # Outcome 1 statement said "access to quality child care" in the
    # 2022-23 October Budget, "access to quality early childhood
    # education and care" every year since) -- picking one description
    # per (portfolio, agency, outcome_number), preferring the latest
    # edition's own wording, keeps a program from appearing twice under
    # what's really the same outcome just because its blurb was reworded.
    outcome_description = {}
    for r in resolved:
        key = (r["portfolio"], r["agency"], r["outcome_number"])
        if key not in outcome_description or r["edition"] == LATEST_BUDGET_EDITION:
            outcome_description[key] = r["outcome_description"]

    seen = {}
    for r in resolved:
        key = (r["portfolio"], r["agency"], r["outcome_number"], r["program_number"], r["program_name"])
        if key not in seen:
            seen[key] = {
                "portfolio": r["portfolio"],
                "agency": r["agency"],
                "outcome_number": r["outcome_number"],
                "outcome_description": outcome_description[
                    (r["portfolio"], r["agency"], r["outcome_number"])
                ],
                "program_number": r["program_number"],
                "program_name": r["program_name"],
            }

    return sorted(
        seen.values(),
        key=lambda r: (r["portfolio"], r["agency"], r["outcome_number"], r["program_number"]),
    )


@api_view(["GET"])
def program_outcome_audit(request):
    """-> every (outcome_number, program_name) combination across EVERY
    ingested edition's program_expenses, with every raw portfolio/agency
    spelling that ever reported under it folded together and one
    estimated_actual figure per year -- a manual data-quality review tool
    (KNOWN_GAPS.md-adjacent, not used by any other page).

    Grouped by (outcome_number, program_name) rather than the
    (program_name, portfolio) identity every other endpoint in this file
    uses deliberately: those endpoints need to keep a genuine machinery-
    of-government transfer as two separate series (see program_profile's
    own docstring). This page exists for the opposite reason -- a portfolio
    rename or spelling drift (see _PORTFOLIO_ALIASES, and the "Foreign
    Affairs and Trade" investigation that motivated this endpoint) makes
    an otherwise-continuous program look sparse/broken when split by raw
    portfolio spelling, and a human reviewing data quality needs the
    single wide picture to actually spot what's missing. Outcome number
    (not portfolio) is the extra key alongside program_name because a
    program name could in principle recur under a genuinely different
    outcome; portfolio/agency are surfaced as columns to review, not used
    to split rows.

    Each row's `rows` list is every underlying program_expenses record
    (every estimate_type, not just estimated_actual) so the frontend can
    show a drill-down -- the whole point is letting a human check *why* a
    year is blank: no data at all, or just no estimated_actual yet.
    """
    alias_lookup = {
        (a["edition"], a["short_name"]): a["formal_name"]
        for a in AgencyAlias.objects.values("edition", "short_name", "formal_name")
    }

    def gap_years(years):
        """Fiscal years missing *between* the earliest and latest year this
        program has an estimated_actual for -- e.g. actuals for 2016-17,
        2017-18, 2019-20 has a one-year gap at 2018-19. Years before the
        earliest or after the latest aren't a "gap": a program starting or
        ending partway through the ingested window is just incomplete
        coverage at the edge, not a break in the middle of its own run.
        Powers this page's "only show programs with a gap" filter (see the
        2026-08 investigation that found ~50 of these were actually
        unaliased portfolio spellings, not real gaps, before this existed
        as a standing filter rather than a one-off script)."""
        if len(years) < 2:
            return []
        start_years = sorted(int(fy.split("-")[0]) for fy in years)
        missing = []
        for a, b in zip(start_years, start_years[1:]):
            for y in range(a + 1, b):
                missing.append(f"{y}-{(y + 1) % 100:02d}")
        return missing

    # Ordered by edition -- required by _stitch_series below (see its own
    # docstring): the one real case of two editions both claiming the
    # same fiscal_year's estimated_actual (2022-23 March Budget and
    # 2022-23 October Budget both restate 2021-22) is resolved there by
    # excluding October's figure rather than summing the two into a
    # double-counted total.
    raw_rows = list(
        ProgramExpense.objects.values(
            "edition", "portfolio", "agency", "outcome_number", "outcome_description",
            "program_number", "program_name", "fiscal_year", "estimate_type",
            "amount_thousands",
        ).order_by("edition")
    )

    groups = {}
    for r in raw_rows:
        key = (r["outcome_number"], r["program_name"])
        g = groups.setdefault(key, {
            "outcome_descriptions": Counter(),
            "portfolios": set(),
            "agencies": set(),
            "program_numbers": set(),
            "rows": [],
        })
        if r["outcome_description"]:
            g["outcome_descriptions"][r["outcome_description"]] += 1
        g["portfolios"].add(_canon_portfolio(r["portfolio"]))
        g["agencies"].add(alias_lookup.get((r["edition"], r["agency"]), r["agency"]))
        g["program_numbers"].add(r["program_number"])
        g["rows"].append({
            "edition": r["edition"],
            "portfolio": r["portfolio"],
            "agency": r["agency"],
            "program_number": r["program_number"],
            "fiscal_year": r["fiscal_year"],
            "estimate_type": r["estimate_type"],
            "amount_thousands": r["amount_thousands"],
        })

    results = []
    for (outcome_number, program_name), g in groups.items():
        # _stitch_series also fills years with no estimated_actual from
        # latest_edition's own budget/forward_estimate -- drop those here,
        # keeping only genuine estimated_actual figures (its own
        # "estimate_type" tags which is which).
        stitched = _stitch_series(g["rows"])
        years = {
            s["fiscal_year"]: s["amount_thousands"]
            for s in stitched if s["estimate_type"] == "estimated_actual"
        }
        results.append({
            "outcome_number": outcome_number,
            "outcome_description": (
                g["outcome_descriptions"].most_common(1)[0][0]
                if g["outcome_descriptions"] else None
            ),
            "program_name": program_name,
            "program_numbers": sorted(n for n in g["program_numbers"] if n),
            "portfolios": sorted(p for p in g["portfolios"] if p),
            "agencies": sorted(a for a in g["agencies"] if a),
            "years": years,
            "gap_years": gap_years(years),
            "rows": sorted(g["rows"], key=lambda r: (r["edition"], r["fiscal_year"])),
        })

    results.sort(key=lambda r: (
        r["outcome_number"] if r["outcome_number"] is not None else -1,
        r["program_name"] or "",
    ))
    return Response(results)


@api_view(["GET"])
def portfolio_profile(request):
    """?portfolio=<name>&budget_year=<year> -> for every agency in that
    portfolio as of budget_year (a single-year snapshot -- which agencies
    belong to a portfolio can itself change year to year), that agency's
    own multi-year financial profile (summed across all its programs,
    across every spelling agency_aliases links to the same real agency
    within this portfolio -- see _agency_history -- so a same-agency
    rename over time, e.g. "SAUS" one year and "SA" the next, doesn't
    truncate the series). A portfolio reshuffle (the same agency moving to
    a *different* portfolio in some year) is still not bridged -- that
    year's data won't show under either portfolio's chart -- left for the
    user to recognise, not resolved automatically.
    """
    portfolio = request.query_params.get("portfolio")
    budget_year = request.query_params.get("budget_year")
    if not portfolio or not budget_year:
        return Response(
            {"detail": "portfolio and budget_year query params are required"},
            status=400,
        )

    agencies = list(
        ProgramExpense.objects.filter(portfolio=portfolio, budget_year=budget_year)
        .values_list("agency", flat=True)
        .distinct()
    )
    if not agencies:
        return Response({"detail": "not found"}, status=404)

    result = []
    for agency in sorted(agencies):
        all_names = _agency_history(agency, portfolio)
        rows = list(
            ProgramExpense.objects.filter(
                portfolio=portfolio, agency__in=all_names
            ).values("fiscal_year", "estimate_type", "edition", "amount_thousands")
            .order_by("edition")
        )
        result.append({"agency": agency, "series": _stitch_series(rows)})

    return Response({"portfolio": portfolio, "budget_year": budget_year, "agencies": result})


@api_view(["GET"])
def agency_outcome_profile(request):
    """?agency=<name>&portfolio=<name>&budget_year=<year> -> for every
    outcome under that agency as of budget_year, its own multi-year
    financial profile (summed across all its programs, across every
    spelling agency_aliases links to the same real agency within this
    portfolio -- see _agency_history -- so a same-agency rename over time
    doesn't truncate the series). portfolio is required for the same
    reason as portfolio_profile: a bare agency abbreviation isn't safe to
    trust across years without it.

    `agency` is resolved via agency_aliases first (same as measure_detail)
    since the caller is typically a measure's own agency badge -- the
    formal name typed into a Table 1.2 sheet ("Services Australia") --
    while program_expenses.agency for that budget_year is often the
    filename-derived short form ("SAUS"). outcome_number identity is
    trusted within that agency's bridged history the same way
    program_profile trusts program_name -- not independently re-verified
    per year, just inherited from the agency-level bridge above.
    """
    agency = request.query_params.get("agency")
    portfolio = request.query_params.get("portfolio")
    budget_year = request.query_params.get("budget_year")
    if not agency or not portfolio or not budget_year:
        return Response(
            {"detail": "agency, portfolio and budget_year query params are required"},
            status=400,
        )
    agency = _resolve_agencies({agency}, budget_year)[agency]
    all_names = _agency_history(agency, portfolio)

    # agency__in=all_names, not agency=agency: a single budget_year's own
    # alias row won't always resolve `agency` all the way to that same
    # year's program_expenses spelling (e.g. a year whose own Table 1.1
    # title uses the abbreviation, so _resolve_agencies has nothing to
    # match the long form against) -- the alias chain already computed
    # above is the reliable way to find that year's actual spelling.
    outcomes = list(
        ProgramExpense.objects.filter(
            agency__in=all_names, portfolio=portfolio, budget_year=budget_year
        )
        .values("outcome_number", "outcome_description")
        .distinct()
    )
    if not outcomes:
        return Response({"detail": "not found"}, status=404)

    result = []
    for o in sorted(outcomes, key=lambda o: o["outcome_number"]):
        rows = list(
            ProgramExpense.objects.filter(
                agency__in=all_names,
                portfolio=portfolio,
                outcome_number=o["outcome_number"],
            ).values("fiscal_year", "estimate_type", "edition", "amount_thousands")
            .order_by("edition")
        )
        result.append(
            {
                "outcome_number": o["outcome_number"],
                "outcome_description": o["outcome_description"],
                "series": _stitch_series(rows),
            }
        )

    return Response(
        {
            "agency": agency,
            "portfolio": portfolio,
            "budget_year": budget_year,
            "outcomes": result,
        }
    )


def _canon_for_lookup(s):
    """Same dash/quote canonicalization parse_bp2.py's/parse_measures.py's
    own _canon_measure_name() applies before a measure_name is stored --
    applied here to a related-measure phrase (never itself run through
    that function at ingest time, see measure_text_related's own
    docstring) before comparing it against that already-canonicalized
    set, so both sides of the match use the same normalization."""
    return (
        s.replace("–", "-").replace("—", "-").replace("‑", "-")
        .replace("─", "-").replace("‐", "-")
        .replace("’", "'").replace("‘", "'")
        .strip()
    )


def _resolve_related_measures(phrases):
    """A BP2 write-up italicizes two different things identically: a
    referenced *program* name, and a referenced *prior/related measure*
    name (see parse_bp2.py's own module docstring) -- captured the same
    way at ingest time since telling them apart from typography alone
    isn't reliable, deferred to "resolve them to other measures at run
    time" instead. This is that resolution: only a phrase that actually
    matches a real measure_name (now checked against all 21 BP2 editions
    plus the 3 PBS-covered ones, case-insensitively) is returned --
    e.g. "Connected Beginnings"/"Indigenous Advancement Strategy" in the
    2021-22 MYEFO Closing the Gap Package write-up are program names,
    don't match anything, and are correctly dropped rather than shown as
    if they were clickable related measures a lookup would never find.

    Where a name matches in more than one edition, picks the
    alphabetically-latest edition string -- which, given this dataset's
    "<YYYY-YY> Budget"/"<YYYY-YY> MYEFO"/"<YYYY-YY> March/October
    Budget" naming, also happens to sort chronologically latest.

    Returns the resolved measure's own canonical measure_name alongside
    the id, not just the original phrase -- measure_detail/measure_text
    are looked up by an *exact* (measure_name, edition) match, and a
    phrase can differ from the stored name in case ("Statistical
    Business Transformation program" vs. the stored "...Program"), so
    the phrase alone isn't safe to navigate to.
    """
    lookup = defaultdict(list)
    for name, edition, measure_id in MeasureText.objects.values_list(
        "measure_name", "edition", "measure_id"
    ):
        lookup[name.lower()].append((edition, measure_id, name))
    for name, edition, measure_id in MeasureImpact.objects.values_list(
        "measure_name", "edition", "measure_id"
    ):
        lookup[name.lower()].append((edition, measure_id, name))

    resolved = []
    for phrase in phrases:
        matches = lookup.get(_canon_for_lookup(phrase).lower())
        if not matches:
            continue
        edition, measure_id, measure_name = sorted(matches)[-1]
        resolved.append(
            {
                "phrase": phrase,
                "measure_id": measure_id,
                "measure_name": measure_name,
                "edition": edition,
            }
        )
    return resolved


@api_view(["GET"])
def measure_text(request):
    """?name=<measure_name>&edition=<edition> -> the Budget Paper No. 2
    narrative write-up for one measure, if one was ingested for that
    edition: intro/end prose, the bulleted components (flat list; a
    subcomponent's parent_ordinal points back to its level-1 parent, per
    the "Government will also:" case -- a measure can have more than one
    intro+bullet-list block in the source, flattened into one ordered
    list here rather than kept as separate groups), related measures
    that actually resolve to a real measure_name (see
    _resolve_related_measures -- a phrase that doesn't resolve is a
    program/legislation/document reference or a measure from an
    uningested edition, not shown here), and BP2's own headline
    financial table (kept separate from measure_impacts -- BP2 is a
    different source document, occasionally with a wider set of
    agencies than any individual agency's own PBS Table 1.2 reports,
    e.g. a receipt-side contribution that never appears in that
    agency's own filing).

    Looked up by (measure_name, edition) alone, matching how
    build_bp2_db.py keys the table -- portfolio isn't part of the
    lookup, just returned for display.
    """
    measure_name = request.query_params.get("name")
    edition = request.query_params.get("edition")
    if not measure_name or not edition:
        return Response(
            {"detail": "name and edition query params are required"}, status=400
        )

    text = MeasureText.objects.filter(measure_name=measure_name, edition=edition).first()
    if text is None:
        return Response({"detail": "not found"}, status=404)

    components = list(
        MeasureTextComponent.objects.filter(measure_text_id=text.id)
        .order_by("ordinal")
        .values("ordinal", "level", "marker", "parent_ordinal", "text")
    )
    related_phrases = list(
        MeasureTextRelated.objects.filter(measure_text_id=text.id)
        .order_by("ordinal")
        .values_list("phrase", flat=True)
    )
    related = _resolve_related_measures(related_phrases)
    financial_rows = list(
        MeasureTextHeadlineFinancial.objects.filter(measure_text_id=text.id).order_by(
            "impact_type", "is_related", "id", "year_index"
        )
    )

    grouped_financials = {}
    order = []
    for r in financial_rows:
        key = (r.impact_type, r.is_related, r.department_name)
        if key not in grouped_financials:
            grouped_financials[key] = []
            order.append(key)
        grouped_financials[key].append(
            {
                "value_kind": r.value_kind,
                "value_numeric_million": r.value_numeric_million,
                "value_raw": r.value_raw,
            }
        )
    headline_financials = [
        {
            "impact_type": k[0],
            "is_related": k[1],
            "department_name": k[2],
            "values": grouped_financials[k],
        }
        for k in order
    ]

    return Response(
        {
            "measure_id": text.measure_id,
            "measure_name": text.measure_name,
            "edition": text.edition,
            "portfolio": text.portfolio,
            "document_section": text.document_section,
            "source_page": text.source_page,
            "full_measure_text": text.full_measure_text,
            "components": list(components),
            "related_measures": related,
            "headline_financials": headline_financials,
        }
    )


@api_view(["GET"])
def measure_list(request):
    """-> every (measure_name, edition) pair across BOTH sources -- BP2
    write-ups (measure_text, 21 editions) and PBS Table 1.2 data
    (measure_impacts/measure_programs, 19 editions -- see KNOWN_GAPS.md
    #6/#9) -- each with whatever portfolios/agencies are known for it
    and a has_financial_data flag.

    has_financial_data is true only for the PBS side: that's the only
    data measure_detail can render a $ breakdown/charts from. A measure
    from one of the BP2-only editions still gets a real measure_id and
    a working page (see measure_text() above and the frontend's
    graceful-degradation handling) -- just without that section, since
    there's no PBS Table 1.2 data behind it to show.

    portfolios is run through _canon_portfolio() -- unlike measure_
    detail's own portfolios field below, which deliberately is NOT
    (see that function's own note): this one only ever feeds a filter
    dropdown/badge display, never an exact-match join against another
    table's still-raw portfolio strings, so it's safe to clean up here.

    Returned in full and filtered client-side (by name substring,
    portfolio) rather than server-side: a few thousand rows of JSON is
    still small enough that a server round-trip on every keystroke
    would cost more than it buys, the same reasoning as when this was
    limited to the ~500-row 3-edition-only list.
    """
    rows = defaultdict(
        lambda: {"portfolios": set(), "agencies": set(), "measure_id": None, "has_financial_data": False}
    )

    for r in MeasureText.objects.values("measure_id", "measure_name", "edition", "portfolio"):
        key = (r["measure_name"], r["edition"])
        rows[key]["measure_id"] = r["measure_id"]
        if r["portfolio"]:
            rows[key]["portfolios"].add(_canon_portfolio(r["portfolio"]))

    for r in MeasureImpact.objects.values("measure_id", "measure_name", "edition", "portfolio", "agency"):
        key = (r["measure_name"], r["edition"])
        rows[key]["measure_id"] = r["measure_id"]
        rows[key]["portfolios"].add(_canon_portfolio(r["portfolio"]))
        rows[key]["agencies"].add(r["agency"])
        rows[key]["has_financial_data"] = True
    for r in MeasureProgram.objects.values("measure_id", "measure_name", "edition", "portfolio", "agency"):
        key = (r["measure_name"], r["edition"])
        rows[key]["measure_id"] = r["measure_id"]
        rows[key]["portfolios"].add(_canon_portfolio(r["portfolio"]))
        rows[key]["agencies"].add(r["agency"])
        rows[key]["has_financial_data"] = True

    results = sorted(
        (
            {
                "measure_id": v["measure_id"],
                "measure_name": name,
                "edition": edition,
                "portfolios": sorted(v["portfolios"]),
                "agencies": sorted(v["agencies"]),
                "has_financial_data": v["has_financial_data"],
            }
            for (name, edition), v in rows.items()
        ),
        key=lambda r: (r["measure_name"], r["edition"]),
    )
    return Response(results)


@api_view(["GET"])
def measure_by_id(request):
    """?id=<8-digit measure_id> -> {measure_name, edition} for that id, so
    a short shareable URL (?m=<id>) can be resolved back to the lookup
    key measure_detail/measure_text actually use. measure_id is
    precomputed and stored at build time (measure_id.py's
    MeasureIdAssigner, used by both build_measures_db.py and
    build_bp2_db.py), so this is a plain indexed lookup, not a hash
    recomputation. Checked in order -- measure_impacts, measure_programs,
    then measure_text -- so a BP2-only measure (no PBS data) still
    resolves, just via the third lookup."""
    measure_id = request.query_params.get("id")
    if not measure_id:
        return Response({"detail": "id query param is required"}, status=400)

    row = (
        MeasureImpact.objects.filter(measure_id=measure_id)
        .values("measure_name", "edition")
        .first()
        or MeasureProgram.objects.filter(measure_id=measure_id)
        .values("measure_name", "edition")
        .first()
        or MeasureText.objects.filter(measure_id=measure_id)
        .values("measure_name", "edition")
        .first()
    )
    if row is None:
        return Response({"detail": "not found"}, status=404)
    return Response(row)


def _resolve_measure_ids(measure_ids):
    """Bulk id -> (measure_name, edition) resolution for measure_combined()
    below -- one query per source table (impacts, programs, text)
    covering every requested id at once, rather than one query per id,
    since this list can be 10+ long. Same precedence as measure_by_id:
    measure_impacts, then measure_programs, then measure_text, so a
    BP2-only measure still resolves via the third source."""
    remaining = set(measure_ids)
    resolved = {}
    for model in (MeasureImpact, MeasureProgram, MeasureText):
        if not remaining:
            break
        for row in model.objects.filter(measure_id__in=remaining).values(
            "measure_id", "measure_name", "edition"
        ):
            resolved.setdefault(row["measure_id"], (row["measure_name"], row["edition"]))
        remaining -= resolved.keys()
    return resolved


@api_view(["GET"])
def measure_combined(request):
    """?ids=<id1,id2,...> -> per-measure detail (the same shape
    _build_measure_detail returns for one measure via measure_detail/)
    for several measures in one request -- built for the multi-measure
    comparison page, which needs every selected measure's data at once
    (e.g. to compute which agency received the largest combined $
    before it can even pick a default chart), so N parallel calls to
    detail/ would mean N round trips just to get to that point. Ids that
    don't resolve to any measure at all (a stale/typo'd id) are reported
    in not_found rather than failing the whole batch.

    portfolios here ARE run through _canon_portfolio() -- unlike
    measure_detail's own (deliberately raw) field. That field stays raw
    because it feeds portfolio_profile()'s exact-match join; this page
    has no drilldown, so the cleaned-up display form is safe here (same
    reasoning measure_list already uses its own _canon_portfolio() call
    for).

    Deliberately excludes each measure's own BP2 write-up
    (full_measure_text/components/related_measures/headline_financials)
    -- a separate, larger payload the frontend fetches lazily per
    measure via the existing text/ endpoint, only once that measure's
    own row is expanded on the page. Keeping it out of this response
    means a 10+ measure selection doesn't block the whole page behind
    10+ write-ups fetched up front.
    """
    ids_param = request.query_params.get("ids", "")
    ids = [i.strip() for i in ids_param.split(",") if i.strip()]
    if not ids:
        return Response({"detail": "ids query param is required"}, status=400)

    resolved = _resolve_measure_ids(ids)

    measures = []
    not_found = []
    for measure_id in ids:
        hit = resolved.get(measure_id)
        if hit is None:
            not_found.append(measure_id)
            continue
        measure_name, edition = hit
        detail = _build_measure_detail(measure_name, edition)
        if detail is None:
            # BP2-only measure -- no measure_impacts/measure_programs row,
            # so no $ data, but it still belongs in the summary set's
            # own measures list (its write-up is still readable there).
            text = MeasureText.objects.filter(
                measure_name=measure_name, edition=edition
            ).first()
            measures.append(
                {
                    "measure_id": measure_id,
                    "measure_name": measure_name,
                    "edition": edition,
                    "portfolios": [_canon_portfolio(text.portfolio)]
                    if text and text.portfolio
                    else [],
                    "agencies": [],
                    "has_financial_data": False,
                    "impacts": [],
                    "programs": [],
                }
            )
        else:
            measures.append(
                {
                    **detail,
                    "portfolios": [_canon_portfolio(p) for p in detail["portfolios"]],
                    "has_financial_data": True,
                }
            )

    return Response({"measures": measures, "not_found": not_found})


_program_reverse_index = None


def _build_program_reverse_index():
    """Maps every real program -- keyed by (program_name, portfolio), NOT
    program_name alone -- to every measure that touches it across every
    ingested edition. The reverse of what _build_measure_detail resolves
    per-measure above, built once for the whole measure_programs table
    (module-level singleton, same pattern as _get_topic_collection:
    recomputing this per request would mean redoing the same agency-
    history bridging + program_expenses lookups for ~12k rows on every
    call to measures_by_program below).

    portfolio is part of the key deliberately: program_hierarchy() above
    already keeps genuinely different portfolio-era names distinct (e.g.
    "Education and Training", 2017-19, vs "Education", 2023-26 -- a real
    machinery-of-government rename, not a spelling variant), on purpose
    -- program_name alone can legitimately be reused for what a user
    wants to treat as a *different* selection (confirmed in practice: a
    since-restructured department's own "Child Care Subsidy" filing and
    the current department's own "Child Care Subsidy" filing share a
    name but sit under different portfolios, and collapsing them into
    one tray entry blocked adding both -- see measures_by_program's own
    docstring for how the picker and this index now agree on that same
    (program_name, portfolio) identity throughout).

    Mirrors _build_measure_detail's own per-row resolution exactly
    (agency bridged via _agency_history within that row's own portfolio,
    matched against program_expenses for that row's own budget_year) --
    just batched across every budget_year present in measure_programs
    instead of scoped to one measure's own rows, since two different
    measures touching the same program can come from completely
    different editions.
    """
    global _program_reverse_index
    if _program_reverse_index is not None:
        return _program_reverse_index

    rows_by_budget_year = defaultdict(list)
    for p in MeasureProgram.objects.values(
        "measure_name", "edition", "portfolio", "agency", "program_number"
    ):
        rows_by_budget_year[_budget_year(p["edition"])].append(p)

    index = defaultdict(set)  # (program_name, portfolio) -> {(measure_name, edition)}
    for budget_year, rows in rows_by_budget_year.items():
        agency_name_sets = {}
        for p in rows:
            key = (p["agency"], p["portfolio"])
            if key not in agency_name_sets:
                agency_name_sets[key] = _agency_history(p["agency"], p["portfolio"])
        all_candidate_names = set().union(*agency_name_sets.values()) if agency_name_sets else set()

        program_lookup = {
            (r["agency"], r["program_number"]): r["program_name"]
            for r in ProgramExpense.objects.filter(
                budget_year=budget_year, agency__in=all_candidate_names
            ).values("agency", "program_number", "program_name")
        }

        for p in rows:
            resolved_name = None
            for candidate in agency_name_sets[(p["agency"], p["portfolio"])]:
                resolved_name = program_lookup.get((candidate, p["program_number"]))
                if resolved_name:
                    break
            if resolved_name:
                index[(resolved_name, _canon_portfolio(p["portfolio"]))].add(
                    (p["measure_name"], p["edition"])
                )

    _program_reverse_index = index
    return _program_reverse_index


@api_view(["GET"])
def measures_by_program(request):
    """?program_name=<name>&portfolio=<portfolio> -> every measure (across
    every edition) whose own measure_programs rows resolve to that
    (program_name, portfolio) pair, in the same per-measure shape
    measure_combined() returns -- CombinedMeasuresPage's "start from a
    program" entry point (the Portfolio/Agency/Outcome/Program picker,
    see program_hierarchy() above) feeds both straight into this instead
    of a list of measure ids.

    portfolio is required, not optional: see _build_program_reverse_
    index's own docstring for why the same program_name under two
    different portfolio eras needs to resolve to two different measure
    sets, not get silently merged.
    """
    program_name = request.query_params.get("program_name")
    portfolio = request.query_params.get("portfolio")
    if not program_name or not portfolio:
        return Response(
            {"detail": "program_name and portfolio query params are required"}, status=400
        )

    keys = _build_program_reverse_index().get((program_name, portfolio), set())

    measures = []
    for measure_name, edition in sorted(keys):
        detail = _build_measure_detail(measure_name, edition)
        if detail is None:
            continue  # shouldn't happen -- these keys came from measure_programs itself
        measures.append(
            {
                **detail,
                "portfolios": [_canon_portfolio(p) for p in detail["portfolios"]],
                # Always true -- unlike measure_combined's own ids-based
                # lookup, every key here came from measure_programs
                # itself (see _build_program_reverse_index), so there's
                # no BP2-only/no-financial-data case to represent.
                "has_financial_data": True,
            }
        )

    return Response({"program_name": program_name, "portfolio": portfolio, "measures": measures})


def _snippet(text, query, context=60):
    """A short window of `text` around the first case-insensitive match
    of `query`, with an ellipsis on whichever side got cut -- so a text-
    content search result shows *why* it matched, not just that it did.
    None if `text` doesn't actually contain `query` (the caller tries
    another text source, e.g. a component, in that case)."""
    if not text:
        return None
    idx = text.lower().find(query.lower())
    if idx == -1:
        return None
    start = max(0, idx - context)
    end = min(len(text), idx + len(query) + context)
    snippet = text[start:end].strip()
    if start > 0:
        snippet = "…" + snippet
    if end < len(text):
        snippet = snippet + "…"
    return snippet


CHROMA_PATH = Path(__file__).resolve().parent.parent.parent / "chroma_measures"
CHROMA_COLLECTION_NAME = "measure_text"

_topic_collection = None
_query_embedder = None


def _get_topic_collection():
    """Lazily loads the chromadb collection built by
    build_measure_embeddings.py (module-level singleton -- the embedding
    model it needs is expensive to load, so this pays that cost once per
    server process, not once per request). Imported lazily rather than at
    module load time so a plain `python manage.py check`/migration run
    doesn't need chromadb installed just to import this file.

    Returns None if the collection hasn't been built yet (rather than
    raising), so measure_topic_search can degrade to an empty result
    with a clear message instead of a 500.
    """
    global _topic_collection
    if _topic_collection is not None:
        return _topic_collection
    import chromadb
    from chromadb.utils import embedding_functions

    if not CHROMA_PATH.exists():
        return None
    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    try:
        collection = client.get_collection(
            name=CHROMA_COLLECTION_NAME, embedding_function=embedding_functions.DefaultEmbeddingFunction()
        )
    except Exception:
        return None
    _topic_collection = collection
    return _topic_collection


def _get_query_embedder():
    """A second, separately-cached singleton for embedding the QUERY
    text at search time -- confirmed in production this is not
    redundant with _get_topic_collection's own caching above.
    chromadb's DefaultEmbeddingFunction.__call__ does
    `return ONNXMiniLM_L6_V2()(input)`: a brand-new ONNXMiniLM_L6_V2
    instance, discarded right after, on every single call. That class's
    own onnxruntime InferenceSession and tokenizer are @cached_property
    -- built once and reused -- but that only pays off across calls on
    the SAME instance, which chromadb's own wrapper never gives it.
    Net effect: every topic search rebuilt the whole ONNX inference
    session from scratch, on Render's own limited free-tier CPU slow
    enough to occasionally exceed gunicorn's worker timeout and get
    killed mid-request (a SystemExit from gunicorn's own handle_abort,
    seen in production logs, raised from inside this exact __call__).

    Building our own instance once here and reusing it for every query
    (via query_embeddings= below, which skips the collection's own
    embedding function entirely -- it's only invoked when you pass raw
    query_texts instead) fixes that: the expensive session/tokenizer
    build happens once per server process, same as the collection load
    above, not once per request.
    """
    global _query_embedder
    if _query_embedder is not None:
        return _query_embedder
    from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2

    _query_embedder = ONNXMiniLM_L6_V2()
    return _query_embedder


@api_view(["GET"])
def measure_topic_search(request):
    """?q=<topic phrase> -> measures whose BP2 write-up is semantically
    close to the query, via the chromadb collection build_measure_
    embeddings.py builds (all-MiniLM-L6-v2 sentence embeddings over each
    measure's full write-up + components). Complements measure_text_
    search's exact-substring search: a query like "child care" surfaces a
    measure whose text only ever says "Early Childhood Education and Care
    (ECEC)" and never the literal query words, which a substring search
    can't do (confirmed concretely: "Building Australia's Future -
    delivering pay rises for early educators", 2024-25 MYEFO, mentions
    ECEC but not "child care" anywhere in its own text).

    Same response shape as measure_text_search (portfolios/agencies/
    has_financial_data resolved the same way, by joining back to
    MeasureImpact/MeasureProgram) so the frontend can render both result
    lists with the same component -- plus a `score` (cosine similarity,
    1.0 = identical) the substring search has no equivalent of, since
    there's no exact match position to build an excerpt-style snippet
    from; `snippet` here is just the write-up's own opening text.
    """
    q = request.query_params.get("q", "").strip()
    if not q:
        return Response([])

    collection = _get_topic_collection()
    if collection is None:
        return Response(
            {"detail": "Topic search index not built yet -- run build_measure_embeddings.py."},
            status=503,
        )

    n_results = min(int(request.query_params.get("n", 30)), 100)
    # query_embeddings=, not query_texts= -- computing the embedding
    # ourselves via the cached singleton above means the collection's
    # own (expensive-per-call, see _get_query_embedder's own docstring)
    # embedding function is never invoked at all.
    query_embedding = _get_query_embedder()([q])[0]
    result = collection.query(query_embeddings=[query_embedding], n_results=n_results)
    ids = result["ids"][0]
    metadatas = result["metadatas"][0]
    distances = result["distances"][0]
    if not ids:
        return Response([])

    full_text_by_id = dict(
        MeasureText.objects.filter(measure_id__in=ids).values_list("measure_id", "full_measure_text")
    )

    keys = [(m["measure_name"], m["edition"]) for m in metadatas]
    key_set = set(keys)
    rows = {
        key: {
            "measure_id": measure_id,
            "portfolios": {_canon_portfolio(m["portfolio"])} if m["portfolio"] else set(),
            "agencies": set(),
            "has_financial_data": False,
            # chromadb's distance for its default embedding function is
            # squared L2 on normalized vectors, equivalent to 2*(1-cosine)
            # -- converted here so callers see a familiar 0..1 similarity
            # instead of an unbounded distance.
            "score": max(0.0, 1 - distance / 2),
        }
        for measure_id, m, key, distance in zip(ids, metadatas, keys, distances)
    }

    for r in MeasureImpact.objects.filter(
        measure_name__in=[k[0] for k in keys], edition__in=[k[1] for k in keys]
    ).values("measure_id", "measure_name", "edition", "portfolio", "agency"):
        key = (r["measure_name"], r["edition"])
        if key not in key_set:
            continue
        rows[key]["portfolios"].add(_canon_portfolio(r["portfolio"]))
        rows[key]["agencies"].add(r["agency"])
        rows[key]["has_financial_data"] = True
    for r in MeasureProgram.objects.filter(
        measure_name__in=[k[0] for k in keys], edition__in=[k[1] for k in keys]
    ).values("measure_id", "measure_name", "edition", "portfolio", "agency"):
        key = (r["measure_name"], r["edition"])
        if key not in key_set:
            continue
        rows[key]["portfolios"].add(_canon_portfolio(r["portfolio"]))
        rows[key]["agencies"].add(r["agency"])
        rows[key]["has_financial_data"] = True

    results = [
        {
            "measure_id": rows[key]["measure_id"],
            "measure_name": key[0],
            "edition": key[1],
            "portfolios": sorted(rows[key]["portfolios"]),
            "agencies": sorted(rows[key]["agencies"]),
            "has_financial_data": rows[key]["has_financial_data"],
            "score": rows[key]["score"],
            "snippet": (full_text_by_id.get(rows[key]["measure_id"], "") or "")[:180].strip(),
        }
        for key in dict.fromkeys(keys)  # de-dupe, preserve chroma's own rank order
    ]
    results.sort(key=lambda r: -r["score"])
    return Response(results)


@api_view(["GET"])
def measure_text_search(request):
    """?q=<substring> -> measures whose BP2 write-up (intro/end prose or
    a bulleted component) contains the query, each with a short snippet
    showing the match in context. Covers all 21 ingested BP2 editions --
    every match has its own measure_id (measure_text.measure_id, see
    measure_id.py), so unlike before this no longer needs to discard
    matches from the 18 editions with no measure_impacts/measure_programs
    row; those just come back with has_financial_data: false.

    Unlike measure_list (fetched once, filtered client-side -- see its
    own docstring for why that's fine even at a few thousand rows), this
    is a server-side search triggered per query: the underlying text
    corpus (every paragraph and bullet of every ingested BP2 write-up)
    is much bigger than the name list, so shipping the whole thing to
    the browser to filter isn't worth it.
    """
    q = request.query_params.get("q", "").strip()
    if not q:
        return Response([])

    matched = list(
        MeasureText.objects.filter(
            Q(full_measure_text__icontains=q) | Q(measuretextcomponent__text__icontains=q)
        )
        .distinct()
        .values("id", "measure_id", "measure_name", "edition", "portfolio", "full_measure_text")
    )
    if not matched:
        return Response([])

    snippet_by_key = {}
    rows = {}
    for m in matched:
        key = (m["measure_name"], m["edition"])
        snippet = _snippet(m["full_measure_text"], q)
        if snippet is None:
            comp_text = (
                MeasureTextComponent.objects.filter(measure_text_id=m["id"], text__icontains=q)
                .order_by("ordinal")
                .values_list("text", flat=True)
                .first()
            )
            snippet = _snippet(comp_text, q) if comp_text else None
        snippet_by_key[key] = snippet
        rows[key] = {
            "measure_id": m["measure_id"],
            "portfolios": {_canon_portfolio(m["portfolio"])} if m["portfolio"] else set(),
            "agencies": set(),
            "has_financial_data": False,
        }

    matched_keys = set(rows)

    for r in MeasureImpact.objects.values("measure_id", "measure_name", "edition", "portfolio", "agency"):
        key = (r["measure_name"], r["edition"])
        if key not in matched_keys:
            continue
        rows[key]["measure_id"] = r["measure_id"]
        rows[key]["portfolios"].add(_canon_portfolio(r["portfolio"]))
        rows[key]["agencies"].add(r["agency"])
        rows[key]["has_financial_data"] = True
    for r in MeasureProgram.objects.values("measure_id", "measure_name", "edition", "portfolio", "agency"):
        key = (r["measure_name"], r["edition"])
        if key not in matched_keys:
            continue
        rows[key]["measure_id"] = r["measure_id"]
        rows[key]["portfolios"].add(_canon_portfolio(r["portfolio"]))
        rows[key]["agencies"].add(r["agency"])
        rows[key]["has_financial_data"] = True

    results = sorted(
        (
            {
                "measure_id": v["measure_id"],
                "measure_name": name,
                "edition": edition,
                "portfolios": sorted(v["portfolios"]),
                "agencies": sorted(v["agencies"]),
                "has_financial_data": v["has_financial_data"],
                "snippet": snippet_by_key[(name, edition)],
            }
            for (name, edition), v in rows.items()
        ),
        key=lambda r: (r["measure_name"], r["edition"]),
    )
    return Response(results)
