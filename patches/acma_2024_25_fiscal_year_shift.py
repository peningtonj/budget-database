"""
Patch: 2024-25 Budget -- ACMA's own PBS workbook (Table 2.1.1).

The table's own header row mislabels its columns: "2023-24 Estimated
actual", "2023-24 Budget" (the budget-year column should read "2024-25
Budget" -- it always matches the edition's own budget year), then jumps
straight to "2025-26 Forward estimate", skipping a genuine "2024-25"
label anywhere in the row. Confirmed by direct inspection of the source
cells -- this is the document's own defect, not a parsing bug.

parse_pbs.py's normalize_fiscal_years() (see its own docstring) is
designed for exactly this class of problem -- a mistyped column-year
label -- by deriving every column's real fiscal year from its position
relative to the budget column rather than trusting each column's own
text. It can't help here, though: it anchors on the budget column's own
(also wrong) text, so this whole-year mislabelling shifts every column
in the table back by one year instead of being corrected.

Confirmed isolated to this one file: no other 2024-25 Budget workbook
carries the same mislabelling (checked directly). Fix: shift every
record's fiscal_year forward by one year (2022-23 -> 2023-24, ...,
2026-27 -> 2027-28), recovering the table's real coverage.
"""

TARGET_FILE = "2024-25 Budget/Infrastructure/2024-25 PB Statement - ACMA.xlsx"


def _shift_forward_one_year(fy):
    start = int(fy.split("-")[0]) + 1
    return f"{start}-{(start + 1) % 100:02d}"


def apply(records):
    out = []
    for r in records:
        r = dict(r)
        r["fiscal_year"] = _shift_forward_one_year(r["fiscal_year"])
        out.append(r)
    return out
