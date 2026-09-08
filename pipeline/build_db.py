"""
Build the PBS Programs SQLite database from data/pbs/Budget.

Directory layout:
    data/pbs/Budget/<EDITION>/<PORTFOLIO>/<agency workbook>.xlsx

Each Budget PBS reports an "Estimated actual" for the year prior to its budget
year, plus the budget-year figure and forward estimates. We store every
column as a tidy long table so the estimated-actual series is one filter away.
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


def iter_files():
    exts = ("*.xlsx", "*.XLSX", "*.xls", "*.xlsm", "*.xlsb")
    for edition in sorted(os.listdir(BUDGET_DIR)):
        edir = os.path.join(BUDGET_DIR, edition)
        if not os.path.isdir(edir):
            continue
        for portfolio in sorted(os.listdir(edir)):
            pdir = os.path.join(edir, portfolio)
            if not os.path.isdir(pdir):
                continue
            files = []
            for pat in exts:
                files += glob.glob(os.path.join(pdir, pat))
            for f in sorted(set(files)):
                if os.path.basename(f).startswith(("~$", ".")):
                    continue
                yield edition, clean_portfolio(portfolio), f


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
        estimate_type       TEXT NOT NULL,   -- estimated_actual | budget | forward_estimate
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


def main(only=None):
    """only: an optional list of substrings matched (case-insensitively)
    against each file's own path relative to BUDGET_DIR -- e.g.
    "2022-23 October Budget/Social Services" to rescan one edition's
    portfolio folder, or "NDIA.xlsx" to rescan just one workbook. When
    given, this is a SCOPED reingest, not a full rebuild: the table is
    never dropped (ensure_schema, not create_schema), and only the
    matching files' own existing rows are cleared (by source_file)
    before they're reparsed and reinserted -- every other file's rows
    are left completely untouched. Built for exactly the situation a
    one- or two-row program-name fix keeps running into: a full rebuild
    re-scans 1,600+ workbooks to change a handful of rows, taking
    9-15 minutes when the actual affected file set is tiny.

    normalize_program_name_casing() still runs at the end over the
    WHOLE table regardless of scope -- it's a fast in-DB pass (no
    re-parsing), and a scoped run can still change which cross-edition
    casing group a program falls into (exactly what motivated adding
    this flag in the first place -- see build_db.py's own commit
    history), so it has to see the complete, current table to stay
    correct.
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
    for edition, portfolio, path in iter_files():
        rel = os.path.relpath(path, BUDGET_DIR)
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

    if only:
        print(f"Scoped to (--only) : {only}")
        print(f"Files matched      : {file_count}  (skipped {skipped} non-matching)")
    print(f"Files scanned      : {file_count}")
    print(f"Rows inserted      : {inserted}")
    print(f"Files w/ 0 records : {len(empty)}")
    print(f"Files w/ errors    : {len(errors)}")
    print(f"Conflicting dups   : {len(conflicts)}")
    print(f"Program names recased : {renamed}")
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
        description="Build (or scoped-reingest into) programs.db from data/pbs/Budget.",
    )
    parser.add_argument(
        "--only",
        action="append",
        default=None,
        metavar="PATH_SUBSTRING",
        help=(
            "Scope to files whose path (relative to data/pbs/Budget, e.g. "
            "'2022-23 October Budget/Social Services/2022-23 OCT PBS Excel "
            "Tables - NDIA.xlsx') contains this substring (case-insensitive). "
            "Repeatable to match several files/folders at once. Only those "
            "files' own rows are cleared and reinserted -- every other row in "
            "programs.db is left untouched, and the table itself is never "
            "dropped. Omit for a normal full rebuild."
        ),
    )
    args = parser.parse_args()
    main(only=args.only)
