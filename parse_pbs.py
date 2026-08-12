"""
Parse Australian Portfolio Budget Statement (PBS) Excel workbooks into
program-level expense records.

Gold source: the "Budgeted expenses for Outcome N" tables inside each agency
workbook. Each lists every `Program N.M: <name>` and a `Total ... for program
N.M` row. Columns are: prior-year Estimated actual, Budget year, and forward
estimates -- identified by their header text, not by position.

The parser keys off *table content* rather than sheet names, because sheet
names, row labels, and column offsets all drift between budget years.
"""
import os
import re
import math
import pandas as pd


# ---- low-level loading -------------------------------------------------------

def load_sheets(path):
    """Return {sheet_name: [ [cell, ...], ... ]} for any Excel format.

    Empty cells come back as None. Uses the right engine per extension so xlsx,
    xls and xlsb all flow through one interface.
    """
    ext = os.path.splitext(path)[1].lower()
    engine = {".xlsx": "openpyxl", ".xlsm": "openpyxl",
              ".xls": "xlrd", ".xlsb": "pyxlsb"}.get(ext, "openpyxl")
    dfs = pd.read_excel(path, sheet_name=None, header=None, engine=engine,
                        dtype=object)
    sheets = {}
    for name, df in dfs.items():
        rows = []
        for row in df.itertuples(index=False, name=None):
            rows.append([None if (v is None or (isinstance(v, float) and math.isnan(v)))
                         else v for v in row])
        sheets[name] = rows
    return sheets


# ---- text helpers ------------------------------------------------------------

def norm(s):
    """Collapse all whitespace (incl. newlines, soft hyphens, nbsp) to spaces."""
    if s is None:
        return ""
    s = str(s).replace("\xad", " ").replace("\xa0", " ").replace("_x000D_", " ")
    return re.sub(r"\s+", " ", s).strip()


OUTCOME_TITLE_RE = re.compile(
    r"budgeted expenses(?:\s+and\s+resources)?\s+for\s+outcome\s*(\d+)", re.I)
OUTCOME_DESC_RE = re.compile(r"^outcome\s*(\d+)\s*[:\-]\s*(.+)", re.I)
# Sheet-name fallback when the title text itself doesn't say "Budgeted
# expenses for Outcome N" (e.g. a sheet literally, and *only*, named
# '2.1.1'). Must fullmatch -- other eras reuse the same "Table 2.1.1" name
# for a single-*program* detail sheet, which would collide otherwise.
SHEET_NAME_OUTCOME_RE = re.compile(r"2\.(\d+)\.1")
# Program number: "1.1" normally, but some agencies (e.g. CSIRO) use a bare
# "1" with no sub-number.
PROG_NUM = r"(\d+(?:\.\w+)?)"
# "Program 1.1: Name"  OR  "Program expenses 1.1 Name"  OR  "Program1.1: Name"
# OR "Program 1.1 – Name" (en/em dash separator) -- without the dashes in
# the separator class, a lone space before the required separator gets
# consumed as if IT were the separator (since \s is also in the class),
# leaving the dash itself attached to the start of the captured name.
PROGRAM_HDR_RE = re.compile(
    rf"^program(?:\s+expenses)?\s*{PROG_NUM}\s*[:\-–—\s]\s*(.+)$", re.I)
# "Total expenses for program 1.1" / "Total for Program 1.1" / "Total expenses Program 1.1"
PROGRAM_TOTAL_RE = re.compile(
    rf"^total\s+(?:expenses\s+)?(?:for\s+)?program\s+{PROG_NUM}\b", re.I)
# "Total Program expenses" (no program number -- falls back to current program)
PROGRAM_TOTAL_NO_NUM_RE = re.compile(r"^total\s+program\s+expenses\b", re.I)
# Used only as a fallback for single-program outcomes that have no explicit
# per-program total row (their one program's total equals the outcome total).
OUTCOME_TOTAL_RE = re.compile(r"^total\s+expenses\s+for\s+outcome\s+(\d+)\b", re.I)
# Marks the start of an outcome-wide reconciliation/summary section. Rows
# after this are aggregates or unrelated sub-tables (e.g. "Movement of
# administered funds"), not per-program expense rows -- text that merely
# resembles a program header here must not be treated as one.
OUTCOME_SUMMARY_MARKER_RE = re.compile(r"^outcome\s*\d*\s*totals?\s+by\b", re.I)
YEAR_RE = re.compile(r"(20\d{2})\D+(\d{2})")


def fiscal_year(text):
    """Extract 'YYYY-YY' from a header cell like '2024-25 Estimated actual'."""
    m = YEAR_RE.search(norm(text))
    return f"{m.group(1)}-{m.group(2)}" if m else None


