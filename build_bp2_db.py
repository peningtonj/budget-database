"""
Build the BP2 measure-text tables in programs.db from Budget Paper No. 2
(and MYEFO's own equivalent Appendix A) -- the narrative measure write-
ups parse_bp2.py extracts, as opposed to the per-agency PBS/PAES Table
1.2 spreadsheets parse_measures.py/build_measures_db.py handle.

Scope: every 2014-15-through-2025-26 Budget and MYEFO document (see
EDITIONS below) except 2014-15 Budget. Pre-2020-21 editions use an
older three-part "Part 1: Revenue Measures" / "Part 2: Expense
Measures" / "Part 3: Capital Measures" structure (vs. the current
two-part Receipt/Payment split, with no separate Capital section) --
parse_bp2.py's SECTION_MARKER_RE/FIN_TABLE_HEADER_RE/TOTAL_ROW_RE all
accept both eras' wording, normalized to one canonical
receipt/payment/capital value via _canon_impact_word(). Those editions
also mix Arial/Helvetica and TimesNewRomanPSMT/"Times New Roman" font
naming depending on which tool generated that year's PDF -- see
_is_plain_sans()/_is_bullet_word() in parse_bp2.py.

2014-15 Budget.pdf alone is excluded: its "PART N: ... MEASURES"
divider uses a more extreme drop-cap effect than
normalize_portfolio_heading() repairs -- the first-letter-of-each-word
portion and the rest-of-word portion land far enough apart vertically
to land in two separate extracted lines with word-interleaved
fragments (e.g. "P 1: R M" / "ART EVENUE EASURES"), not just awkward
spacing on one line -- so SECTION_MARKER_RE never matches and the whole
file returns zero records. 2014-15 MYEFO is unaffected (its own
section markers are plain "Revenue/Expense/Capital Measures" text with
no drop-cap styling) and ingests normally. See KNOWN_GAPS.md.

Lookup key: (measure_name, edition), which should be unique -- portfolio
is stored as a plain field for display, not part of the key, per the
call made when this table was designed (agency/portfolio naming drifts
in ways measure_name canonicalization already accounts for; layering a
second lookup key on top of it isn't worth the complexity for data
that's only ever looked up by measure_name + edition in practice).

measure_id (see measure_id.py) makes every one of these 21 editions'
measures reachable by a short shareable URL and searchable, including
the 18 editions with no corresponding measure_impacts/measure_programs
row at all (those tables only cover 3 editions -- see
build_measures_db.py) -- the frontend degrades gracefully to a text-
only view (no $ breakdown/charts) for a measure with a BP2 write-up but
no PBS Table 1.2 data.

Deliberately NOT done here: merging headline_financials into
measure_impacts (a separate step -- when it happens, only fill gaps,
never overwrite PBS-sourced figures), and resolving related_measures
references to actual measure_name rows (deferred to run time).
"""
import os
import sqlite3

from parse_bp2 import extract_measure_records
from measure_id import MeasureIdAssigner

ROOT = "/Users/josephpenington/budget/budget-database"
DB_PATH = os.path.join(ROOT, "programs.db")

