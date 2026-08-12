"""
Build the PBS Programs SQLite database from data/pbs/Budget.

Directory layout:
    data/pbs/Budget/<EDITION>/<PORTFOLIO>/<agency workbook>.xlsx

Each Budget PBS reports an "Estimated actual" for the year prior to its budget
year, plus the budget-year figure and forward estimates. We store every
column as a tidy long table so the estimated-actual series is one filter away.
"""
import os
import re
import glob
import sqlite3
import traceback

from parse_pbs import parse_workbook, norm

ROOT = "/Users/josephpenington/budget/budget-database"
BUDGET_DIR = os.path.join(ROOT, "data/pbs/Budget")
DB_PATH = os.path.join(ROOT, "programs.db")

AGENCY_STRIP = re.compile(
    r"""(?ix)
      \d{4}[-–]\d{2}                       # 2025-26
    | \b(pb|pbs)\b
    | portfolio\s+budget\s+statements?
    | pb\s+statement
    | excel\s+tables?
    | \btables?\b
    | \bcleaned?\b
    | \bstatement\b
    | \bpaes\b                            # MYEFO filenames: "<Agency> PAES 2024-25.xlsx"
    """)


def clean_agency(filename):
    stem = os.path.splitext(os.path.basename(filename))[0]
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


def create_schema(con):
    con.executescript("""
    DROP TABLE IF EXISTS program_expenses;
    CREATE TABLE program_expenses (
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
    );
    """)
    for col in ("estimate_type", "portfolio", "agency", "program_name",
                "fiscal_year", "budget_year"):
        con.execute(f"CREATE INDEX idx_pe_{col} ON program_expenses({col});")


def main():
    con = sqlite3.connect(DB_PATH)
    create_schema(con)
    inserted = 0
    empty, errors = [], []
    conflicts = []  # (source_file, key, kept_amount, dropped_amount)
    file_count = 0
    for edition, portfolio, path in iter_files():
        file_count += 1
        rel = os.path.relpath(path, BUDGET_DIR)
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

    print(f"Files scanned      : {file_count}")
    print(f"Rows inserted      : {inserted}")
    print(f"Files w/ 0 records : {len(empty)}")
    print(f"Files w/ errors    : {len(errors)}")
    print(f"Conflicting dups   : {len(conflicts)}")
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
    main()