def clean_program_name(name):
    name = norm(name)
    # drop trailing footnote markers like " (a)", " (a)(b)"
    name = re.sub(r"(\s*\([a-z]\))+\s*$", "", name)
    return name.strip()


def col_types(header_row):
    """Map column index -> (fiscal_year, estimate_type) from a header row."""
    out = {}
    for j, cell in enumerate(header_row):
        t = norm(cell).lower()
        if not t or len(t) > 60:
            # A genuine header cell is a short label ("2023-24 Budget
            # $'000"). Longer text is a merged title/outcome-description
            # blob picked up by the header-block scan -- and since titles
            # always contain "Budgeted expenses", it would otherwise be
            # misread as a real "budget" column, occasionally paired with a
            # bogus year if the description prose happens to mention one.
            continue
        fy = fiscal_year(cell)
        if "estimated actual" in t or "revised budget" in t or re.search(r"\bactual\b", t):
            etype = "estimated_actual"
        elif "budget" in t:
            etype = "budget"
        elif "forward" in t or "estimate" in t:
            etype = "forward_estimate"
        else:
            continue
        if fy:
            out[j] = (fy, etype)
    return out


def normalize_fiscal_years(cmap):
    """Recompute each column's fiscal year as a positional offset from the
    budget-year column.

    Source documents occasionally typo a column's year (e.g. a header
    literally reading "2023-23" instead of "2023-24"). The budget-year
    column is reliably correct, and PBS tables always present columns in a
    fixed left-to-right order -- prior-year actual, budget year, then
    forward estimates -- immediately adjacent with no gaps. So the correct
    year for every column can be derived purely from its position relative
    to the budget column, regardless of what its own header cell says.
    """
    budget_cols = [(j, fy) for j, (fy, etype) in cmap.items() if etype == "budget"]
    if not budget_cols:
        return cmap
    j_budget, fy_budget = budget_cols[0]
    start_year = int(fy_budget.split("-")[0])

    def fy_str(y):
        return f"{y}-{(y + 1) % 100:02d}"

    return {j: (fy_str(start_year + (j - j_budget)), etype)
            for j, (_, etype) in cmap.items()}


def find_column_map(rows, max_window=5):
    """Locate the header block. Returns (actual_row_index, {col_index: (fiscal_year, estimate_type)}).

    Some years split the header across multiple rows -- e.g. row1 has the
    fiscal years, row2 has 'Estimated'/'Budget'/'Forward', row3 has
    'actual'/'estimate'. We scan for the first window of up to `max_window`
    rows whose vertically-merged text yields a recognisable column map,
    stopping a window early once it hits a program header/total row or a row
    with actual numeric data (i.e. the data section has started).

    The returned index is the specific row within the block that carries the
    'actual'/'estimated' text (not just the window's start row) -- callers
    use it to tell "header precedes this program row" from "this program
    title happens to sit a couple of rows above the real header".
    """
    for i in range(len(rows)):
        block = []
        for k in range(max_window):
            if i + k >= len(rows):
                break
            r = rows[i + k]
            if k > 0:
                texts = [norm(c) for c in r if c is not None]
                if any(PROGRAM_HDR_RE.match(t) or PROGRAM_TOTAL_RE.match(t)
                       or PROGRAM_TOTAL_NO_NUM_RE.match(t) for t in texts):
                    break
                if any(isinstance(c, (int, float)) for c in r):
                    break
            block.append(r)
        ncols = max((len(r) for r in block), default=0)
        merged = []
        for j in range(ncols):
            parts = [str(r[j]) for r in block if j < len(r) and r[j] is not None]
            merged.append(" ".join(parts))
        cand = col_types(merged)
        n_actual = sum(1 for _, t in cand.values() if t == "estimated_actual")
        # Exactly one column should ever be "estimated actual". More than
        # one means this window is ambiguous (e.g. a stray "Revised Budget"
        # column collides with a genuine "Estimated actual" one) -- keep
        # scanning rather than risk mislabeling a budget-year column.
        if n_actual == 1:
            actual_idx = i
            for offset, r in enumerate(block):
                row_text = " ".join(str(c) for c in r if c is not None).lower()
                if "actual" in row_text or "estimated" in row_text:
                    actual_idx = i + offset
            return actual_idx, normalize_fiscal_years(cand)
    return None, {}