# (pdf_path, edition_label) -- edition_label matches the convention
# program_expenses.edition/measure_impacts.edition already use (filename
# minus ".pdf", stray whitespace trimmed) so a future join lines up
# without another normalization pass.
EDITIONS = [
    # 2014-15 Budget.pdf deliberately omitted -- see module docstring.
    (os.path.join(ROOT, "data/papers/Budget/2015-16 Budget.pdf"), "2015-16 Budget"),
    (os.path.join(ROOT, "data/papers/Budget/2016-17 Budget.pdf"), "2016-17 Budget"),
    (os.path.join(ROOT, "data/papers/Budget/2017-18 Budget.pdf"), "2017-18 Budget"),
    (os.path.join(ROOT, "data/papers/Budget/2018-19 Budget.pdf"), "2018-19 Budget"),
    (os.path.join(ROOT, "data/papers/Budget/2019-20 Budget.pdf"), "2019-20 Budget"),
    (os.path.join(ROOT, "data/papers/Budget/2020-21 Budget.pdf"), "2020-21 Budget"),
    (os.path.join(ROOT, "data/papers/Budget/2021-22 Budget.pdf"), "2021-22 Budget"),
    (os.path.join(ROOT, "data/papers/Budget/2022-23 March Budget.pdf"), "2022-23 March Budget"),
    (os.path.join(ROOT, "data/papers/Budget/2022-23 October Budget.pdf"), "2022-23 October Budget"),
    (os.path.join(ROOT, "data/papers/Budget/2023-24 Budget.pdf"), "2023-24 Budget"),
    (os.path.join(ROOT, "data/papers/Budget/2024-25 Budget.pdf"), "2024-25 Budget"),
    (os.path.join(ROOT, "data/papers/Budget/2025-26 Budget.pdf"), "2025-26 Budget"),
    (os.path.join(ROOT, "data/papers/MYEFO/2014-15 MYEFO.pdf"), "2014-15 MYEFO"),
    (os.path.join(ROOT, "data/papers/MYEFO/2015-16 MYEFO.pdf"), "2015-16 MYEFO"),
    (os.path.join(ROOT, "data/papers/MYEFO/2016-17 MYEFO.pdf"), "2016-17 MYEFO"),
    (os.path.join(ROOT, "data/papers/MYEFO/2017-18 MYEFO.pdf"), "2017-18 MYEFO"),
    (os.path.join(ROOT, "data/papers/MYEFO/2018-19 MYEFO.pdf"), "2018-19 MYEFO"),
    (os.path.join(ROOT, "data/papers/MYEFO/2019-20 MYEFO.pdf"), "2019-20 MYEFO"),
    (os.path.join(ROOT, "data/papers/MYEFO/2020-21 MYEFO.pdf"), "2020-21 MYEFO"),
    (os.path.join(ROOT, "data/papers/MYEFO/2021-22 MYEFO.pdf"), "2021-22 MYEFO"),
    (os.path.join(ROOT, "data/papers/MYEFO/2023-24 MYEFO .pdf"), "2023-24 MYEFO"),
    (os.path.join(ROOT, "data/papers/MYEFO/2024-25 MYEFO.pdf"), "2024-25 MYEFO"),
    (os.path.join(ROOT, "data/papers/MYEFO/2025-26 MYEFO.pdf"), "2025-26 MYEFO"),
]


def create_schema(con):
    con.executescript(
        """
        DROP TABLE IF EXISTS measure_text_related;
        DROP TABLE IF EXISTS measure_text_component;
        DROP TABLE IF EXISTS measure_text_headline_financial;
        DROP TABLE IF EXISTS measure_text;

        CREATE TABLE measure_text (
            id                 INTEGER PRIMARY KEY,
            measure_id         TEXT NOT NULL,   -- stable 8-digit id, see measure_id.py
            measure_name       TEXT NOT NULL,
            edition            TEXT NOT NULL,
            portfolio          TEXT NOT NULL,   -- display only, not part of the lookup key
            document_section   TEXT NOT NULL,   -- 'payment' | 'receipt' | 'capital'
            source_page        INTEGER NOT NULL,
            full_measure_text  TEXT NOT NULL,
            source_file        TEXT NOT NULL,
            UNIQUE (measure_name, edition)
        );

        CREATE TABLE measure_text_headline_financial (
            id                    INTEGER PRIMARY KEY,
            measure_text_id       INTEGER NOT NULL REFERENCES measure_text(id),
            impact_type           TEXT NOT NULL,   -- 'Receipt' | 'Payment' | 'Capital'
            is_related            INTEGER NOT NULL,
            department_name       TEXT NOT NULL,
            year_index            INTEGER NOT NULL,
            value_kind            TEXT NOT NULL,   -- 'numeric' | 'special'
            value_numeric_million REAL,
            value_raw             TEXT NOT NULL
        );

        CREATE TABLE measure_text_component (
            id               INTEGER PRIMARY KEY,
            measure_text_id  INTEGER NOT NULL REFERENCES measure_text(id),
            ordinal          INTEGER NOT NULL,
            level            INTEGER NOT NULL,   -- 1 | 2
            marker           TEXT NOT NULL,       -- 'dot' | 'dash'
            parent_ordinal   INTEGER,
            text             TEXT NOT NULL
        );

        CREATE TABLE measure_text_related (
            id               INTEGER PRIMARY KEY,
            measure_text_id  INTEGER NOT NULL REFERENCES measure_text(id),
            ordinal          INTEGER NOT NULL,
            phrase           TEXT NOT NULL
        );

        CREATE INDEX idx_measure_text_lookup ON measure_text(measure_name, edition);
        CREATE INDEX idx_measure_text_measure_id ON measure_text(measure_id);
        """
    )


