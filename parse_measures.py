"""
Parse Budget/MYEFO "Table 1.2: Measures" sheets from PBS/PAES Excel workbooks.

A measures table lists policy Measures, each with a $ impact profile broken
down by agency (and, within an agency, by program). One measure can touch
several agencies across several portfolios -- each agency's own PBS/PAES
file independently lists its own slice of the measure with matching figures,
so parsing every file and taking the union reconstructs the full picture
without needing to reason across files.

Row hierarchy within one sheet:
    Measure name                              (free text, no $ values)
      Agency name                             (free text, no $ values)
        Administered payments   1.8   v1 v2 v3 v4 v5   (category + optional
        Departmental payments   2     v1 v2 v3 v4 v5    program number + $)
                                 2.3   v1 v2 v3 v4 v5   (continuation: same
                                                          category, more rows)
      Agency name                             (another agency, same measure)
        ...
    Total payments                    v1 v2 v3 v4 v5   (closes the block --
                                                          may span >1 agency)
    Related receipts                                    (optional: a second,
      Agency name                     v1 v2 v3 v4 v5     receipt-side block
                                                          for the same measure)

Some editions (e.g. Treasury) instead split the whole sheet into top-level
"Receipt measures" / "Payment measures" sections, each measure ending in a
bare "Total" line. Both are handled below.
"""
import re
import math
import pandas as pd

from parse_pbs import load_sheets, norm, fiscal_year, to_amount  # noqa: F401
from build_db import clean_agency


# ---- row-type regexes ---------------------------------------------------------

HEADER_LABEL_RE = re.compile(r"^outcome\s*/?\s*program$|^program$", re.I)
PART_RE = re.compile(r"^part\s+\d", re.I)
# A second "Part"-equivalent divider (seen e.g. Health 2025-26) introducing
# measures where this agency isn't the lead but has some involvement,
# reported since the last update -- own repeated mini-header row right
# after it, same shape as the sheet's own opening block. Without
# recognising it as a boundary the same way PART_RE is, it fell through
# and got captured as a measure name of its own -- and worse, the row
# right after it (the repeated "Outcome/Program" header, silently
# skipped by HEADER_LABEL_RE without resetting state) left the *real*
# next measure's name captured as if it were that bogus measure's own
# agency, corrupting everything under this divider.
OTHER_PORTFOLIO_MEASURES_RE = re.compile(r"^other\s+portfolio\s+measures\b", re.I)
TITLE_ROW_RE = re.compile(r"^table\s+1\.2\b", re.I)
# Broader than TITLE_ROW_RE -- used only to detect a mislabeled sheet (one
# literally named "1.2" whose own title row says otherwise, e.g. OSI
# 2021-22 Budget's "Table 1.2" sheet actually contains "Table 1.1: OSI
# resource statement...", a completely different table, because that
# agency had no Table 1.2 measures that edition and never removed/
# replaced the placeholder sheet). TITLE_ROW_RE alone can't catch this
# since it requires the title to already say "1.2".
ANY_TABLE_TITLE_RE = re.compile(r"^table\s+\d", re.I)
# Marks unused leftover template rows some agencies never deleted from
# their published sheet (an "Outcome 1..7 / Administered / Departmental"
# skeleton, values all zero) -- not real measure data. Confirmed by
# checking one instance (DSS 2025-26) has zero non-zero cells anywhere
# after this marker, so it's safe to stop parsing the sheet entirely here.
SPARE_SPACE_RE = re.compile(r"^\(spare space", re.I)
#  measure vs measures: most sheets use the plural ("Payment measures"),
# but at least AUSTRADE/WGEA 2025-26 use the singular ("Payment measure",
# "Total payment measure") for the exact same structural role -- without
# the "s?", these fell through to the free-text handler and got read as
# if they were real measure names (a section-header/grand-total row
# masquerading as a measure). The receipt/payment prefix is itself
# optional: a bare "Measures" divider (seen e.g. ATO 2024-25, under its
# own "Part 2: Other measures not previously reported in a portfolio
# statement" -- a mixed bag with no single direction) needs the same
# treatment; direction there is left alone (falls back to each category
# line's own wording) rather than guessed at. "Revenue"/"Expense" is a
# third wording alongside receipt/payment for the exact same two
# categories -- most 2017-18-through-roughly-2022-23 editions use it
# (the same terminology shift BP2's own parser handles, see
# parse_bp2.py's _canon_impact_word) -- widely affects the older
# editions once ingestion expanded past the original 3-edition
# validation set.
# A trailing footnote marker ("Expense measures (a)", DHS 2017-18) is
# also possible here, same as on a measure/category line elsewhere in
# this file -- optional, not required. "Receipt measures (if applicable)"
# (Home Affairs 2022-23 March Budget) is a second, wordier trailing
# qualifier -- the space inside its parens means it isn't a footnote
# marker (\([a-z]+\) has no room for one), so it needs its own
# alternative rather than folding into that same group.
SECTION_RE = re.compile(
    r"^(?:(receipts?|payments?|revenues?|expenses?|capital)\s+)?measures?"
    r"(\s*\(if applicable\)|\s*\([a-z]+\))*$", re.I)
# The receipt/payment word is sometimes dropped for a second "Part"'s
# closing total ("Total measures", not "Total payment measures") -- the
# direction is implied by context there rather than restated. A trailing
# qualifier is also sometimes appended ("...measures (including Capital)")
# -- matched as a prefix (no trailing "$"), not tied to the exact phrase.
# A leading section-label ("(A) Total 2022-23 Budget measures", "(A+B)
# Total payment measures", "(B) Total other measures" -- PM&C 2022-23
# March Budget, each closing a different one of that sheet's own labelled
# sub-sections) or a "Sub-Total" prefix (2023-24 MYEFO) is also possible;
# an edition name ("2022-23 Budget") sometimes sits between "Total" and
# "measures" in place of a receipt/payment word, same structural role.
# None of these matched the plain "^total" anchor and fell through as
# bogus measure names of their own.
GRAND_TOTAL_RE = re.compile(
    r"^(?:\([a-z](?:\+[a-z])*\)\s*|sub-?\s*)?"
    r"total\s+(?:(?:receipts?|payments?|revenues?|expenses?|capital)\s+)?"
    r"(?:\d{4}-\d{2}\s+(?:budget|myefo)\s+)?(?:other\s+)?measures?\b", re.I)
