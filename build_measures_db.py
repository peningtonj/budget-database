"""
Build the Measures tables in programs.db from Table 1.2 (Budget and MYEFO).

Scope: all 19 Budget/MYEFO editions from 2017-18 through 2025-26 (both the
March and October 2022-23 Budgets). Ingestion started on a 3-edition
validation set (2025-26 Budget, 2024-25 Budget, 2024-25 MYEFO); expanded to
the full set once parse_measures.py's remaining structural gaps across the
older/wider file variety were found and fixed (see KNOWN_GAPS.md).

Schema (two tables, joined by measure_name + edition + agency + direction):

  measure_impacts -- the combined $ profile per (measure, agency, direction),
  one row per fiscal year. Summed across every program/category line that
  contributed, per the brief: "the financial impact ... combined into one
  profile" at the agency level.

  measure_programs -- the distinct list of programs touched by that same
  (measure, agency, direction) group. No $ amounts here by design -- join
  back to program_expenses on (budget_year/edition, agency, program_number)
  to resolve a program's name for that year, since numbers can be reused
  for a different program over time (the same reason program_expenses
  itself is keyed by program_name, not program_number, for cross-year
  identity).

Usage:

  python3 build_measures_db.py
      Full rebuild -- drops and re-ingests every edition. What CI/the
      release script (package_release_data.sh) runs. Slow (~1900 files,
      several minutes): every parser change re-walks the entire dataset
      even when the bug it fixed was scoped to one agency's file format.

  python3 build_measures_db.py --edition "2022-23 October Budget" [--edition ...]
      Re-ingests only the named edition(s) -- deletes just their own rows
      first, leaves every other edition's rows untouched. The right size
      for "I fixed a parsing bug that only affects this edition's file
      convention" (the common case: a bug is usually scoped to one
      edition's or one agency's own layout, not universal). Requires the
      tables to already exist (run a full build at least once first).

  python3 build_measures_db.py --file "data/pbs/Budget/.../EDU.xlsx" [--file ...]
      Re-ingests only the named workbook(s) (path relative to this
      script's directory, or absolute) -- its edition/portfolio are
      inferred from its path. Deletes just that file's own rows first.
      The fastest loop for iterating on a single file's own parsing
      (pair with parse_workbook_measures(path) directly first to check
      the parse looks right before persisting it here -- see this
      session's own README/CLAUDE.md for that one-liner).

  --edition and --file combine freely; a --file already covered by one of
  the given --edition values is skipped (the edition-level walk already
  re-ingests it) rather than processed twice.
"""
import argparse
import os
import glob
import sqlite3
import traceback
from collections import defaultdict

from parse_measures import parse_workbook_measures
from build_db import clean_agency
from measure_id import MeasureIdAssigner

ROOT = "/Users/josephpenington/budget/budget-database"
DB_PATH = os.path.join(ROOT, "programs.db")

# (root_dir, edition_label)
EDITIONS = [
    (os.path.join(ROOT, "data/pbs/Budget/2017-18 Budget"), "2017-18 Budget"),
    (os.path.join(ROOT, "data/pbs/Budget/2018-19 Budget"), "2018-19 Budget"),
    (os.path.join(ROOT, "data/pbs/Budget/2019-20 Budget"), "2019-20 Budget"),
    (os.path.join(ROOT, "data/pbs/Budget/2020-21 Budget"), "2020-21 Budget"),
    (os.path.join(ROOT, "data/pbs/Budget/2021-22 Budget"), "2021-22 Budget"),
    (os.path.join(ROOT, "data/pbs/Budget/2022-23 March Budget"), "2022-23 March Budget"),
    (os.path.join(ROOT, "data/pbs/Budget/2022-23 October Budget"), "2022-23 October Budget"),
    (os.path.join(ROOT, "data/pbs/Budget/2023-24 Budget"), "2023-24 Budget"),
    (os.path.join(ROOT, "data/pbs/Budget/2024-25 Budget"), "2024-25 Budget"),
    (os.path.join(ROOT, "data/pbs/Budget/2025-26 Budget"), "2025-26 Budget"),
    (os.path.join(ROOT, "data/pbs/MYEFO/2017-18 MYEFO"), "2017-18 MYEFO"),
    (os.path.join(ROOT, "data/pbs/MYEFO/2018-19 MYEFO"), "2018-19 MYEFO"),
    (os.path.join(ROOT, "data/pbs/MYEFO/2019-20 MYEFO"), "2019-20 MYEFO"),
    (os.path.join(ROOT, "data/pbs/MYEFO/2020-21 MYEFO"), "2020-21 MYEFO"),
    (os.path.join(ROOT, "data/pbs/MYEFO/2021-22 MYEFO"), "2021-22 MYEFO"),
    (os.path.join(ROOT, "data/pbs/MYEFO/2022-23 MYEFO"), "2022-23 MYEFO"),
    (os.path.join(ROOT, "data/pbs/MYEFO/2023-24 MYEFO"), "2023-24 MYEFO"),
    (os.path.join(ROOT, "data/pbs/MYEFO/2024-25 MYEFO"), "2024-25 MYEFO"),
    (os.path.join(ROOT, "data/pbs/MYEFO/2025-26 MYEFO"), "2025-26 MYEFO"),
]


