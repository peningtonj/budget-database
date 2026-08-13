import re
from collections import defaultdict
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

BUDGET_YEAR_RE = re.compile(r"(\d{4}-\d{2})")

# The latest ingested Budget edition -- its own "budget" and "forward_estimate"
# rows are the government's most up to date prediction for years not yet
# covered by an estimated_actual. Update this each time a newer Budget is
# ingested into program_expenses.
LATEST_BUDGET_EDITION = "2025-26 Budget"

# Some editions' PBS directory layout embeds the edition + document type
# directly in the folder name used as measure_impacts/measure_programs'
# own `portfolio` ("2020-21 PAES AGs", "2021-22 PBS PM&C1") instead of a
# clean portfolio label -- confirmed via a direct directory listing that
# other editions (e.g. 2024-25 Budget) use a bare short code ("AG",
# "DAFF") with no such prefix, so this is a genuine per-edition source
# inconsistency, not a uniform convention to rely on. Stripped here at
# the API layer rather than upstream in build_measures_db.py, the same
# choice already made for agency naming (see agency_aliases /
# _resolve_agencies below, and KNOWN_GAPS.md #4/#6) -- the raw stored
# value stays as-is (still a real, if messy, historical record of what
# the source file was actually organised as), only what the API
# surfaces gets cleaned up.
_PORTFOLIO_YEAR_PREFIX_RE = re.compile(r"^\d{4}-\d{2}\s+(?:PAES|PBS|MYEFO)\s+", re.I)


def _strip_portfolio_prefix(raw):
    s = _PORTFOLIO_YEAR_PREFIX_RE.sub("", raw.strip())
    # A stray trailing digit ("PM&C1", a one-off file-naming collision
    # artifact) -- never a real part of any portfolio name.
    s = re.sub(r"(?<=[A-Za-z])\d+$", "", s)
    return re.sub(r"\s+", " ", s).strip()


# Every raw portfolio string observed across measure_text/measure_impacts/
# measure_programs (170 distinct as of this pass -- see KNOWN_GAPS.md's
# own portfolio-normalization section for the full survey) that is a
# case/punctuation/typo/abbreviation variant of another, built by direct
# inspection of that full list -- not a fuzzy/guessed merge. Genuinely
# different portfolio names from different eras/machinery-of-government
# changes (e.g. "Agriculture" vs "Agriculture, Water and the Environment"
# vs "Agriculture, Fisheries and Forestry"; "Health" vs "Health and Aged
# Care") are deliberately kept distinct, not merged -- they really were
# called different things in different years, and collapsing them would
# misrepresent history the same way cross-year agency-identity bridging
# is deliberately NOT attempted beyond the one case documented in
# KNOWN_GAPS.md #6. A handful of ambiguous cases (bare "Communications",
# "Industry", "Foreign Affairs") were left unmapped for the same reason:
# not confident enough they're the same era as their longer-named
# siblings to merge safely. Keys are matched case-sensitively (no
# case-folding) against the already prefix-stripped string -- exhaustive
# rather than heuristic, matching how _canon_measure_name/_canon_for_
# lookup elsewhere in this codebase only ever normalize specific,
# individually-verified unicode variants, never fold case wholesale.
_PORTFOLIO_ALIASES = {
    # Attorney-General's
    "AG": "Attorney-General's",
    "AGs": "Attorney-General's",
    "ATTORNEY-GENERAL'S": "Attorney-General's",
    "ATTORNEY-GENERAL’S": "Attorney-General's",
    "Attorney General": "Attorney-General's",
    "Attorney General's": "Attorney-General's",
    "Attorney-Generals": "Attorney-General's",
    "Attorney-General’s": "Attorney-General's",
    "Attorney‑General’s": "Attorney-General's",
    # Agriculture
    "AGRICULTURE": "Agriculture",
    "AGRICULTURE AND WATER RESOURCES": "Agriculture and Water Resources",
    "Agriculture Water and the Environment": "Agriculture, Water and the Environment",
    "AWE": "Agriculture, Water and the Environment",
    "DAFF": "Agriculture, Fisheries and Forestry",
    # Climate change / energy / environment / water
    "CCEEW": "Climate Change, Energy, the Environment and Water",
    "DCCEEW": "Climate Change, Energy, the Environment and Water",
    "ENVIRONMENT": "Environment",
    "ENVIRONMENT AND ENERGY": "Environment and Energy",
    # Communications
    "COMMUNICATIONS": "Communications",
    "COMMUNICATIONS AND THE ARTS": "Communications and the Arts",
    "CROSS PORTFOLIO": "Cross Portfolio",
    # Defence / Veterans' Affairs
    "DEFENCE": "Defence",
    "DVA": "Veterans' Affairs",
    "VETERANS' AFFAIRS": "Veterans' Affairs",
    "VETERANS’ AFFAIRS": "Veterans' Affairs",
    "Veteran's Affairs": "Veterans' Affairs",
    "Veterans": "Veterans' Affairs",
    "Veterans Affairs": "Veterans' Affairs",
    "Veterans’ Affairs": "Veterans' Affairs",
    # Education / Employment
    "DESE": "Education, Skills and Employment",
    "EDUCATION": "Education",
    "EDUCATION AND TRAINING": "Education and Training",
    "Education Skills and Employment": "Education, Skills and Employment",
    "EMPLOYMENT": "Employment",
    "DEWR": "Employment and Workplace Relations",
    # Finance / Treasury
    "FINANCE": "Finance",
    "TREASURY": "Treasury",
    # Foreign Affairs and Trade
    "DFAT": "Foreign Affairs and Trade",
    "FOREIGN AFFAIRS AND TRADE": "Foreign Affairs and Trade",
    "Foreign Affairs & Trade": "Foreign Affairs and Trade",
    "Foreign Affiars and Trade": "Foreign Affairs and Trade",
    "Foriegn Affairs & Trade": "Foreign Affairs and Trade",
    # Health
    "HEALTH": "Health",
    # Home Affairs / Immigration
    "HOME AFFAIRS": "Home Affairs",
    "IMMIGRATION AND BORDER PROTECTION": "Immigration and Border Protection",
    # Human Services
    "HUMAN SERVICES": "Human Services",
    "Human Services (part of the Social Services Portfolio)": "Human Services",
    # Industry / Infrastructure
    "INDUSTRY": "Industry",
    "DISER": "Industry, Science, Energy and Resources",
    "INDUSTRY, INNOVATION AND SCIENCE": "Industry, Innovation and Science",
    "INFRASTRUCTURE AND REGIONAL DEVELOPMENT": "Infrastructure and Regional Development",
    "INFRASTRUCTURE, REGIONAL DEVELOPMENT AND CITIES": "Infrastructure, Regional Development and Cities",
    "Infrastructure adn Regional Development": "Infrastructure and Regional Development",
    # Jobs
    "JOBS AND INNOVATION": "Jobs and Innovation",
    "Jobs and Innovation Portfolio (Department of Industry, Innovation and Science)": "Jobs and Innovation",
    "JOBS AND SMALL BUSINESS": "Jobs and Small Business",
    "Jobs and Innovation Portfolio (Department of Jobs and Small Business)": "Jobs and Small Business",
    # Prime Minister and Cabinet
    "PM&C": "Prime Minister and Cabinet",
    "Prime Minister & Cabinet": "Prime Minister and Cabinet",
    "PRIME MINISTER AND CABINET": "Prime Minister and Cabinet",
    # Parliament
    "PARL": "Parliament",
    "PARLIAMENT": "Parliament",
    "Parliamentary Departments - Department of Parliamentary Services": "Parliamentary Departments",
    # Social Services
    "SOCIAL SERVICES": "Social Services",
    "Social Services (Human Services)": "Social Services",
}