def main():
    con = sqlite3.connect(DB_PATH)
    create_schema(con)
    measure_ids = MeasureIdAssigner()

    inserted = 0
    skipped_dupes = []
    for pdf_path, edition in EDITIONS:
        rel = os.path.relpath(pdf_path, ROOT)
        records = extract_measure_records(pdf_path)
        for rec in records:
            try:
                cur = con.execute(
                    """INSERT INTO measure_text
                       (measure_id, measure_name, edition, portfolio, document_section,
                        source_page, full_measure_text, source_file)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        measure_ids.get(rec["measure_title"], edition),
                        rec["measure_title"],
                        edition,
                        rec["portfolio_name"] or "",
                        rec["document_section"] or "",
                        rec["source_page"],
                        rec["full_measure_text"],
                        rel,
                    ),
                )
            except sqlite3.IntegrityError:
                # Same measure_title appears more than once in this one
                # PDF (e.g. a genuinely duplicated write-up, or two
                # distinct measures that happen to canonicalize to the
                # same name) -- keep the first, log the rest rather than
                # silently overwriting or crashing the whole ingest.
                skipped_dupes.append((edition, rec["measure_title"]))
                continue

            measure_text_id = cur.lastrowid
            inserted += 1

            for row in rec["headline_financials"]:
                for year_index, value in enumerate(row["values"]):
                    con.execute(
                        """INSERT INTO measure_text_headline_financial
                           (measure_text_id, impact_type, is_related, department_name,
                            year_index, value_kind, value_numeric_million, value_raw)
                           VALUES (?,?,?,?,?,?,?,?)""",
                        (
                            measure_text_id,
                            row["impact_type"],
                            row["is_related"],
                            row["department_name"],
                            year_index,
                            value["value_kind"],
                            value["value_numeric_million"],
                            value["value_raw"],
                        ),
                    )

            for ordinal, c in enumerate(rec["components"]):
                con.execute(
                    """INSERT INTO measure_text_component
                       (measure_text_id, ordinal, level, marker, parent_ordinal, text)
                       VALUES (?,?,?,?,?,?)""",
                    (measure_text_id, ordinal, c["level"], c["marker"], c["parent_ordinal"], c["text"]),
                )

            for ordinal, phrase in enumerate(rec["related_measures"]):
                con.execute(
                    """INSERT INTO measure_text_related
                       (measure_text_id, ordinal, phrase)
                       VALUES (?,?,?)""",
                    (measure_text_id, ordinal, phrase),
                )

    con.commit()

    print(f"measure_text rows inserted : {inserted}")
    print(f"skipped duplicates         : {len(skipped_dupes)}")
    print(f"distinct measure ids       : {measure_ids.count()}")
    print(f"id collisions resolved     : {measure_ids.collisions_resolved}")
    for edition, title in skipped_dupes:
        print("  ", edition, "::", title)
    con.close()


if __name__ == "__main__":
    main()