def _portfolio_fallback_index(myefo_root_dir):
    """2017-18 MYEFO alone has no portfolio subdirectory layer at all --
    its 48 agency files sit flat in the edition root (every other
    edition, Budget or MYEFO, organizes files one level down inside a
    portfolio directory). iter_files() falls back to this for a
    portfolio label rather than leaving it blank: that same fiscal
    year's own Budget edition's directory structure, since an agency
    practically always sits in the same portfolio a few months later at
    MYEFO time as it did at Budget time within the one year. Returns
    {agency_short_name: portfolio}, or {} if there's no matching Budget
    directory to fall back to (portfolio is a display-only field either
    way, never part of the measure_impacts/measure_programs join key)."""
    budget_root = myefo_root_dir.replace("/MYEFO/", "/Budget/").replace(" MYEFO", " Budget")
    if not os.path.isdir(budget_root):
        return {}
    index = {}
    for portfolio in sorted(os.listdir(budget_root)):
        pdir = os.path.join(budget_root, portfolio)
        if not os.path.isdir(pdir):
            continue
        for f in glob.glob(os.path.join(pdir, "*.xls*")):
            index[clean_agency(f)] = portfolio
    return index


def iter_files(root_dir):
    entries = sorted(os.listdir(root_dir))
    if not any(os.path.isdir(os.path.join(root_dir, e)) for e in entries):
        # Flat layout -- see _portfolio_fallback_index.
        fallback = _portfolio_fallback_index(root_dir)
        files = glob.glob(os.path.join(root_dir, "*.xlsx")) + glob.glob(os.path.join(root_dir, "*.xls"))
        for f in sorted(set(files)):
            if os.path.basename(f).startswith(("~$", ".")):
                continue
            yield fallback.get(clean_agency(f), ""), f
        return

    for portfolio in entries:
        pdir = os.path.join(root_dir, portfolio)
        if not os.path.isdir(pdir):
            continue
        files = glob.glob(os.path.join(pdir, "*.xlsx")) + glob.glob(os.path.join(pdir, "*.xls"))
        for f in sorted(set(files)):
            if os.path.basename(f).startswith(("~$", ".")):
                continue
            yield portfolio, f


def _edition_for_path(path):
    """Which EDITIONS entry a --file argument falls under, by path
    prefix -- so --file alone (no --edition needed alongside it) is
    enough to know both the edition label to insert and which root_dir
    to resolve its portfolio against below."""
    abspath = os.path.abspath(path)
    for root_dir, edition in EDITIONS:
        if os.path.commonpath([abspath, root_dir]) == root_dir:
            return root_dir, edition
    raise SystemExit(
        f"--file isn't inside any known edition directory: {path}\n"
        f"(expected it under one of the data/pbs/Budget|MYEFO/<edition> roots in EDITIONS)"
    )


def _portfolio_for_path(path, root_dir):
    """Same portfolio-resolution iter_files() applies to every file it
    walks, applied here to a single --file passed outside that walk:
    the immediate subdirectory name under the edition root, or the
    2017-18-MYEFO-style flat-layout fallback if there isn't one."""
    rel = os.path.relpath(os.path.abspath(path), root_dir)
    parts = rel.split(os.sep)
    if len(parts) > 1:
        return parts[0]
    return _portfolio_fallback_index(root_dir).get(clean_agency(path), "")