def _canon_portfolio(raw):
    """Cleans up a raw portfolio string for display/filtering -- see
    _PORTFOLIO_ALIASES' own docstring for what is and isn't merged.
    Falsy input passes through unchanged (some rows have "", handled by
    the caller, same as before this function existed)."""
    if not raw:
        return raw
    stripped = _strip_portfolio_prefix(raw)
    return _PORTFOLIO_ALIASES.get(stripped, stripped)


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
    """
    summed = defaultdict(int)
    for r in rows:
        summed[(r["fiscal_year"], r["estimate_type"], r["edition"])] += r[
            "amount_thousands"
        ]

    by_fiscal_year = {}
    for (fy, etype, ed), amt in summed.items():
        if etype == "estimated_actual":
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
    """?program_name=<name> -> one program's long-run financial profile,
    stitched from every ingested Budget edition (2017-18 through the
    latest): the estimated_actual figure for every year that has one
    (the most authoritative retrospective figure available -- reported by
    the Budget edition immediately following that year), and for years
    with no estimated_actual yet, the latest Budget edition's own
    budget/forward_estimate figure. program_name is used alone, not
    scoped to an agency, because agency naming drifts heavily across
    calendar years for the exact same real program (e.g. this program's
    agency has been spelled "DET", "ESE", "Education, Skills and
    Employment DESE", and "Education" across the 9 ingested editions) --
    program_name is the stable identifier program_expenses was built
    around for exactly this reason.
    """
    program_name = request.query_params.get("program_name")
    if not program_name:
        return Response(
            {"detail": "program_name query param is required"}, status=400
        )

    rows = list(
        ProgramExpense.objects.filter(program_name=program_name).values(
            "edition",
            "agency",
            "fiscal_year",
            "estimate_type",
            "amount_thousands",
            "outcome_number",
            "outcome_description",
        )
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
            "outcome_number": meta["outcome_number"],
            "outcome_description": meta["outcome_description"],
            "series": series,
        }
    )


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
            # so no $ data, but it still belongs in the comparison set's
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