# A different flavour of grand total, keyed by Outcome rather than
# direction -- DVA's own sheets recur across nearly every edition:
# "Total Outcome 1"/"Total Outcome 2"/... closes that outcome's own
# block of measures, immediately followed by its own Administered/
# Departmental/Total breakdown rows, exactly like GRAND_TOTAL_RE's own
# structural role -- unrecognised, "Total Outcome N" itself fell
# through and got read as a measure name.
TOTAL_OUTCOME_RE = re.compile(r"^total\s+(?:outcome\s+\d+|all\s+outcomes|outcome\s+all)$", re.I)
# Social Services/SAus's own 2022-23 MYEFO closing lines -- "Net impact on
# appropriations for Outcome 1 (departmental)" (a sub-total closing just
# the Departmental section) and "Total net impact on appropriations for
# Outcome 1" (the sheet's own grand total right after it) -- neither
# matches GRAND_TOTAL_RE (no "measures" in either) or TOTAL_OUTCOME_RE (no
# bare "Total Outcome N"). Unrecognised, the first fell through as a
# free-text "agency" line carrying that outcome's full $ total inline --
# double-counting it into whatever measure was still open -- and the
# second then got misread as a new measure name of its own.
NET_IMPACT_TOTAL_RE = re.compile(
    r"^(total\s+)?net\s+impact\s+on\s+appropriations\s+for\s+outcome\s+\d+(\s*\([a-z]+\))?$", re.I)
# Same file's own "Other Variations" sub-header, right before the two
# NET_IMPACT_TOTAL_RE lines above -- a parameter-change/appropriations
# reconciliation ("(net increase)"/"(net decrease)"), not a real
# announced measure, structurally the same "footer breakdown before this
# section's own total" role GRAND_TOTAL_RE's own in_grand_breakdown
# skip-mode exists for. Unrecognised, "Other Variations" itself became a
# bogus measure name and "(net decrease)" -- the one with no matching
# "(net increase)" row directly above/below it here -- a second, entirely
# separate bogus measure of its own.
OTHER_VARIATIONS_RE = re.compile(r"^other\s+variations$", re.I)
# A bare "Total"/"Total payments"/"Total revenue" (a *measure's own*
# closing total, not a grand total) sometimes carries a trailing
# "impact" too ("Total revenue impact"/"Total expense impact", seen in
# some Agriculture-portfolio sheets) -- optional, not tied to the exact
# phrase.
TOTAL_RE = re.compile(r"^total(\s+(payments?|receipts?|revenues?|expenses?)(\s+impact)?)?$", re.I)
RELATED_RECEIPTS_RE = re.compile(r"^related\s+receipts$", re.I)
# A trailing footnote marker ("Departmental payment (a)", "Administered
# receipt (b)(c)") is common and must not break the match -- otherwise
# the row falls through to the free-text handler and gets misread as an
# agency name (silently discarding the real one that preceded it). "Equity"
# (an equity injection/capital-type payment, seen e.g. NDIS Q&S Commission)
# is a third category alongside Administered/Departmental. "Expenses"
# (NIAA 2024-25 PAES: "Administered expenses"/"Departmental expenses")
# is a third wording alongside payments/receipts for the exact same
# category-line role -- unrecognised, these fell through and got
# misread as a measure name (the very first row in the sheet) or an
# agency name (the direction-check below already treats "expense" as
# "payment" the same way BP2's own Revenue/Expense/Capital terminology
# does, via not containing "receipt"). "Revenue" is a fourth wording,
# alongside receipt/payment/expense, for this same role -- SECTION_RE/
# GRAND_TOTAL_RE/TOTAL_RE/_is_receipt_like() already all treat it as a
# receipt-direction synonym, but CATEGORY_RE itself never got the same
# treatment (confirmed via a full-dataset scan: 301 "Administered
# revenue"/"Departmental revenue" cells across 82 files). Unrecognised,
# each such line fell through to the free-text handler and got misread
# as a brand-new agency name -- silently discarding the real one, and
# (worse) leaving the *next* row's own category data wrongly still
# attributed to whatever measure was open before it, corrupting agency
# attribution across every revenue-measures section that uses this
# wording (mostly older, pre-~2019 editions).
CATEGORY_RE = re.compile(
    r"^(administered|departmental|equity)(\s+capital)?(\s+(payments?|receipts?|revenues?|expenses?))?(\s*\([a-z]+\))*$",
    re.I,
)


def _is_receipt_like(label):
    """Revenue ~ Receipt, Expense ~ Payment -- same terminology shift
    parse_bp2.py's own _canon_impact_word() handles for BP2. "Expense"
    already defaulted correctly (doesn't contain "receipt", so falls
    through to the "payment" default below) -- "revenue" needs an
    explicit check, since it *also* doesn't contain "receipt" and would
    otherwise default to "payment" too, backwards."""
    lower = label.lower()
    return "receipt" in lower or "revenue" in lower