def create_schema(con):
    con.executescript("""
    DROP TABLE IF EXISTS measure_impacts;
    DROP TABLE IF EXISTS measure_programs;
    CREATE TABLE measure_impacts (
        id                INTEGER PRIMARY KEY,
        measure_id        TEXT NOT NULL,   -- stable 8-digit id, see _measure_id()
        edition           TEXT NOT NULL,
        measure_name      TEXT NOT NULL,
        portfolio         TEXT NOT NULL,
        agency            TEXT NOT NULL,
        direction         TEXT NOT NULL,   -- 'payment' | 'receipt'
        fiscal_year       TEXT NOT NULL,
        amount_thousands  INTEGER NOT NULL,
        source_file       TEXT NOT NULL
    );
    CREATE TABLE measure_programs (
        id                INTEGER PRIMARY KEY,
        measure_id        TEXT NOT NULL,
        edition           TEXT NOT NULL,
        measure_name      TEXT NOT NULL,
        portfolio         TEXT NOT NULL,
        agency            TEXT NOT NULL,
        direction         TEXT NOT NULL,
        program_number    TEXT NOT NULL,   -- 'X.0' + is_departmental=1 means
        is_departmental   INTEGER NOT NULL,-- "Departmental (Outcome X)", not a real program
        source_file       TEXT NOT NULL
    );
    """)
    for col in ("edition", "measure_name", "agency", "direction", "measure_id"):
        con.execute(f"CREATE INDEX idx_mi_{col} ON measure_impacts({col});")
        con.execute(f"CREATE INDEX idx_mp_{col} ON measure_programs({col});")


def _ensure_schema_exists(con):
    tables = {
        r[0]
        for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('measure_impacts','measure_programs')"
        )
    }
    if tables != {"measure_impacts", "measure_programs"}:
        raise SystemExit(
            "measure_impacts/measure_programs don't exist yet in programs.db -- "
            "run a full build first (no --edition/--file) before using a subset rebuild."
        )


def _seed_from_existing(con, measure_ids):
    """Pre-loads every (measure_name, edition) -> measure_id pair
    already in programs.db (read BEFORE this run's own DELETEs, since
    that's the only place the about-to-be-replaced rows' own correct
    ids are still available) -- see MeasureIdAssigner.seed_known's own
    docstring for why a straight cache-seed, not just adding the ids to
    the collision set, is required for a subset run to keep every
    measure's id stable. UNION of both tables since either one alone
    can be incomplete for a given (measure_name, edition) -- e.g. a
    measure with $0 impact everywhere still gets a measure_impacts row,
    but one recorded as touching zero programs wouldn't have a
    measure_programs row at all.
    """
    rows = con.execute(
        "SELECT measure_name, edition, measure_id FROM measure_impacts "
        "UNION SELECT measure_name, edition, measure_id FROM measure_programs"
    ).fetchall()
    measure_ids.seed_known(rows)


def parse_args():
    p = argparse.ArgumentParser(
        description="Build/rebuild measure_impacts + measure_programs in programs.db.",
        epilog=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--edition",
        action="append",
        default=None,
        metavar="EDITION",
        help='Only re-ingest this edition (e.g. "2022-23 October Budget"). '
        "Repeatable. Omit for a full rebuild of every edition.",
    )
    p.add_argument(
        "--file",
        action="append",
        default=None,
        metavar="PATH",
        help="Only re-ingest this one source workbook. Repeatable, combinable with --edition.",
    )
    return p.parse_args()


