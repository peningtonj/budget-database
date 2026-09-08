"""
Patch: 2021-22 Budget PBS -- Department of Social Services.

DSS restructured its outcome/program numbering partway through the 2021-22
Budget cycle. The workbook carries the *current*-structure expense tables
(e.g. "Table 2.3.1") side by side with "... old"-suffixed sheets for the
same outcomes (e.g. "Table 2.3.1 old", titled inside the sheet as
"Budgeted expenses for former Outcome N"), which show the same programs
under the pre-restructure layout.

Because the restructure took effect *within* the 2020-21 financial year,
every current-structure table reports a literal $0 for the whole "2020-21
estimated actual" column -- the new program numbering simply didn't exist
yet that year -- while every "old" table reports $0 for 2021-22 onward
instead (verified by direct cell inspection of both sheet families).
build_db.py's generic conflict resolution already notices the two tables
disagree on these cells, but since it always keeps whichever sheet it
reads first, the current-structure sheet's $0 wins and the "old" sheet's
real 2020-21 figure is silently dropped as a "conflict".

This patch recovers that figure in one of two ways, depending on whether
the program's *name* survived the restructure under the same program
number:

- Same number, same name (e.g. Program 3.2 "National Disability Insurance
  Scheme") -- the program itself didn't change, so the old table's 2020-21
  figure is exactly the right number for the current-structure row.
  Substituted in directly.
- Same number, different name (e.g. Program 1.1 was "Family Tax Benefit"
  pre-restructure, "Family Assistance" post -- the program's scope itself
  moved, not just its label) -- there's no current-structure row this
  figure can honestly be attributed to. Rather than drop a real,
  government-reported dollar figure entirely, it's inserted as its own
  one-year record under the name PBS itself used for it that year, tagged
  "(pre-restructure)" on its program number so it reads as historical
  rather than as a program that carried on. This is the same kind of
  discontinuous (program_name, portfolio) series an ordinary machinery-of-
  government rename already produces elsewhere in this database -- the
  identity model already expects a rename to look like an old series
  ending and a new one starting, so this isn't a new kind of gap, just
  this document's own version of one.

Confirmed by inspecting the workbook directly: Program 3.2 2020-21
estimated_actual is $14,080,517k in "Table 2.3.1 old" vs $0 in the current
"Table 2.3.1"; Program 1.1 (Outcome 1) is $18,884,914k under the old name
"Family Tax Benefit" vs $0 under the current name "Family Assistance".
"""
from parse_pbs import norm

TARGET_FILE = "2021-22 Budget/2021-22 PBS Social Services/2021-22 PBS DSS.xlsx"

OLD_SHEET_SUFFIX = " old"
RESCUE_FISCAL_YEAR = "2020-21"
RESCUE_ESTIMATE_TYPE = "estimated_actual"


def apply(records):
    old_by_key = {}
    for r in records:
        sheet = r.get("sheet_name") or ""
        if not sheet.endswith(OLD_SHEET_SUFFIX):
            continue
        if r["fiscal_year"] != RESCUE_FISCAL_YEAR or r["estimate_type"] != RESCUE_ESTIMATE_TYPE:
            continue
        if r["amount_thousands"] == 0:
            continue
        old_by_key[(r["outcome_number"], r["program_number"])] = r

    recovered_keys = set()
    out = []
    for r in records:
        sheet = r.get("sheet_name") or ""
        if sheet.endswith(OLD_SHEET_SUFFIX):
            continue  # superseded by the current-structure sheets; never insert these as-is
        if (r["fiscal_year"] == RESCUE_FISCAL_YEAR
                and r["estimate_type"] == RESCUE_ESTIMATE_TYPE
                and r["amount_thousands"] == 0):
            key = (r["outcome_number"], r["program_number"])
            old_rec = old_by_key.get(key)
            if old_rec is not None and norm(old_rec["program_name"]).lower() == norm(r["program_name"]).lower():
                r = dict(r)
                r["amount_thousands"] = old_rec["amount_thousands"]
                r["sheet_name"] = f'{sheet} (2020-21 recovered from "{old_rec["sheet_name"]}")'
                recovered_keys.add(key)
        out.append(r)

    # Programs renamed as part of the restructure: keep their 2020-21 figure
    # as its own one-year record under the pre-restructure name, since it
    # can't be folded into the current-structure row without misattributing
    # it to a program it doesn't actually describe (see module docstring).
    for key, old_rec in old_by_key.items():
        if key in recovered_keys:
            continue
        rec = dict(old_rec)
        rec["program_number"] = f'{rec["program_number"]} (pre-restructure)'
        rec["sheet_name"] = f'{rec["sheet_name"]} (superseded from 2021-22 onward)'
        out.append(rec)

    return out
