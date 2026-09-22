"""
Build the PBS Programs SQLite database from data/pbs/Budget and
data/pbs/MYEFO.

Directory layout:
    data/pbs/Budget/<EDITION>/<PORTFOLIO>/<agency workbook>.xlsx
    data/pbs/MYEFO/<EDITION>/<PORTFOLIO>/<agency PAES workbook>.xlsx
    (2017-18 MYEFO alone has no <PORTFOLIO> layer -- its files sit flat in
    the edition root; see _myefo_portfolio_fallback_index.)

Each Budget PBS reports an "Estimated actual" for the year prior to its budget
year, plus the budget-year figure and forward estimates. Each PAES (a MYEFO's
own per-agency workbook) instead reports that same prior year's now-settled
"Actual expenses" (also stored as estimated_actual -- same authoritative
figure, just labelled differently once the year has actually closed), a
"Revised estimate" for its own current year (a mid-year update to that
year's Budget-time forecast, stored as revised_estimate -- see
parse_pbs.col_types), and forward estimates beyond that. We store every
column as a tidy long table so the estimated-actual series is one filter
away.
"""
import argparse
import os
import re
import glob
import sqlite3
import traceback
from collections import defaultdict

from parse_pbs import parse_workbook, norm
from patches import apply_patches
from patches.agency_name_overrides import OVERRIDES as AGENCY_NAME_OVERRIDES
from portfolio_aliases import canon_portfolio

# Repo root (this file lives in pipeline/). data/, programs.db and
# chroma_measures/ all sit here, one level up.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUDGET_DIR = os.path.join(ROOT, "data/pbs/Budget")
MYEFO_DIR = os.path.join(ROOT, "data/pbs/MYEFO")
DB_PATH = os.path.join(ROOT, "programs.db")

AGENCY_STRIP = re.compile(
    r"""(?ix)
      \d{4}\s*[-–]\s*\d{2}                 # 2025-26 (whitespace-tolerant: at least
                                           # one filename has "2026 -27" with a stray
                                           # space, otherwise left as "2026 27 AHL")
    | \b(pb|pbs)\b
    | portfolio\s+budget\s+statements?
    | pb\s+statements?
    | excel\s+tables?
    | \btables?\b
    | \bbudget\b(?!\s+office)              # Generic boilerplate in most filenames
                                           # ("Budget 2020-21 AIFS PBS Tables.xlsx",
                                           # "2021-22 Budget ANSTO.xlsx") -- but for a
                                           # handful of real agencies "Budget" is part
                                           # of the actual name ("Parliamentary Budget
                                           # Office", "Portfolio Budget Office"), so
                                           # left alone whenever "Office" follows it.
    | \bclean(?:ed)?\b                    # "clean(ed?)?": ?\b previously only made
                                           # the trailing "d" optional, requiring the
                                           # "e" of "ed" to always be present -- so it
                                           # matched "cleaned" but silently missed the
                                           # far more common bare "clean" (366 of 1506
                                           # source filenames), leaving e.g. "ACARA
                                           # clean" as agency instead of "ACARA".
    | \boct\b                             # 2022-23 October Budget's own filename
                                           # convention prefixes every file with "OCT"
                                           # (e.g. "2022-23 OCT PBS ... - ACARA.xlsx"),
                                           # otherwise left dangling as "OCT ACARA".
    | \boctober\b                         # Same edition, some portfolios (Finance,
                                           # Treasury) spell it out in full instead --
                                           # "October 2022-23 PBS - AEC.xlsx" -- left
                                           # dangling as "October AEC" otherwise.
    | \bstatements?\b
    | \bsatements?\b                      # Every 2025-26 Budget filename in this
                                           # batch misspells "Statement" as "Satement"
                                           # (5 files: AFP, NTC, SBS, Screen Australia,
                                           # OPH) -- tolerated alongside the correct
                                           # spelling rather than left dangling as
                                           # "Satement AFP".
    | \bpaes\b                            # MYEFO filenames: "<Agency> PAES 2024-25.xlsx"
    """)