BARE_PROGRAM_RE = re.compile(r"^\d+(?:\.\d+)?$")
FOOTNOTE_MARKER_RE = re.compile(r"^\([a-z]{1,3}\)$", re.I)
# A footnote's own explanation text, trailing at the end of a sheet
# ("(a) ACSQHC is not the lead entity for this measure. Full details for
# this measure are published under Table 1.2: Department of Health
# 2024-25 Budget Measures.", ACSQHC 2025-26 Budget) -- distinct from
# FOOTNOTE_MARKER_RE (a bare "(a)" sharing a row with a data value).
# Unhandled, this fell through and got captured as a measure name of its
# own, and a stray trailing row of bare numbers right after it (with no
# real label at all) got attached to it as if it were real agency $ data.
FOOTNOTE_EXPLANATION_RE = re.compile(r"^\([a-z]{1,3}\)\s+\S")
# A measure name ending in a bare, dangling dash: its own Excel row-wrap
# landed exactly on the "<title> - <subtitle>" separator, splitting the
# subtitle onto its own row -- which, for a single-agency sheet with no
# explicit "Agency Name" row of its own, is exactly the row position
# "expect_agency" is waiting to read next (seen e.g. TEQSA 2024-25
# MYEFO: "Australian Universities Accord -" / "further reforms (a)",
# category lines immediately following with no agency row at all). No
# real, complete measure title ends in a dangling dash, so this is a
# safe, mechanical continuation signal, not a guess.
TRAILING_DASH_RE = re.compile(r"[-–—‐‑]\s*$")
# A single free-text heading row ("Tax Integrity Package", Treasury
# 2017-18 Budget; "Australian Technology and Science Growth Plan", DIIS
# 2018-19 Budget) sometimes introduces a group of sibling sub-measures,
# each its own row, each titled with only a bare leading dash ("–
# combatting fraud in the precious metals industry") and no repeat of
# the heading text -- the heading is only ever implied by the source
# document's own indentation/bullet-list styling, not machine-readable
# from any single row alone. Confirmed via direct cell dumps to be a
# real, recurring convention (not free-form prose that happens to start
# with a hyphen): every dash-bulleted title observed is immediately
# preceded, with only category/total rows in between, by either such a
# heading or another dash-bulleted sibling of the same group -- never a
# genuinely unrelated row. Unrecognised, a bare-dash title fell through
# to the generic free-text handler and got misread as an agency name
# for whatever measure was still open (a real agency name is never
# itself dash-prefixed, so this is a safe, mechanical signal, the same
# kind of reasoning TRAILING_DASH_RE above already relies on).
#
# A second, single-cell variant (DVA 2017-18 Budget) combines the
# heading and its own first bullet into one title ("Guaranteeing
# Medicare:_x000D_\n- Medicare Benefits Schedule -_x000D_\n
# indexation(b)", after norm() collapsing to "Guaranteeing Medicare: -
# Medicare Benefits Schedule - indexation(b)"); only its LATER sibling
# bullets are bare-dash-only. HEADING_PREFIX_RE below captures just the
# "Heading:" portion in that case, so it isn't duplicated onto later
# siblings alongside the first bullet's own text.
LEADING_DASH_RE = re.compile(r"^[-–—‐‑]\s")
HEADING_PREFIX_RE = re.compile(r"^(.+?:)\s*[-–—‐‑]")


def _resolve_measure_title(label, pending_heading):
    """Returns (measure_title, updated_pending_heading) for a newly
    captured measure name -- see LEADING_DASH_RE's own docstring. A
    bare-dash title gets pending_heading prepended (left alone,
    unprefixed, if none is set -- the previous, less-good behaviour);
    pending_heading itself is deliberately NOT updated in that case,
    since more dash-prefixed siblings of the same group may still
    follow. A non-dash title always updates pending_heading instead,
    to whatever a later sibling (if any) should be prefixed with.
    """
    if LEADING_DASH_RE.match(label):
        title = f"{pending_heading} {label}" if pending_heading else label
        return title, pending_heading
    m = HEADING_PREFIX_RE.match(label)
    return label, (m.group(1) if m else label)
# A literal unfilled Excel template placeholder some agencies never
# replaced with their actual measure name (seen e.g. ACSQHC 2025-26 --
# "<Enter measure name here> (a)", a $0 row whose own footnote says the
# real measure is published under a different agency's own Table 1.2;
# also "<insert measure name here>", ARPANSA 2021-22 -- "enter" vs
# "insert" varies by agency/year) -- not a real measure name, filtered
# out in parse_workbook_measures() rather than recorded as one.
PLACEHOLDER_MEASURE_NAME_RE = re.compile(r"^<(?:enter|insert) measure name here>", re.I)
# A measure-name row's trailing program reference (Education-style): a
# single program number, a comma-separated list ("1.3,1.5"), or "All"
# (outcome/measure-wide, no specific program). Home Affairs' own 2022-23
# March Budget file uses "&" instead of "," as its separator ("2.2 & 3.3")
# -- unrecognised, that measure's own name-plus-hint row fell through and
# got misread as a category/agency line instead, corrupting every record
# under it (the real measure name never got set, so the section header
# above it -- itself a separate, still-unfixed case -- was used instead).
PROGRAM_LIST_RE = re.compile(r"^(all|\d+(?:\.\d+)?(?:\s*[,&]\s*\d+(?:\.\d+)?)*)$", re.I)


def find_measures_year_columns(rows, max_scan=8):
    """Return {col_index: fiscal_year} from the sheet's header block.

    Headers are either one row ("Outcome/ Program", "2024-25 $'000", ...) or
    split across two ("Program" / "$'000" one row, years on another). Scan
    the first few rows for one containing >=2 year-like tokens.
    """
    for r in rows[:max_scan]:
        found = {}
        for j, cell in enumerate(r):
            fy = fiscal_year(cell)
            if fy:
                found[j] = fy
        if len(found) >= 2:
            return found
    return {}


def _is_blank_row(row):
    return all(c is None or norm(c) == "" for c in row)