def main():
    args = parse_args()
    subset = bool(args.edition or args.file)

    con = sqlite3.connect(DB_PATH)
    if subset:
        _ensure_schema_exists(con)
    else:
        create_schema(con)

    if args.edition:
        known = {edition for _, edition in EDITIONS}
        unknown = set(args.edition) - known
        if unknown:
            raise SystemExit(
                f"Unknown --edition value(s): {sorted(unknown)}\n"
                f"Valid editions: {[e for _, e in EDITIONS]}"
            )
        editions_to_run = [(root, edition) for root, edition in EDITIONS if edition in args.edition]
    elif args.file:
        editions_to_run = []  # --file only: don't walk any edition root at all
    else:
        editions_to_run = EDITIONS

    editions_to_run_labels = {edition for _, edition in editions_to_run}
    explicit_files = []
    if args.file:
        for f in args.file:
            path = f if os.path.isabs(f) else os.path.join(ROOT, f)
            if not os.path.isfile(path):
                raise SystemExit(f"--file not found: {f}")
            root_dir, edition = _edition_for_path(path)
            if edition in editions_to_run_labels:
                continue  # already covered by an --edition value above
            portfolio = _portfolio_for_path(path, root_dir)
            explicit_files.append((portfolio, path, edition))

    measure_ids = MeasureIdAssigner()
    # touched_editions is deliberately ONLY editions named via --edition,
    # NOT also every edition an explicit --file happens to fall under --
    # a --file with no accompanying --edition must delete/reseed just
    # that one file's own rows, not its whole edition's (an earlier
    # version of this script folded explicit_files' editions in here
    # too and it bulk-deleted the rest of that edition's files' data
    # out from under a plain --file run).
    touched_editions = set()
    touched_files = set()
    if subset:
        touched_editions = editions_to_run_labels
        touched_files = {os.path.relpath(path, ROOT) for _, path, _ in explicit_files}
        # Read BEFORE the deletes below -- see _seed_from_existing's own
        # docstring for why every existing key needs seeding (not just
        # ones outside what's about to be deleted).
        _seed_from_existing(con, measure_ids)
        for edition in touched_editions:
            con.execute("DELETE FROM measure_impacts WHERE edition = ?", (edition,))
            con.execute("DELETE FROM measure_programs WHERE edition = ?", (edition,))
        for rel in touched_files:
            con.execute("DELETE FROM measure_impacts WHERE source_file = ?", (rel,))
            con.execute("DELETE FROM measure_programs WHERE source_file = ?", (rel,))

    files_scanned = 0
    files_with_data = 0
    empty, errors = [], []
    impact_rows = 0
    program_rows = 0

    def process(portfolio, path, edition):
        nonlocal files_scanned, files_with_data, impact_rows, program_rows
        files_scanned += 1
        rel = os.path.relpath(path, ROOT)
        try:
            recs = parse_workbook_measures(path)
        except Exception as e:
            errors.append((rel, repr(e)))
            traceback.print_exc()
            return
        if not recs:
            empty.append(rel)
            return
        files_with_data += 1

        # Combine to one $ profile per (measure, agency, direction, fy).
        impacts = defaultdict(int)
        # Distinct programs touched per (measure, agency, direction).
        programs = defaultdict(lambda: defaultdict(bool))  # key -> {(prog_num, is_dept): True}

        for r in recs:
            key = (r["measure_name"], r["agency"], r["direction"], r["fiscal_year"])
            impacts[key] += r["amount_thousands"]
            group_key = (r["measure_name"], r["agency"], r["direction"])
            for p in r["programs"]:
                programs[group_key][(p, r["is_departmental"])] = True

        for (mname, agency, direction, fy), amt in impacts.items():
            con.execute(
                """INSERT INTO measure_impacts
                   (measure_id, edition, measure_name, portfolio, agency, direction,
                    fiscal_year, amount_thousands, source_file)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (measure_ids.get(mname, edition), edition, mname, portfolio, agency,
                 direction, fy, amt, rel))
            impact_rows += 1

        for (mname, agency, direction), prog_set in programs.items():
            for (prog_num, is_dept) in prog_set:
                con.execute(
                    """INSERT INTO measure_programs
                       (measure_id, edition, measure_name, portfolio, agency, direction,
                        program_number, is_departmental, source_file)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (measure_ids.get(mname, edition), edition, mname, portfolio, agency,
                     direction, prog_num, int(is_dept), rel))
                program_rows += 1

    for root_dir, edition in editions_to_run:
        for portfolio, path in iter_files(root_dir):
            process(portfolio, path, edition)
    for portfolio, path, edition in explicit_files:
        process(portfolio, path, edition)

    con.commit()

    if subset:
        print(f"Subset rebuild -- editions: {sorted(touched_editions) or '(none)'}")
        if explicit_files:
            print(f"                  extra files: {sorted(touched_files)}")
    print(f"Files scanned         : {files_scanned}")
    print(f"Files with measures    : {files_with_data}")
    print(f"Files w/ 0 records     : {len(empty)}")
    print(f"Files w/ errors        : {len(errors)}")
    print(f"measure_impacts rows   : {impact_rows}")
    print(f"measure_programs rows  : {program_rows}")
    cur = con.execute("SELECT COUNT(DISTINCT measure_name) FROM measure_impacts")
    print(f"Distinct measure names (whole DB) : {cur.fetchone()[0]}")
    # measure_ids.count() includes every pre-existing key seeded from disk
    # on a subset run (see _seed_from_existing), not just ones this run's
    # own file processing looked up -- so it reads as "whole DB" there too,
    # not "this run", to avoid implying a subset run touched that many.
    ids_label = "whole DB" if subset else "this run"
    print(f"Distinct measure ids ({ids_label}) : {measure_ids.count()}")
    print(f"measure_id collisions resolved by salt bump: {measure_ids.collisions_resolved}")
    if errors:
        print("\n--- ERROR FILES ---")
        for e, msg in errors:
            print("  ", e, "::", msg)
    con.close()


if __name__ == "__main__":
    main()