# Health and Aged Care's own files for the 2022-23 October Budget are named
# as an internal routing note to Finance -- "For Finance - NHMRC 2022-23
# October PBS.xlsx" -- rather than just the agency, unlike every other
# portfolio/edition. Only strips that exact leading phrase (not the bare
# words "for"/"finance", which are legitimate agency-name content
# elsewhere, e.g. "Department of Finance" itself), so it's kept as its own
# prefix-anchored pattern rather than folded into AGENCY_STRIP's word list.
AGENCY_ROUTING_NOTE_RE = re.compile(r"^for\s+finance\s*[-–]\s*", re.I)


def clean_agency(filename):
    override = AGENCY_NAME_OVERRIDES.get(os.path.basename(filename))
    if override is not None:
        return override
    stem = os.path.splitext(os.path.basename(filename))[0]
    stem = AGENCY_ROUTING_NOTE_RE.sub("", stem)
    # Underscore counts as a \w character, so it satisfies regex \b word
    # boundaries the same as a letter does -- meaning AGENCY_STRIP's \b-
    # anchored patterns (PBS, clean, OCT, tables...) silently fail to match
    # inside an underscore-joined filename like "Communications_PBS_10_NMA"
    # unless underscores become real word breaks *before* AGENCY_STRIP runs.
    # (Dashes are left for the pass below -- AGENCY_STRIP's own year-range
    # pattern, \d{4}[-–]\d{2}, depends on that dash still being present.)
    stem = stem.replace("_", " ")
    stem = AGENCY_STRIP.sub(" ", stem)
    stem = re.sub(r"[\-–_]+", " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" -–")
    return stem or os.path.splitext(os.path.basename(filename))[0]


# The 2021-22 Budget and 2022-23 March Budget source folders are named
# "<year> PBS <Portfolio>" (e.g. "2021-22 PBS Health") -- every other
# edition's portfolio folder is just the bare portfolio name (e.g.
# "Health"). Left unstripped, this fragmented "Health" into two distinct
# portfolio strings depending on edition, which broke agency_aliases'
# same-portfolio lookups (_agency_history in backend/measures/views.py)
# for any agency whose only alias row came from one of these two editions
# -- e.g. the Aged Care Quality and Safety Commission's sole alias row is
# from 2022-23 March Budget, so it never matched a "Health" filter.
PORTFOLIO_YEAR_PREFIX = re.compile(r"^\d{4}[-–]\d{2}\s+pbs\s+", re.I)


def clean_portfolio(name):
    return PORTFOLIO_YEAR_PREFIX.sub("", name).strip()


def budget_year(edition):
    m = re.search(r"(\d{4}[-–]\d{2})", edition)
    return m.group(1).replace("–", "-") if m else edition


_EXTS = ("*.xlsx", "*.XLSX", "*.xls", "*.xlsm", "*.xlsb")


def _glob_workbooks(directory):
    files = []
    for pat in _EXTS:
        files += glob.glob(os.path.join(directory, pat))
    return sorted(f for f in set(files) if not os.path.basename(f).startswith(("~$", ".")))


# 2017-18 MYEFO's own flat layout (see _myefo_portfolio_fallback_index)
# resolves most agencies' portfolio by matching clean_agency(this MYEFO
# file) against clean_agency(that same fiscal year's own Budget file) --
# but 18 of its 48 files use a filename convention different enough from
# their own Budget-side sibling that the two clean_agency() outputs never
# match (e.g. "Education PAES 2017-18.xlsx" -> "Education", vs the
# Budget-side "Education and Training.xlsx" -> "Education and Training"),
# silently leaving those 18 agencies' whole rows with a blank portfolio.
# Confirmed one by one against the 2017-18 Budget directory's own listing
# (data/pbs/Budget/2017-18 Budget) -- the same "same fiscal year" source
# the general fallback already prefers -- except AFP and Home Affairs
# (the Department of Home Affairs itself), both created by the December
# 2017 machinery-of-government change that happened *between* the 2017-18
# Budget (May 2017) and this same MYEFO (February 2018): there's no
# 2017-18 Budget-side "Home Affairs" directory at all to match against,
# and by MYEFO's own publication date AFP had already moved out of
# Attorney-General's into the newly-created Home Affairs, so both are
# given that real, current-as-of-publication portfolio directly instead.
_MYEFO_2017_18_PORTFOLIO_OVERRIDES = {
    "AFP": "Home Affairs",
    "AGD": "Attorney General's",
    "BoM": "Environment and Energy",
    "Comms": "Communications and the Arts",
    "DFAT": "Foreign Affairs and Trade",
    "DHA": "Defence",
    "DIIS": "Industry, Innovation and Science",
    # Keeps the source folder's own typo ("adn") rather than correcting
    # it -- portfolio_aliases.py's own canon_portfolio() already has this
    # exact misspelling aliased to "Infrastructure", so matching it
    # verbatim is what lets this row canonicalize the same way every
    # other 2017-18 Budget row from that same (real, typo'd) folder does.
    "DIRDC": "Infrastructure adn Regional Development",
    "DSS": "Social Services",
    "DoAWR PASE": "Agriculture and Water Resources",  # filename typo "PASE" for "PAES"
    "Education": "Education and Training",
    "FWOROCE": "Employment",
    "FedCA": "Attorney General's",
    "HCA": "Attorney General's",
    "Home Affairs": "Home Affairs",
    "Jobs": "Employment",
    "OAIC": "Attorney General's",
    "OPC": "Attorney General's",
}


def _myefo_portfolio_fallback_index(myefo_edition_dir):
    """2017-18 MYEFO alone has no portfolio subdirectory layer at all --
    its agency files sit flat in the edition root, unlike every other
    Budget or MYEFO edition (see build_measures_db.py's own
    _portfolio_fallback_index, which the measures pipeline already relies
    on for this exact same layout gap). Falls back to that same fiscal
    year's own Budget edition's directory structure for a portfolio label,
    since an agency practically always sits in the same portfolio a few
    months later at MYEFO time as it did at Budget time within the one
    year -- topped up with _MYEFO_2017_18_PORTFOLIO_OVERRIDES for the
    handful of agencies that fallback can't reach on its own (see its own
    comment). Returns {agency_short_name: portfolio}, or just the
    overrides (still {} for every other edition) if there's no matching
    Budget directory to fall back to. portfolio here becomes
    program_expenses.portfolio directly -- part of a program's own
    (program_name, portfolio) identity everywhere else in this codebase,
    not merely a display label, so getting it right matters as much here
    as for a normally-laid-out edition.
    """
    budget_edition_dir = myefo_edition_dir.replace("/MYEFO/", "/Budget/").replace(" MYEFO", " Budget")
    index = {}
    if os.path.isdir(budget_edition_dir):
        for portfolio in sorted(os.listdir(budget_edition_dir)):
            pdir = os.path.join(budget_edition_dir, portfolio)
            if not os.path.isdir(pdir):
                continue
            for f in _glob_workbooks(pdir):
                index[clean_agency(f)] = clean_portfolio(portfolio)
    if os.path.basename(myefo_edition_dir) == "2017-18 MYEFO":
        index.update(_MYEFO_2017_18_PORTFOLIO_OVERRIDES)
    return index


def _iter_root(root_dir, use_fallback_for_stray_files=False):
    """Yield (edition, portfolio, path) for every workbook under one root
    (BUDGET_DIR or MYEFO_DIR) -- portfolio-nested layout, plus (only when
    `use_fallback_for_stray_files`) any workbook that sits directly in the
    edition root, via the fallback portfolio lookup: MYEFO_DIR's own flat
    2017-18-MYEFO layout (every file is "stray" -- no portfolio
    subdirectory layer at all), and 2022-23 MYEFO's own mix of the two (its
    DHOR workbook sits loose in the edition root alongside every other
    agency's normal Defence/, Social Services/, ... subdirectories).

    BUDGET_DIR has its own handful of stray top-level files (2023-24
    Budget's DHoR/DPS/PBO/Senate workbooks, 2024-25 Budget's DHR one) --
    left alone here (use_fallback_for_stray_files defaults to False)
    rather than opportunistically swept up the same way: unlike a MYEFO
    edition, there's no corresponding same-year Budget directory to derive
    a real portfolio from, so `_myefo_portfolio_fallback_index` would only
    ever fall back to an empty portfolio for them -- a pre-existing gap,
    not something this change is scoped to fix.
    """
    for edition in sorted(os.listdir(root_dir)):
        edir = os.path.join(root_dir, edition)
        if not os.path.isdir(edir):
            continue
        if use_fallback_for_stray_files:
            fallback = _myefo_portfolio_fallback_index(edir)
            for f in _glob_workbooks(edir):
                yield edition, fallback.get(clean_agency(f), ""), f
        for entry in sorted(os.listdir(edir)):
            pdir = os.path.join(edir, entry)
            if not os.path.isdir(pdir):
                continue
            for f in _glob_workbooks(pdir):
                yield edition, clean_portfolio(entry), f


def iter_files():
    """Yield (edition, portfolio, path, rel) for every Budget and MYEFO/PAES
    workbook. `rel` is the path patches/--only match against: Budget files
    keep their existing BUDGET_DIR-relative form (unchanged, so every
    existing patches/*.py TARGET_FILE and past --only invocation still
    matches); MYEFO files get an "MYEFO/"-prefixed, MYEFO_DIR-relative form
    instead -- distinct from any Budget rel (whose first path segment is
    always an edition folder, never literally "MYEFO"), so the two can
    never collide.
    """
    for edition, portfolio, path in _iter_root(BUDGET_DIR):
        yield edition, portfolio, path, os.path.relpath(path, BUDGET_DIR)
    for edition, portfolio, path in _iter_root(MYEFO_DIR, use_fallback_for_stray_files=True):
        yield edition, portfolio, path, "MYEFO/" + os.path.relpath(path, MYEFO_DIR)


_PROGRAM_EXPENSES_TABLE = """
    program_expenses (
        id                  INTEGER PRIMARY KEY,
        edition             TEXT NOT NULL,   -- e.g. '2025-26 Budget'
        budget_year         TEXT NOT NULL,   -- e.g. '2025-26'
        portfolio           TEXT NOT NULL,
        agency              TEXT NOT NULL,
        outcome_number      INTEGER NOT NULL,
        outcome_description TEXT,
        program_number      TEXT NOT NULL,   -- e.g. '1.1'
        program_name        TEXT,
        fiscal_year         TEXT NOT NULL,   -- the FY the amount refers to
        estimate_type       TEXT NOT NULL,   -- estimated_actual | revised_estimate | budget | forward_estimate
        amount_thousands    INTEGER NOT NULL,
        source_file         TEXT NOT NULL,
        sheet_name          TEXT
    )
"""


def create_schema(con):
    con.executescript(f"DROP TABLE IF EXISTS program_expenses; CREATE TABLE {_PROGRAM_EXPENSES_TABLE};")
    for col in ("estimate_type", "portfolio", "agency", "program_name",
                "fiscal_year", "budget_year"):
        con.execute(f"CREATE INDEX idx_pe_{col} ON program_expenses({col});")


def ensure_schema(con):
    """Same table/indexes as create_schema(), but CREATE ... IF NOT EXISTS
    rather than DROP + CREATE -- used by a scoped (--only) run, which must
    never wipe rows outside the files it's actually re-ingesting."""
    con.executescript(f"CREATE TABLE IF NOT EXISTS {_PROGRAM_EXPENSES_TABLE};")
    for col in ("estimate_type", "portfolio", "agency", "program_name",
                "fiscal_year", "budget_year"):
        con.execute(f"CREATE INDEX IF NOT EXISTS idx_pe_{col} ON program_expenses({col});")


def normalize_program_name_casing(con):
    """Rewrites program_name wherever two editions report what's clearly
    the same program with different capitalization only (confirmed: ~28
    pairs, e.g. "Delivery of Specialist Education" vs "...specialist
    education") -- the source workbook's own header casing drifting
    between editions, not a real change in identity. Every older edition's
    casing is rewritten to match whichever casing the *latest* edition
    (by budget_year, then edition string -- which already orders "2022-23
    March Budget" before "...October Budget" correctly) used for that
    program, so a program stops fragmenting purely on capitalization.

    Scoped by canonical portfolio (canon_portfolio, from the shared
    portfolio_aliases module also used by backend/measures/views.py), not
    just the case-folded name alone: checked directly against the data,
    20 of the 28 raw case-only pairs turned out to span genuinely
    different real portfolios once canonicalized -- a generic-sounding
    name ("Regional Development", "Local Government", "Secret
    Intelligence"...) reused by a different portfolio era or an unrelated
    department, not the same program. Folding those into one shared name
    purely because the text happens to match case-insensitively would
    misrepresent them as the same program everywhere identity is grouped
    by name (e.g. program_outcome_audit's own view). Only the 8 pairs that
    share one real canonical portfolio are safe to recase.

    Deliberately a separate, cross-edition pass over the fully-populated
    table rather than something clean_program_name() (parse_pbs.py) could
    ever do: that function only ever sees one workbook's own text at a
    time, with no way to know what casing some *other* edition used for
    the same program, let alone what portfolio era it belongs to. Run once
    after every file's own rows are already inserted.
    """
    rows = con.execute(
        "select distinct edition, budget_year, portfolio, program_name "
        "from program_expenses where program_name is not null"
    ).fetchall()

    groups = defaultdict(set)   # (canon_portfolio, fold_key) -> {(name, portfolio)}
    latest = {}                 # same key -> (sort_key, name)
    for edition, by, portfolio, name in rows:
        fold_key = re.sub(r"\s+", " ", name.strip().lower())
        group_key = (canon_portfolio(portfolio), fold_key)
        groups[group_key].add((name, portfolio))
        sort_key = (by, edition)
        if group_key not in latest or sort_key > latest[group_key][0]:
            latest[group_key] = (sort_key, name)

    renamed = 0
    for group_key, name_portfolio_pairs in groups.items():
        names = {n for n, _ in name_portfolio_pairs}
        if len(names) <= 1:
            continue
        canonical = latest[group_key][1]
        for name, portfolio in name_portfolio_pairs:
            if name == canonical:
                continue
            con.execute(
                "update program_expenses set program_name = ? "
                "where program_name = ? and portfolio = ?",
                (canonical, name, portfolio),
            )
            renamed += 1
    con.commit()
    return renamed


def normalize_portfolio_via_myefo(con):
    """Within one budget_year, a program's own portfolio should be
    consistent between its Budget filing and that same year's MYEFO/PAES
    filing -- but a machinery-of-government portfolio rename can land
    squarely between the two (the Budget in May, the MYEFO seven months
    later in December), giving the exact same program two different
    (program_name, portfolio) identities for one budget_year. Confirmed
    concretely: "Non-Government Schools National Support" was filed under
    "Education and Training" by the 2019-20 Budget but "Education, Skills
    and Employment" by the 2019-20 MYEFO -- the Department of Education
    and Training became the Department of Education, Skills and
    Employment in between. Left alone, that program's own FY2018-19
    "estimated actual" -- restated by BOTH editions, at very slightly
    different amounts -- ends up split across two program-identity lines
    instead of one, and "Combine programs with the same name"
    (ProgramMeasuresPage.svelte, which sums $ across combined portfolios
    per year) would double-count it.

    PAES/MYEFO is treated as authoritative: whenever a program_name's
    canonical portfolio (canon_portfolio -- comparing canonical, not raw,
    spellings so this doesn't fire on two raw strings that are already
    the same real portfolio, e.g. "DESE" vs "Education, Skills and
    Employment") differs between its budget_year's own Budget edition(s)
    and that same budget_year's own MYEFO edition, every one of that
    Budget edition's own rows for it is rewritten to MYEFO's own
    *canonical* portfolio (not its raw string, which can itself be a
    messy MYEFO-folder-derived spelling like "2020-21 PAES DESE") -- the
    MYEFO filing is the later, more current statement of which department
    the program actually sits in, seven months further into the same
    annual cycle.

    Deliberately conservative about *which* mismatches count as the same
    program continuing under a new name, rather than two unrelated
    programs that merely share a generic name (confirmed real cases:
    "Program Support", "Other Administered" -- both independently reused,
    within a single edition, by several completely different agencies'
    own outcome structures): only acts when (a) the MYEFO edition reports
    exactly one portfolio for that program_name (not itself internally
    ambiguous), (b) the Budget edition being rewritten also reports
    exactly one portfolio for it, and (c) the two editions' own
    program_number sets for that name actually overlap -- the numbering
    staying stable is what distinguishes "the same program, renamed
    portfolio" from a coincidental name collision between two different
    agencies' own unrelated programs. "Program Support"/"Other
    Administered" already fail check (a) or (b) on their own (each is
    ambiguous even within one single edition), but (c) guards the same
    risk for a generic name that only happens to resolve to one portfolio
    per edition.

    Run once, after every file's own rows are already inserted and after
    normalize_program_name_casing() (so program_name comparisons use its
    already-normalized casing) -- same pattern as that function.
    """
    rows = con.execute(
        "select edition, budget_year, portfolio, program_name, program_number "
        "from program_expenses where program_name is not null"
    ).fetchall()

    # (budget_year, program_name) -> {edition: {(portfolio, program_number), ...}}
    by_program = defaultdict(lambda: defaultdict(set))
    for edition, by, portfolio, name, pnum in rows:
        by_program[(by, name)][edition].add((portfolio, pnum))

    updated = 0
    changes = []  # (edition, program_name, old_portfolio, new_portfolio)
    for (by, name), by_edition in by_program.items():
        myefo_editions = [ed for ed in by_edition if ed.endswith("MYEFO")]
        # Only ever one MYEFO edition per budget_year in this dataset --
        # if that ever stops holding, skip rather than guess which wins.
        if len(myefo_editions) != 1:
            continue
        myefo_edition = myefo_editions[0]
        myefo_entries = by_edition[myefo_edition]
        myefo_portfolios = {p for p, _ in myefo_entries}
        if len(myefo_portfolios) != 1:
            continue
        myefo_portfolio_canon = canon_portfolio(next(iter(myefo_portfolios)))
        myefo_numbers = {n for _, n in myefo_entries}

        for edition, entries in by_edition.items():
            if edition == myefo_edition:
                continue
            portfolios = {p for p, _ in entries}
            if len(portfolios) != 1:
                continue
            portfolio = next(iter(portfolios))
            if canon_portfolio(portfolio) == myefo_portfolio_canon:
                continue
            numbers = {n for _, n in entries}
            if not (numbers & myefo_numbers):
                continue
            cur = con.execute(
                "update program_expenses set portfolio = ? "
                "where edition = ? and program_name = ? and portfolio = ?",
                (myefo_portfolio_canon, edition, name, portfolio),
            )
            updated += cur.rowcount
            changes.append((edition, name, portfolio, myefo_portfolio_canon))
    con.commit()
    return updated, changes


def main(only=None):
    """only: an optional list of substrings matched (case-insensitively)
    against each file's own `rel` (see iter_files) -- e.g.
    "2022-23 October Budget/Social Services" to rescan one Budget edition's
    portfolio folder, "MYEFO/2024-25 MYEFO" to rescan a whole MYEFO
    edition, or "NDIA.xlsx" to rescan just one workbook. When
    given, this is a SCOPED reingest, not a full rebuild: the table is
    never dropped (ensure_schema, not create_schema), and only the
    matching files' own existing rows are cleared (by source_file)
    before they're reparsed and reinserted -- every other file's rows
    are left completely untouched. Built for exactly the situation a
    one- or two-row program-name fix keeps running into: a full rebuild
    re-scans 1,600+ workbooks to change a handful of rows, taking
    9-15 minutes when the actual affected file set is tiny.

    normalize_program_name_casing() and normalize_portfolio_via_myefo()
    both still run at the end over the WHOLE table regardless of scope --
    both are fast in-DB passes (no re-parsing), and a scoped run can
    still change which cross-edition casing/portfolio group a program
    falls into (exactly what motivated adding this flag in the first
    place -- see build_db.py's own commit history), so both have to see
    the complete, current table to stay correct.
    """
    con = sqlite3.connect(DB_PATH)
    if only:
        ensure_schema(con)
    else:
        create_schema(con)
    inserted = 0
    empty, errors = [], []
    conflicts = []  # (source_file, key, kept_amount, dropped_amount)
    file_count = 0
    skipped = 0
    for edition, portfolio, path, rel in iter_files():
        if only and not any(pat.lower() in rel.lower() for pat in only):
            skipped += 1
            continue
        file_count += 1
        agency = clean_agency(path)
        by = budget_year(edition)
        try:
            recs = parse_workbook(path)
        except Exception as e:
            errors.append((rel, repr(e)))
            traceback.print_exc()
            continue
        if not recs:
            empty.append(rel)
            continue
        recs = apply_patches(rel, recs)
        if only:
            # Idempotent re-runs: clear this exact file's own previously
            # ingested rows before reinserting, so rerunning the same
            # --only doesn't duplicate rows the way it would in full-
            # rebuild mode (where the whole table was already dropped).
            con.execute("DELETE FROM program_expenses WHERE source_file = ?", (rel,))
        # Occasionally a source workbook mislabels a line (e.g. reuses a
        # program name inside a different program's block), producing two
        # conflicting amounts for the same key. Keep the first (top-to-
        # bottom, i.e. the primary program section) and drop the rest --
        # silently inserting both would corrupt any aggregate/sum query.
        seen = {}
        for r in recs:
            key = (r["outcome_number"], r["program_number"], r["fiscal_year"],
                   r["estimate_type"])
            if key in seen:
                if seen[key] != r["amount_thousands"]:
                    conflicts.append((rel, key, seen[key], r["amount_thousands"]))
                continue
            seen[key] = r["amount_thousands"]
            con.execute(
                """INSERT INTO program_expenses
                   (edition, budget_year, portfolio, agency, outcome_number,
                    outcome_description, program_number, program_name,
                    fiscal_year, estimate_type, amount_thousands,
                    source_file, sheet_name)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (edition, by, portfolio, agency, r["outcome_number"],
                 r["outcome_description"], r["program_number"],
                 r["program_name"], r["fiscal_year"], r["estimate_type"],
                 r["amount_thousands"], rel, r.get("sheet_name")))
            inserted += 1
    con.commit()

    renamed = normalize_program_name_casing(con)
    portfolios_normalized, portfolio_changes = normalize_portfolio_via_myefo(con)

    if only:
        print(f"Scoped to (--only) : {only}")
        print(f"Files matched      : {file_count}  (skipped {skipped} non-matching)")
    print(f"Files scanned      : {file_count}")
    print(f"Rows inserted      : {inserted}")
    print(f"Files w/ 0 records : {len(empty)}")
    print(f"Files w/ errors    : {len(errors)}")
    print(f"Conflicting dups   : {len(conflicts)}")
    print(f"Program names recased : {renamed}")
    print(f"Portfolios normalized to PAES/MYEFO : {portfolios_normalized}")
    if portfolio_changes:
        print("\n--- PORTFOLIO NORMALIZED TO MYEFO/PAES (edition, program, old -> new) ---")
        for edition, name, old, new in portfolio_changes:
            print("  ", edition, "|", name, "|", old, "->", new)
    if conflicts:
        print("\n--- CONFLICTING DUPLICATES (kept first, dropped rest) ---")
        for rel, key, kept, dropped in conflicts:
            print("  ", rel, key, "kept", kept, "dropped", dropped)
    if empty:
        print("\n--- ZERO-RECORD FILES ---")
        for e in empty:
            print("  ", e)
    if errors:
        print("\n--- ERROR FILES ---")
        for e, msg in errors:
            print("  ", e, "::", msg)
    con.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Build (or scoped-reingest into) programs.db from data/pbs/Budget and data/pbs/MYEFO.",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=None,
        metavar="PATH_SUBSTRING",
        help=(
            "Scope to files whose `rel` (relative to data/pbs/Budget for a "
            "Budget file, e.g. '2022-23 October Budget/Social Services/2022-23 "
            "OCT PBS Excel Tables - NDIA.xlsx'; 'MYEFO/'-prefixed and relative "
            "to data/pbs/MYEFO for a PAES file, e.g. 'MYEFO/2024-25 MYEFO/"
            "Agriculture/DAFF PAES 2024-25.xlsx') contains this substring "
            "(case-insensitive). Repeatable to match several files/folders at "
            "once. Only those files' own rows are cleared and reinserted -- "
            "every other row in programs.db is left untouched, and the table "
            "itself is never dropped. Omit for a normal full rebuild."
        ),
    )
    args = parser.parse_args()
    main(only=args.only)