def _numeric_like(v):
    """Return the raw int/float a cell holds, preserving decimals -- unlike
    to_amount() (which rounds, fine for $ amounts but corrupts a program
    number like "1.8" into 2) -- or None if it isn't numeric."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return None if (isinstance(v, float) and math.isnan(v)) else v
    s = norm(v).replace(",", "")
    s = s.replace("(", "-").replace(")", "")
    if re.fullmatch(r"-?\d+(\.\d+)?", s):
        return float(s) if "." in s else int(s)
    return None


def _trailing_cells(row, start=1):
    """Non-None cells from row[start:]; numeric ones normalized via
    _numeric_like (preserving decimals), non-numeric ones (a placeholder
    like '..' or 'nfp' standing in for a withheld/negligible figure) kept
    as their raw value. Used only for the measure-name program-hint check
    (_parse_program_hint) -- amount extraction uses _amounts_at instead,
    which reads exact column positions rather than counting cells (a
    blank cell mid-row -- a common way to write $0 -- would otherwise
    silently shift every later year's value back by one column).
    """
    out = []
    for c in row[start:]:
        if c is None:
            continue
        n = _numeric_like(c)
        out.append(n if n is not None else c)
    return out


def _amounts_at(row, col_order):
    """Raw cell values at the exact year-column positions (col_order is the
    sorted [(col_idx, fiscal_year), ...] from find_measures_year_columns).
    Position-exact, not count-based: preserves a blank cell as None (rather
    than compacting it away) so an interior $0-via-blank-cell can't shift
    every later year's value out of alignment.
    """
    return [row[c] if c < len(row) else None for c, _ in col_order]


def _program_num_before(row, idx, first_year_col):
    """A program number, when present, sits in the gap between the row's
    label (column `idx`) and the first year column -- verified empirically
    across ~3,000 rows in ~400 sheets to always be exactly one column back,
    never shifted further. Returns the formatted string, or None if that
    gap is empty (no program number stated on this line)."""
    for c in range(idx + 1, first_year_col):
        if c < len(row) and row[c] is not None:
            v = _numeric_like(row[c])
            return _format_program_num(v) if v is not None else norm(row[c])
    return None


def _parse_program_hint(cells):
    """cells: the trailing raw cells (via _trailing_cells) of a candidate
    measure-name row. Returns a list of program numbers if it looks like
    a program reference -- Education's format states the program(s) right
    on the measure-name row ('2.4', '1.3,1.5', or 'All' for measure-wide)
    instead of per category line -- or None if it doesn't match that shape
    (so the caller falls back to treating the row as a plain label).
    """
    if len(cells) != 1:
        return None
    v = cells[0]
    text = _format_program_num(v) if isinstance(v, (int, float)) else norm(v)
    if not PROGRAM_LIST_RE.match(text):
        return None
    if text.lower() == "all":
        return []
    return [p.strip() for p in re.split(r"[,&]", text)]


def _format_program_num(v):
    """1.8 -> '1.8', 2.0 or 2 -> '2' (departmental rows store a bare int)."""
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    return ("%g" % v) if isinstance(v, float) else str(v)


# Multiple trailing markers are sometimes comma-separated ("...Package
# (b),(d)", Agriculture 2019-20) rather than just space-separated
# ("...Package (b)(d)") -- without the optional ",", only the last
# marker matched (the comma isn't whitespace, so it broke the
# "+"-repeated chain reading backward from the end), leaving a dangling
# "(b)," on the stripped name.
MEASURE_NAME_FOOTNOTE_RE = re.compile(r"(\s*,?\s*\([a-z]{1,3}\))+$")


def _canon_measure_name(name):
    """Each agency touched by a measure independently retypes its own copy
    of the measure's name into its own PBS/PAES file -- and those copies
    routinely differ in ways that norm()'s whitespace collapsing doesn't
    touch:
    - a plain hyphen vs an en/em/non-breaking dash, or a straight vs curly
      apostrophe (e.g. Education's own file spells this measure with "-",
      while ABS's and Services Australia's spell it with "–" -- otherwise
      identical);
    - a trailing footnote marker -- "Implementation of Aged Care Reforms
      (a)" in one agency's file, "...(k)" in another's, referencing that
      *file's own* footnote list (so the letter itself carries no
      meaning shared across files) -- affecting the *majority* of
      measures (420 of 777 measure names end with one, confirmed by
      direct count), stripped here rather than left as-is.
    Without this, those agencies silently end up as a different
    `measure_name` and never get joined together, even though it's the
    same real measure and the whole point of this table is seeing every
    agency's slice of one measure in one place."""
    if name is None:
        return name
    # "─" (U+2500 box-drawing light horizontal) -- see parse_bp2.py's
    # identical line for where this was found; folded in here too so
    # both sides of the join apply the exact same rule. "‐" (U+2010
    # HYPHEN, distinct from U+002D hyphen-minus) found in at least one
    # 2025-26 Budget agency file's own typing of "Savings from External
    # Labour ‐ further extension".
    name = name.replace("–", "-").replace("—", "-").replace("‑", "-").replace("─", "-").replace("‐", "-")
    name = name.replace("’", "'").replace("‘", "'")
    # U+200B zero-width space -- invisible, but a distinct character as
    # far as string equality is concerned, so a trailing one ("Workplace
    # Relations​", 2024-25 Budget) silently fragments an otherwise
    # identical name.
    name = name.replace("​", "")
    name = MEASURE_NAME_FOOTNOTE_RE.sub("", name).strip()
    return name


def _first_nonempty(row):
    """(index, normalized text) of the first non-None cell, or (None, '').

    Continuation lines (another program number under the same category,
    e.g. a bare "2.3" row following "Administered payments  1.8  ...")
    leave column 0 blank and start their content in column 1 instead --
    so the row's "label" isn't reliably in a fixed column position.
    """
    for i, c in enumerate(row):
        if c is not None:
            return i, norm(c)
    return None, ""


def _is_data_like(v):
    """True if a cell could plausibly hold a data value (a number, or a
    placeholder like 'nfp'/'..' standing in for a withheld one) rather than
    a stray footnote marker ("(a)") sharing a row with a text label."""
    if v is None:
        return False
    if _numeric_like(v) is not None:
        return True
    s = norm(v)
    if not s or FOOTNOTE_MARKER_RE.match(s):
        return False
    return True


def _uses_sparse_totals(rows):
    """True if this sheet's own convention mostly omits the per-measure
    "Total" row the state machine otherwise relies on to know a measure's
    category lines have ended -- confirmed via direct inspection to be a
    genuine, consistent, multi-year filing choice for a handful of
    agencies (Treasury's own department file 2017-18 through 2022-23,
    both its main "TSY" sheet and, to a lesser extent, the ATO's;
    Services Australia's own MYEFO filings), not noise: a "Total" only
    ever appears for a *multi-line* measure (several category rows, or a
    multi-bullet package under one heading -- see LEADING_DASH_RE/
    HEADING_PREFIX_RE), apparently because a Total that would just repeat
    a single category line's own already-visible figure was treated as
    redundant and dropped. Detected by counting how many of the sheet's
    own "Administered .../Departmental ..." category-line rows have a
    corresponding "Total" row at all (real ratios observed: 0.03-0.16 for
    the affected agencies, 0.27-1.5 for everything else checked) -- a
    >=5-category-row floor avoids noise on tiny sheets where one missing
    Total swings the ratio wildly.

    Read by parse_measures_sheet to relax its own agency-vs-new-measure
    disambiguation (see its own use of this flag): normally a free-text
    row with no data, appearing after >=1 category line with no Total in
    between, is read as a *second agency for the same still-open
    measure* (a real, documented pattern -- see this module's own
    docstring). On a sparse-totals sheet that same shape is read as a
    brand new measure instead, since every concretely-traced instance in
    these agencies' own files confirmed the free-text row was always a
    genuinely new, unrelated measure, never a second agency (each of
    these files is that one agency's own single-agency filing, which
    structurally has little reason to name a second agency at all).
    """
    category = 0
    total = 0
    for row in rows:
        _, label = _first_nonempty(row)
        if not label:
            continue
        lower = label.lower()
        if lower.startswith("total"):
            total += 1
        elif CATEGORY_RE.match(label):
            category += 1
    if category < 5:
        return False
    return (total / category) < 0.2


