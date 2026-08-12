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
"""
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


def main():
    con = sqlite3.connect(DB_PATH)
    create_schema(con)
    measure_ids = MeasureIdAssigner()

    files_scanned = 0
    files_with_data = 0
    empty, errors = [], []
    impact_rows = 0
    program_rows = 0

    for root_dir, edition in EDITIONS:
        for portfolio, path in iter_files(root_dir):
            files_scanned += 1
            rel = os.path.relpath(path, ROOT)
            try:
                recs = parse_workbook_measures(path)
            except Exception as e:
                errors.append((rel, repr(e)))
                traceback.print_exc()
                continue
            if not recs:
                empty.append(rel)
                continue
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

    con.commit()

    print(f"Files scanned         : {files_scanned}")
    print(f"Files with measures    : {files_with_data}")
    print(f"Files w/ 0 records     : {len(empty)}")
    print(f"Files w/ errors        : {len(errors)}")
    print(f"measure_impacts rows   : {impact_rows}")
    print(f"measure_programs rows  : {program_rows}")
    cur = con.execute("SELECT COUNT(DISTINCT measure_name) FROM measure_impacts")
    print(f"Distinct measure names : {cur.fetchone()[0]}")
    print(f"Distinct measure ids   : {measure_ids.count()}")
    print(f"measure_id collisions resolved by salt bump: {measure_ids.collisions_resolved}")
    if errors:
        print("\n--- ERROR FILES ---")
        for e, msg in errors:
            print("  ", e, "::", msg)
    con.close()


if __name__ == "__main__":
    main()