def to_amount(v):
    """Coerce a cell to an integer $'000 value, or None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return None if (isinstance(v, float) and math.isnan(v)) else int(round(v))
    s = norm(v).replace(",", "")
    s = s.replace("(", "-").replace(")", "")  # accounting negatives
    if re.fullmatch(r"-?\d+(\.\d+)?", s):
        return int(round(float(s)))
    return None


# ---- sheet parsing -----------------------------------------------------------

def extract_outcome_names(sheets):
    """Scan every sheet in a workbook for short 'Outcome N: Name' lines and
    return {outcome_number: name}.

    Some agencies' own "Budgeted expenses for Outcome N" table never states
    an outcome description at all (Health is the clearest example), but a
    short "Outcome N: <name>" line reliably appears elsewhere in the same
    workbook -- typically the Table 1.1 resource statement (its exact sheet
    name varies by edition: "1.1 Resource Statement", "Table 1.1 Resources
    (PGPA)", "1.1 Health Resources", ...). Scanning by content rather than
    sheet name sidesteps that drift. The length cap keeps this to genuinely
    short names ("Ageing and Aged Care") rather than picking up the long
    prose descriptions OUTCOME_DESC_RE already handles on the primary
    sheet, and the missing-colon check (e.g. DITRDC's "Outcome 1 (b)", a
    footnote marker with no real name) means agencies without an actual
    name here simply don't contribute one -- this is purely additive.
    """
    names = {}
    for rows in sheets.values():
        for r in rows:
            for c in r:
                nc = norm(c)
                if len(nc) > 80:
                    continue
                m = OUTCOME_DESC_RE.match(nc)
                if m:
                    names.setdefault(int(m.group(1)), m.group(2).strip())
    return names


def find_workbook_column_map(sheets):
    """Return the column map (see find_column_map) shared by this workbook's
    outcome sheets, for sheets whose own header row is missing entirely.

    Every sheet in one PBS workbook edition reports the exact same 5-year
    window in the exact same column positions -- so if e.g. a workbook has
    "Table 2.1.1", "Table 2.2.1" and "Table 2.3.1" and the last one is
    missing its header row (a real content omission seen in DVA's 2025-26
    workbook: the other two outcome sheets have it, that one jumps straight
    from the outcome description to the first program with no header row
    at all), the column layout can be safely borrowed from a sibling sheet
    that does have one, rather than losing that sheet's data entirely.
    """
    for rows in sheets.values():
        _, cmap = find_column_map(rows)
        if cmap:
            return cmap
    return {}


def parse_outcome_sheet(rows, sheet_name=None, outcome_names=None, fallback_cmap=None):
    """Yield program records from one 'Budgeted expenses for Outcome N' sheet.

    Returns (outcome_number, outcome_description, records) or None if this is
    not an outcome-expense sheet. Each record:
        {program_number, program_name, fiscal_year, estimate_type, amount}
    """
    # 1. Confirm it's an outcome-expense sheet and get the outcome number.
    header_idx, cmap = find_column_map(rows)
    if not cmap and fallback_cmap:
        # This sheet's own header row is missing entirely -- borrow the
        # layout from a sibling sheet (see find_workbook_column_map).
        # header_idx stays None: it's only used to gate the outcome-number
        # fallbacks below, which shouldn't fire off a borrowed header that
        # doesn't actually appear in this sheet.
        cmap = fallback_cmap
    title_outcome_no = None
    for r in rows[:6]:
        for c in r:
            m = OUTCOME_TITLE_RE.search(norm(c))
            if m:
                title_outcome_no = int(m.group(1))
                break
        if title_outcome_no is not None:
            break
    # The outcome description line ("Outcome N: <text>") independently
    # names the outcome number too -- collected here (not gated on matching
    # title_outcome_no yet) so it can cross-check the title below.
    desc_outcome_no, outcome_desc_text = None, None
    for r in rows[:8]:
        for c in r:
            m = OUTCOME_DESC_RE.match(norm(c))
            if m:
                desc_outcome_no, outcome_desc_text = int(m.group(1)), m.group(2).strip()
                break
        if desc_outcome_no is not None:
            break
    first_program_idx, first_program_num = None, None
    for idx, r in enumerate(rows):
        hm = next((PROGRAM_HDR_RE.match(norm(c)) for c in r if c is not None
                    and PROGRAM_HDR_RE.match(norm(c))), None)
        if hm:
            first_program_idx, first_program_num = idx, hm.group(1)
            break
    prog_outcome_no = None
    if first_program_num:
        outer = first_program_num.split(".")[0]
        if outer.isdigit():
            prog_outcome_no = int(outer)
    # Both fallbacks below apply only when the header block precedes the
    # first program row -- other eras reuse the same sheet-name pattern
    # (e.g. "Table 2.1.1") for a single-*program* detail sheet where the
    # program header is the very first row, which would otherwise collide.
    header_precedes_program = (
        first_program_idx is not None and header_idx is not None
        and header_idx <= first_program_idx)

    outcome_no = title_outcome_no
    if (outcome_no is not None and desc_outcome_no is not None
            and prog_outcome_no is not None and desc_outcome_no == prog_outcome_no
            and desc_outcome_no != outcome_no):
        # Two independent content signals (the outcome description and the
        # program numbering) agree with each other but not with the title --
        # e.g. a workbook where every outcome sheet's title was copy-pasted
        # without updating "Outcome 1". Trust the two that agree.
        outcome_no = desc_outcome_no
    if outcome_no is None and sheet_name and header_precedes_program:
        # Some agencies (e.g. DVA, AIHW, Services Australia) name the sheet
        # '2.1.1' / '2.1.1 Prog Exp' / 'Table 2.1.1 NCCE' with no title text
        # saying "Budgeted expenses for Outcome N" anywhere in it.
        m = SHEET_NAME_OUTCOME_RE.search(sheet_name.strip())
        if m:
            outcome_no = int(m.group(1))
    if outcome_no is None and header_precedes_program and prog_outcome_no is not None:
        # Last resort: a program's own number always encodes its outcome
        # ("Program 1.1" belongs to Outcome 1), used by agencies whose title
        # just says "Budgeted Expenses for <Agency>" with no outcome number
        # or usable sheet name at all (e.g. ADHA, NHFB).
        outcome_no = prog_outcome_no
    if outcome_no is None:
        return None

    # 2. Optional outcome description. Falls back to a short name pulled
    # from elsewhere in the workbook (e.g. Table 1.1's resource statement)
    # when this sheet doesn't state one at all -- see extract_outcome_names.
    outcome_desc = outcome_desc_text if desc_outcome_no == outcome_no else None
    if not outcome_desc and outcome_names:
        outcome_desc = outcome_names.get(outcome_no)

    # 4. Walk rows: track current program, emit on total rows.
    records = []
    programs = {}   # number -> name (from headers)
    explicit_pnums = set()
    outcome_total_row = None
    cur = None
    past_summary_marker = False
    for r in rows:
        for c in r:
            nc = norm(c)
            if OUTCOME_SUMMARY_MARKER_RE.match(nc):
                past_summary_marker = True
                break
            om = OUTCOME_TOTAL_RE.match(nc)
            if om and int(om.group(1)) == outcome_no:
                outcome_total_row = r
                break
            if past_summary_marker:
                continue
            hm = PROGRAM_HDR_RE.match(nc)
            if hm:
                cur = hm.group(1)
                programs.setdefault(cur, clean_program_name(hm.group(2)))
                break
            tm = PROGRAM_TOTAL_RE.match(nc)
            tm_nonum = None if tm else (PROGRAM_TOTAL_NO_NUM_RE.match(nc) if cur is not None else None)
            if (tm or tm_nonum) and cmap:
                # Trust the tracked current program over the number embedded
                # in the total-row text: source spreadsheets occasionally
                # mislabel a total row (e.g. a stray "for program 2.5" total
                # row appearing right after a "Program 2.6:" header).
                pnum = cur if cur is not None else (tm.group(1) if tm else None)
                if pnum is None:
                    break
                explicit_pnums.add(pnum)
                name = programs.get(pnum, programs.get(cur, None))
                for j, (fy, etype) in cmap.items():
                    amt = to_amount(r[j]) if j < len(r) else None
                    if amt is None:
                        continue
                    records.append({
                        "program_number": pnum,
                        "program_name": name,
                        "fiscal_year": fy,
                        "estimate_type": etype,
                        "amount_thousands": amt,
                    })
                break

    # 5. Fallback: a single-program outcome with no explicit per-program
    # total row -- that one program's total equals the outcome total.
    if (len(programs) == 1 and cmap and outcome_total_row is not None
            and not (set(programs) & explicit_pnums)):
        pnum = next(iter(programs))
        for j, (fy, etype) in cmap.items():
            amt = to_amount(outcome_total_row[j]) if j < len(outcome_total_row) else None
            if amt is None:
                continue
            records.append({
                "program_number": pnum,
                "program_name": programs[pnum],
                "fiscal_year": fy,
                "estimate_type": etype,
                "amount_thousands": amt,
            })

    return outcome_no, outcome_desc, records


def parse_workbook(path):
    """Return list of records for every outcome/program in a workbook."""
    sheets = load_sheets(path)
    outcome_names = extract_outcome_names(sheets)
    fallback_cmap = find_workbook_column_map(sheets)
    out = []
    for name, rows in sheets.items():
        parsed = parse_outcome_sheet(rows, sheet_name=name, outcome_names=outcome_names,
                                      fallback_cmap=fallback_cmap)
        if not parsed:
            continue
        outcome_no, outcome_desc, recs = parsed
        for rec in recs:
            rec = dict(rec)
            rec["outcome_number"] = outcome_no
            rec["outcome_description"] = outcome_desc
            rec["sheet_name"] = name
            out.append(rec)
    return out


if __name__ == "__main__":
    import sys
    for rec in parse_workbook(sys.argv[1]):
        print(rec)