def parse_measures_sheet(rows, col_order, default_agency=None, sparse_totals=False):
    """Parse one measures sheet's rows into a flat list of impact records.

    col_order: sorted [(col_idx, fiscal_year), ...] from
    find_measures_year_columns -- amounts are read from these exact
    columns (see _amounts_at), not by counting cells, so a blank
    mid-row cell (a common way to write $0) can't misalign anything.

    Each record:
        {measure_name, agency, direction ('payment'|'receipt'),
         programs: [str, ...], is_departmental, amounts: [v1..vN]}
    `amounts` is positional, aligned to col_order (caller maps position ->
    fiscal_year). `programs` is a list because one $ line can cover
    several programs at once (see program_hint below) -- callers wanting a
    flat program list per measure/agency should just union it across that
    group's records.

    default_agency: some editions omit the agency-name row when a measure
    only affects the filing agency itself (see clean_agency in build_db.py,
    the filename-derived agency for the workbook this sheet came from) --
    used to seed `agency` at the start of each new measure so category
    lines without a preceding agency row still attribute correctly.

    sparse_totals: see _uses_sparse_totals's own docstring -- relaxes the
    agency-vs-new-measure disambiguation for the handful of agencies
    whose own filings need it.
    """
    n_years = len(col_order)
    first_year_col = col_order[0][0]
    records = []
    state = "expect_measure"  # or "expect_agency"
    measure = None
    agency = default_agency
    direction = None
    section_direction = None
    # Some editions (Education) state the affected program(s) once, right
    # on the measure-name row ("Some Measure", "1.3,1.5"), rather than per
    # category line -- applies to every category line under this measure
    # that doesn't carry its own program number.
    program_hint = []
    # See LEADING_DASH_RE/HEADING_PREFIX_RE's own docstring -- tracks the
    # most recent non-dash-prefixed title, for prefixing onto whichever
    # bare-dash sibling bullet(s) follow it, if any.
    pending_heading = None
    # True right after a "Total receipt/payment measures" grand total,
    # until the next section/part/title marker or a program-hint-shaped
    # measure name. The grand total's own breakdown rows that follow it
    # ("Administered", "Administered receipt", "Total ", ...) are
    # indistinguishable in *shape* from a real category/total line for the
    # last measure -- only this position-in-the-sheet context tells them
    # apart, so a static regex alone (GRAND_TOTAL_BREAKDOWN_RE, which only
    # catches the bare "Administered"/"Departmental" form) isn't enough:
    # some editions' breakdown uses "Administered receipt" etc, which
    # matches CATEGORY_RE and would otherwise get recorded as spurious
    # extra data for whatever measure came before the grand total.
    in_grand_breakdown = False

    for row in rows:
        if _is_blank_row(row):
            continue
        idx, label = _first_nonempty(row)
        if idx is None or not label:
            continue

        if label in ("+", "-") and all(v is None or norm(v) in ("+", "-") for v in row):
            # A row of border/divider characters -- DSS's own 2025-26
            # Budget file repeats a "Program"/year header block mid-sheet
            # several times (a print-layout leftover), each preceded by
            # one of these all-"+" (occasionally "+"/"-" mixed) rows.
            # Every cell here reads as "data-like" to _is_data_like
            # (non-empty, non-numeric, not a footnote marker), so
            # unskipped this got folded in as a bogus zero-dollar
            # continuation row of whatever measure was still open, and
            # worse, its own label ("+") sometimes got misread as a new
            # measure name outright. Skipped outright, before it can
            # touch measure/agency state at all.
            continue

        if label in ("Adm", "Dep"):
            # Home Affairs' own 2022-23 March Budget PBS file alone
            # (confirmed via a full scan of every edition -- every other
            # file's category rows lead with the full "Administered
            # receipt"/"Departmental receipt" text itself) prefixes each
            # category row with this short marker in its own leading
            # column, ahead of the real category text a couple of columns
            # later. _first_nonempty picks up the marker instead -- and
            # since "Adm"/"Dep" alone don't match CATEGORY_RE, the row
            # fell through and got misread as a new one-word measure name
            # ("Dep") each time. Re-point idx/label at the real category
            # cell, wherever it sits in the row.
            for c in range(idx + 1, len(row)):
                if row[c] is not None and CATEGORY_RE.match(norm(row[c])):
                    idx, label = c, norm(row[c])
                    break

        if in_grand_breakdown:
            is_boundary = (
                SECTION_RE.match(label) or PART_RE.match(label) or TITLE_ROW_RE.match(label)
                or OTHER_PORTFOLIO_MEASURES_RE.match(label) or TOTAL_OUTCOME_RE.match(label)
                or NET_IMPACT_TOTAL_RE.match(label) or OTHER_VARIATIONS_RE.match(label)
                or _parse_program_hint(_trailing_cells(row, start=idx + 1)) is not None)
            if is_boundary:
                in_grand_breakdown = False
                # fall through -- let the normal handling below process this row
            else:
                continue

        if HEADER_LABEL_RE.match(label) or re.fullmatch(r"\$'?000", label, re.I):
            continue
        if FOOTNOTE_EXPLANATION_RE.match(label):
            # Skip only this row rather than stopping the sheet outright
            # (SPARE_SPACE_RE's approach): a per-measure footnote placed
            # right after that measure's own block, not collected at the
            # end, is plausible in some file's layout, and breaking here
            # would silently drop every measure after it.
            continue
        if SPARE_SPACE_RE.match(label):
            break
        if re.match(r"^cross-outcome$", label, re.I):
            # Marks that the measure right after it will be split across
            # more than one of the filing agency's own outcomes (each via
            # its own "Outcome N" sub-header -- see m_outcome_sub below),
            # not a measure or agency name itself.
            state = "expect_measure"
            continue
        if TITLE_ROW_RE.match(label):
            # The sheet's own title ("Table 1.2: <Agency> ... measures ...").
            # Budget editions always follow it with a "Part 1: ..." line
            # (which resets state below), but MYEFO editions often don't --
            # without this, the title itself gets read as the first measure
            # name and corrupts state for the real first measure.
            state = "expect_measure"
            continue
        if PART_RE.match(label):
            state = "expect_measure"
            continue
        if OTHER_PORTFOLIO_MEASURES_RE.match(label):
            state = "expect_measure"
            continue
        if SECTION_RE.match(label):
            if _is_receipt_like(label):
                section_direction = "receipt"
            elif "payment" in label.lower() or "expense" in label.lower():
                section_direction = "payment"
            else:
                # A bare "Measures" divider with no direction word --
                # reset rather than carry over a stale direction from
                # whatever section came before (same reasoning as
                # GRAND_TOTAL_RE's own reset below); category lines fall
                # back to their own payment/receipt wording instead.
                section_direction = None
            state = "expect_measure"
            continue
        if GRAND_TOTAL_RE.match(label):
            state = "expect_measure"
            in_grand_breakdown = True
            # This closes the section section_direction was set for. Some
            # sheets (e.g. ACMA) follow a "Receipt measures" section with a
            # second, payment-side measure list that has no "Payment
            # measures" header of its own to overwrite it with -- leaving
            # section_direction stuck on "receipt" would otherwise
            # mis-tag every category line there (which must instead fall
            # back to its own payment/receipt wording, same as when no
            # section was ever declared).
            section_direction = None
            continue
        if TOTAL_OUTCOME_RE.match(label):
            state = "expect_measure"
            in_grand_breakdown = True
            continue
        if NET_IMPACT_TOTAL_RE.match(label):
            state = "expect_measure"
            in_grand_breakdown = True
            continue
        if OTHER_VARIATIONS_RE.match(label):
            state = "expect_measure"
            in_grand_breakdown = True
            continue
        if RELATED_RECEIPTS_RE.match(label):
            direction = "receipt"
            # Usually just a marker with its own row (an agency+amounts row
            # follows), but sometimes carries the figures directly on the
            # same row (no separate agency line at all) -- e.g. a bare
            # "Related receipts  nfp  nfp  nfp  nfp" row. Handle both: if
            # there's data here, record it now (against the default/filing
            # agency, since none is named) and expect the next measure;
            # otherwise fall through to expecting an agency line as usual.
            amount_cells = _amounts_at(row, col_order)
            if any(_is_data_like(v) for v in amount_cells):
                records.append({
                    "measure_name": measure, "agency": agency or default_agency,
                    "direction": direction, "programs": list(program_hint),
                    "is_departmental": False,
                    "amounts": [to_amount(v) for v in amount_cells],
                })
                state = "expect_measure"
            else:
                state = "expect_agency"
            continue
        if TOTAL_RE.match(label):
            state = "expect_measure"
            continue

        m_outcome_sub = re.match(r"^outcome\s+\d+$", label, re.I)
        if m_outcome_sub:
            # A per-Outcome sub-block within one measure's own "Cross-
            # Outcome" breakdown (DSS's convention for a measure whose
            # impact spans more than one of its own outcomes) -- not a
            # new measure and not a different agency, just this same
            # measure's own filing agency, split for bookkeeping. Checked
            # before the unconditional program-hint check below: a bare
            # "Outcome 3" carrying its own trailing program number (e.g.
            # "Outcome 3  3.1") would otherwise match that check's shape
            # and get misread as a brand-new measure named "Outcome 3".
            agency = default_agency
            prog_num_raw = _program_num_before(row, idx, first_year_col)
            if prog_num_raw is not None:
                program_hint = [prog_num_raw]
            state = "expect_agency"
            continue

        # Checked before the generic category/agency handling below, and
        # regardless of state: a measure-name row can carry its program
        # reference(s) inline ("Some Measure", "1.3,1.5") instead of a
        # plain label. This shape is unambiguous -- an agency name is never
        # followed by exactly one cell matching a program-number-list
        # pattern -- so it's trusted even in "expect_agency" state, e.g. a
        # spurious sub-agency-group header line ("Services Australia" with
        # no data of its own) can precede the real first measure without a
        # "Total"/section boundary in between to reset state first.
        hint = _parse_program_hint(_trailing_cells(row, start=idx + 1))
        if hint is not None:
            measure, pending_heading = _resolve_measure_title(label, pending_heading)
            agency = default_agency
            direction = None
            program_hint = hint
            state = "expect_agency"
            continue

        m = CATEGORY_RE.match(label)
        if m:
            # A category line's OWN wording ("...receipt" vs "...payment")
            # decides direction -- except when it sits inside a top-level
            # Receipt/Payment section (section_direction set), which wins:
            # some editions label a line "Departmental receipt" for a cost
            # that still rolls into that section's own "Total payment
            # measures" grand total (e.g. IPEA) -- the section is the
            # source of truth for which grand total a line contributes to.
            direction = section_direction or ("receipt" if _is_receipt_like(label) else "payment")
            is_departmental = "departmental" in label.lower()
            prog_num_raw = _program_num_before(row, idx, first_year_col)
            amount_cells = _amounts_at(row, col_order)
            if is_departmental and prog_num_raw is not None and "." not in prog_num_raw:
                # A bare outcome integer (e.g. "1") on a Departmental line
                # means that outcome's departmental allocation as a whole --
                # synthesize the "X.0" convention. If it already has a
                # decimal (e.g. SIA's "1.1"), it's a real program number,
                # not an outcome-wide one -- use it as-is, no synthesis.
                programs = [f"{prog_num_raw}.0"]
            elif prog_num_raw is not None:
                programs = [prog_num_raw]
            else:
                # No program number of its own -- fall back to the
                # measure-level hint (Education-style), if any.
                programs = list(program_hint)
            records.append({
                "measure_name": measure, "agency": agency, "direction": direction,
                "programs": programs,
                "is_departmental": is_departmental,
                # to_amount(v) is None for a blank cell (a common way to
                # write $0) or a placeholder like '..'/'nfp' (withheld) --
                # the position is kept (not dropped) so it still lines up
                # with the right fiscal year; parse_workbook_measures drops
                # the None entries once that alignment has been used.
                "amounts": [to_amount(v) for v in amount_cells],
            })
            state = "expect_agency"
            continue

        if BARE_PROGRAM_RE.match(label):
            # Continuation line: same category/direction as the last one,
            # just another program number under the same agency.
            if agency is not None:
                amount_cells = _amounts_at(row, col_order)
                records.append({
                    "measure_name": measure, "agency": agency, "direction": direction,
                    "programs": [label], "is_departmental": False,
                    "amounts": [to_amount(v) for v in amount_cells],
                })
            state = "expect_agency"
            continue

        # Free-text row: either a measure name, an agency name, or an
        # agency name with inline amounts (the "Related receipts" pattern).
        # Checked via _is_data_like at the exact year-column positions, not
        # "any trailing cell": a figure can be "nfp" (not for publication)
        # instead of a number, which must still count as "this row has
        # data" (otherwise the state machine never closes this agency
        # block and misreads the *next measure's name* as another agency
        # of this one) -- but a footnote marker ("(a)") sitting in some
        # other column (outside the year-column range) correctly never
        # reaches this check at all now.
        amount_cells = _amounts_at(row, col_order)
        if any(_is_data_like(v) for v in amount_cells):
            if state == "expect_measure":
                # The measure name itself carries the $ data inline, with
                # no separate agency/category line at all under it (e.g.
                # ILSC's "Savings from External Labour - extension (a)
                # 1.1  0 -8 -9 -9 -68"; SBS's "Supporting News and Media
                # Diversity" with placeholder '-' cells before its own
                # "Departmental payments" line). We're not mid-agency-block
                # here (no measure is currently open), so this row's label
                # is the measure name, not a bogus "agency" named after it.
                measure, pending_heading = _resolve_measure_title(label, pending_heading)
                agency = default_agency
                prog_num_raw = _program_num_before(row, idx, first_year_col)
                records.append({
                    "measure_name": measure, "agency": agency,
                    "direction": section_direction or "payment",
                    "programs": [prog_num_raw] if prog_num_raw else [],
                    "is_departmental": False,
                    "amounts": [to_amount(v) for v in amount_cells],
                })
                state = "expect_measure"
                continue
            agency = label
            records.append({
                "measure_name": measure, "agency": agency,
                "direction": direction or section_direction or "payment",
                "programs": list(program_hint), "is_departmental": False,
                "amounts": [to_amount(v) for v in amount_cells],
            })
            # An inline agency+amounts row is self-contained and is never
            # followed by category lines for the same agency -- and in
            # practice the next free-text row is almost always the next
            # measure, not another agency, so default there rather than to
            # "expect_agency" (which would misread this measure's own name
            # as this agency's continuation -- seen once concretely: after
            # a "Related receipts" line, the very next row was the next
            # measure's name with no "Total" in between).
            state = "expect_measure"
            continue

        if state == "expect_measure":
            measure, pending_heading = _resolve_measure_title(label, pending_heading)
            agency = default_agency
            direction = None
            program_hint = []
            state = "expect_agency"
        elif measure is not None and TRAILING_DASH_RE.search(measure):
            measure = f"{measure} {label}"
            # state stays "expect_agency": still the same measure, still
            # waiting for a real agency or category line -- agency stays
            # default_agency, unaffected.
        elif LEADING_DASH_RE.match(label):
            # A sibling bullet under an earlier heading/first-bullet --
            # see LEADING_DASH_RE's own docstring. Reads as a brand new
            # sub-measure, never a real agency name (a genuine agency
            # name is never itself dash-prefixed) -- checked before the
            # generic `agency = label` fallback below so it isn't
            # swallowed as bogus agency text for whatever measure was
            # still open.
            measure, pending_heading = _resolve_measure_title(label, pending_heading)
            agency = default_agency
            direction = None
            program_hint = []
            # state stays "expect_agency": this new measure's own
            # category/data line is expected next, same as the
            # state == "expect_measure" branch above.
        elif sparse_totals:
            # See _uses_sparse_totals's own docstring -- on a sheet
            # detected to mostly omit per-measure "Total" rows, this
            # same free-text-after-a-category-line shape is read as a
            # new measure rather than a second agency for the still-open
            # one, matching what every concretely-traced instance in
            # these specific agencies' own files actually was.
            measure, pending_heading = _resolve_measure_title(label, pending_heading)
            agency = default_agency
            direction = None
            program_hint = []
        else:
            agency = label
            # state stays "expect_agency": category lines expected next

    return records


