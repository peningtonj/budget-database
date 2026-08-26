"""
Portfolio-name canonicalization, shared between backend/measures/views.py
(the API layer, importing this) and build_db.py (the standalone ingestion
script, also importing this -- see normalize_program_name_casing()).

Kept dependency-free (no Django) specifically so build_db.py can import it
directly; views.py has Django-only imports at module level, so the
reverse direction (build_db.py importing views.py) isn't an option.
"""
import re

# Some editions' PBS directory layout embeds the edition + document type
# directly in the folder name used as measure_impacts/measure_programs'
# own `portfolio` ("2020-21 PAES AGs", "2021-22 PBS PM&C1") instead of a
# clean portfolio label -- confirmed via a direct directory listing that
# other editions (e.g. 2024-25 Budget) use a bare short code ("AG",
# "DAFF") with no such prefix, so this is a genuine per-edition source
# inconsistency, not a uniform convention to rely on. Stripped here at
# the API layer rather than upstream in build_measures_db.py, the same
# choice already made for agency naming (see agency_aliases /
# _resolve_agencies in views.py, and KNOWN_GAPS.md #4/#6) -- the raw
# stored value stays as-is (still a real, if messy, historical record of
# what the source file was actually organised as), only what callers
# resolve gets cleaned up.
_PORTFOLIO_YEAR_PREFIX_RE = re.compile(r"^\d{4}-\d{2}\s+(?:PAES|PBS|MYEFO)\s+", re.I)


def strip_portfolio_prefix(raw):
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
PORTFOLIO_ALIASES = {
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
    "AFF": "Agriculture, Fisheries and Forestry",
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
    # 2019-20 Budget's own DVA/AWM filings used this folder name (DVA was
    # briefly grouped under the Defence portfolio banner for that one
    # edition) -- confirmed same real agency: 13 of DVA's own programs run
    # continuously through it with zero disruption on either side.
    "Defence (Veterans' Affairs)": "Veterans' Affairs",
    "VA": "Veterans' Affairs",
    # Education / Employment
    "DESE": "Education, Skills and Employment",
    "EDUCATION": "Education",
    "EDUCATION AND TRAINING": "Education and Training",
    "Education Skills and Employment": "Education, Skills and Employment",
    "EMPLOYMENT": "Employment",
    "DEWR": "Employment and Workplace Relations",
    "EW&R": "Employment and Workplace Relations",
    # Finance / Treasury
    "FINANCE": "Finance",
    "TREASURY": "Treasury",
    # Foreign Affairs and Trade
    "DFAT": "Foreign Affairs and Trade",
    "FOREIGN AFFAIRS AND TRADE": "Foreign Affairs and Trade",
    "Foreign Affairs": "Foreign Affairs and Trade",
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
    # Unlike Agriculture/Health/Industry's own genuinely-different-era
    # names (see this dict's own docstring), these two are just the same
    # department's full official name spelled out in full for one
    # edition each -- confirmed via program continuity: 16 of 17 programs
    # under "...Regional Development and Cities" (2019-20 Budget only)
    # also appear under bare "Infrastructure" with no discontinuity, and
    # the same holds for "...and Regional Development" (2017-18 Budget).
    "INFRASTRUCTURE AND REGIONAL DEVELOPMENT": "Infrastructure",
    "INFRASTRUCTURE, REGIONAL DEVELOPMENT AND CITIES": "Infrastructure",
    "Infrastructure and Regional Development": "Infrastructure",
    "Infrastructure, Regional Development and Cities": "Infrastructure",
    "Infrastructure adn Regional Development": "Infrastructure",
    "ITRDCS&A": "Infrastructure",
    # Jobs -- "Jobs and Innovation" was a short-lived ministerial arrangement
    # name (2017-18) covering two actually-separate departments, each of
    # which explicitly names itself in its own raw portfolio string; bare
    # "Jobs and Innovation" never appears in any ingested table, so each
    # variant maps straight to its own real department rather than a
    # generic umbrella (confirmed for the Industry one via continuity: 13
    # of its 14 programs also appear under bare "Industry, Innovation and
    # Science" with no discontinuity).
    "JOBS AND INNOVATION": "Jobs and Innovation",
    "Jobs and Innovation Portfolio (Department of Industry, Innovation and Science)":
        "Industry, Innovation and Science",
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


def canon_portfolio(raw):
    """Cleans up a raw portfolio string for display/filtering -- see
    PORTFOLIO_ALIASES' own docstring for what is and isn't merged.
    Falsy input passes through unchanged (some rows have "", handled by
    the caller, same as before this function existed)."""
    if not raw:
        return raw
    stripped = strip_portfolio_prefix(raw)
    return PORTFOLIO_ALIASES.get(stripped, stripped)