SHEET_NAME_1_2_RE = re.compile(r"(?<![\d.])1\.2(?![\d.])")


def find_measures_sheet_names(sheets):
    """Sheet names that look like a Table 1.2 measures table.

    Matched by the sheet's own NAME containing "1.2" as a distinct token
    (bounded so "2.1.2" or "T_2.1.2.1" -- expense-table sheets -- don't
    collide), not by scanning cell content for the word "measures": that
    was tried first and produced a false positive (an outcome description
    on a *different*, "2.3.1"-named expense sheet happened to contain the
    prose "...infrastructure and measures that stimulate economic
    growth...", pulling billions of dollars of unrelated expense-table
    data into the measures tables). Every genuine measures sheet observed
    across ~400 sheets names itself with "1.2" somewhere ("1.2 Measures",
    "T_1.2", "1.2 - Measures Table", "1.2 Measure", "Table 1.2", ...),
    even the ones with no in-sheet title row at all.

    Excludes three known naming collisions, all real "1.2" matches that
    aren't a measures table at all:
    - A stale leftover tab (seen once: an "old -Measure Table 2020-21
      PAES" tab left behind inside a 2022-23 MYEFO workbook, alongside
      that year's real sheet) -- same "1.2" naming, different, unreliable
      layout.
    - A "Program 1.2"-style sheet -- some editions' old per-program
      expense-detail convention (one sheet per program, see parse_pbs.py's
      own PROGRAM_HDR_RE handling of this era) reuses the program's own
      number as the sheet name, which for program 1.2 collides with the
      "1.2" measures-table token despite being expense data, not measures.
    - A "Table 1.2 Footnotes <Agency>"-style sheet (seen in DAFF): prose
      explaining individual measures in more detail, laid out as a table
      with its own $'000/fiscal-year-looking header that would otherwise
      parse as spurious, duplicate measure data.
    - A "2.1.2 Prog 1.2"-style sheet (seen in DVA 2024-25): a per-program
      expense-detail sheet (Table 2.1.2's own per-program breakdown, one
      tab per program) whose OWN program number happens to be "1.2" --
      the abbreviated "Prog" and its not-at-the-start position meant the
      original `^program\b` exclusion (written for names that start with
      the literal word "Program") never caught it, so its category/total
      labels ("Annual Administered Expenses:", "Special Appropriations:",
      "Total program expenses") got read as measure names.
    - A per-program expense-detail sheet, named any of several ways
      across different editions/agencies for what is, underneath, the
      exact same "Table 2.1.x" convention (see parse_pbs.py's own
      handling of this era) -- all seen recurring, mostly from DVA and
      CCEEW/DCCEEW, across nearly every edition once ingestion expanded
      past the original 3-edition validation set: "2.1.2 Prog 1.2",
      "Program Expenses 1.2", "Table 2.1.2 1.2", "T2.1.2-1.1", "Prog Exp
      1.2". Every one of these produces the same symptom -- a row-by-row
      cell dump of program-level $ line items (e.g. "Loss of earnings",
      "Recreation transport allowance", "Annual Administered Expenses:",
      "Special Appropriations:") misread as measure names -- so rather
      than chasing each new abbreviation individually, excluded by two
      general signals: the literal "2.1.<n>" substring (this table's own
      real numbering, wherever it appears in the sheet name), or "prog"
      followed reasonably closely by "exp" (covers every "Prog"/
      "Program" x "Exp"/"Expenses" abbreviation combination seen).
    """
    out = []
    for name in sheets:
        if re.search(r"\bold\b", name, re.I):
            continue
        if re.search(r"\bprog(?:ram)?\.?\s*\d", name, re.I):
            continue
        if re.search(r"\bprog(?:ram)?\s*exp", name, re.I):
            continue
        if re.search(r"2\.1\.\d+", name):
            continue
        if re.search(r"\bfootnotes?\b", name, re.I):
            continue
        if re.search(r"\blevies\b", name, re.I):
            continue
        if SHEET_NAME_1_2_RE.search(name):
            out.append(name)
    return out


def parse_workbook_measures(path):
    """Return a flat list of long-format measure impact records for a workbook.

    Each record: {measure_name, agency, direction, programs (list),
    is_departmental, fiscal_year, amount_thousands, sheet_name}
    """
    sheets = load_sheets(path)
    sheet_names = find_measures_sheet_names(sheets)
    out = []
    for name in sheet_names:
        rows = sheets[name]

        # A sheet literally named "1.2" isn't always a measures table --
        # e.g. ANAO/APSC 2024-25 PAES both use their own Table 1.2 for
        # "Additional Estimates and other variations to outcomes" (an
        # appropriations reconciliation: Movement of Funds, Changes in
        # Parameters, net increase/decrease sub-totals -- no policy
        # measures at all). find_measures_sheet_names() deliberately
        # doesn't check content (see its own docstring -- that caused a
        # different false positive once), but the sheet's own title row
        # is a much narrower, safe signal: every genuine measures sheet
        # observed says "measures" in its own title ("...Budget
        # Measures", "...MYEFO Measures", ...); a "Table 1.2:" title
        # that doesn't is reliably something else, and every row under
        # it (reconciliation category labels like "Movement of Funds",
        # "(net decrease)", "Total net impact on appropriations...")
        # would otherwise get misread as measure names.
        title_row = next(
            (label for _, label in (_first_nonempty(r) for r in rows[:5]) if ANY_TABLE_TITLE_RE.match(label)),
            None,
        )
        if title_row is not None and "measure" not in title_row.lower():
            continue
        # 2022-23 MYEFO uses a *different* bespoke reconciliation
        # template edition-wide (confirmed: ~90% of that edition's own
        # files) -- "Table 1.2: Additional estimates and variations to
        # outcomes from measures and other variations". This one *does*
        # say "measure" (so the check above alone doesn't catch it), but
        # its actual layout is "Outcome N" -> Administered/Departmental
        # -> Annual appropriations/Movement of Funds/Other Variations/
        # Special appropriations sub-groups, each containing named $
        # line items -- a genuinely different structure from the
        # standard Measure name -> Agency name -> category lines this
        # parser is built around (not just different section-header
        # wording, like the Revenue/Expense/Capital cases elsewhere in
        # this file). Skipped rather than force-fit, the same call made
        # for Defence's own bespoke format (see KNOWN_GAPS.md #1) --
        # attempting it would risk misattributing real $ figures to the
        # wrong measure/agency, worse than a clean gap.
        if title_row is not None and "variations to outcomes" in title_row.lower():
            continue

        year_cols = find_measures_year_columns(rows)
        if len(year_cols) < 2:
            continue
        col_order = sorted(year_cols.items())  # [(col_idx, fy), ...] left-to-right
        # Always the filename-derived name -- the same derivation
        # program_expenses.agency uses in build_db.py -- rather than the
        # sheet's own title text. The two were tried independently at
        # first (title text preferred, filename as fallback) and it
        # seemed reasonable since both name the same real agency, but in
        # practice the title tends to be the FULL formal name ("Department
        # of Education") while the filename is the short form ("Education")
        # matching program_expenses -- diverging for ~44% of files and
        # silently breaking any join between the two tables on `agency`.
        # Using the same derivation as program_expenses keeps them joinable.
        default_agency = clean_agency(path)
        sparse_totals = _uses_sparse_totals(rows)
        for rec in parse_measures_sheet(
            rows, col_order, default_agency=default_agency, sparse_totals=sparse_totals
        ):
            if not rec["measure_name"] or not rec["agency"]:
                continue
            if PLACEHOLDER_MEASURE_NAME_RE.match(rec["measure_name"]):
                continue
            if len(rec["amounts"]) != len(col_order):
                continue
            for (_, fy), amt in zip(col_order, rec["amounts"]):
                if amt is None:
                    # A placeholder ('..'/'nfp') stood in for this year's
                    # figure -- no number to record, but its position was
                    # still needed above to keep the other years aligned.
                    continue
                out.append({
                    "measure_name": _canon_measure_name(rec["measure_name"]),
                    "agency": rec["agency"],
                    "direction": rec["direction"] or "payment",
                    "programs": rec["programs"],
                    "is_departmental": rec["is_departmental"],
                    "fiscal_year": fy,
                    "amount_thousands": amt,
                    "sheet_name": name,
                })
    return out


if __name__ == "__main__":
    import sys
    for rec in parse_workbook_measures(sys.argv[1]):
        print(rec)
