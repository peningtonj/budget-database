# Known gaps and limitations

This documents the coverage limitations of `programs.db` as built by
`build_db.py` / `parse_pbs.py`, so they aren't rediscovered from scratch
later. Sections 1-5 cover the `program_expenses` table (Portfolio →
Agency → Outcome → Program → FY). Section 6 covers `measure_impacts` /
`measure_programs` (built by `parse_measures.py` / `build_measures_db.py`
from Table 1.2).

## Coverage summary

- 1,486 of 1,506 agency workbooks parsed (98.7%) across all 9 Budget
  editions from 2017-18 to 2025-26 (including both the 2022-23 March and
  October budgets).
- 18,564 rows total; 3,724 of them `estimated_actual`.
- `estimated_actual` fiscal years span 2016-17 through 2024-25.
- ~650 distinct program names across 772 agency-name variants (agency
  names are derived from filenames and are not normalised across years —
  see "Agency name drift" below).

## 1. Defence's core operational programs are not captured

Defence does not publish the standard "Budgeted expenses for Outcome N"
table that every other agency uses. Instead its PBS spreads each
program's costs across a bespoke set of sheets:

- `Table 12: Total Budgeted Resources Available for Outcome N` — lists
  programs but without a clean per-program total row (just "Revenues from
  other sources" / "Expenditure funded by appropriations" line items).
- `Table 13, 14, 15, ...: Cost Summary for Program N.M` — one block per
  program, often multiple programs per physical sheet, with a 3-row split
  header (`2024-25` / `Estimated Actual` / `$'000` each on their own row)
  and the total on a line like `Program 1.1 - <name> Total funded
  expenditure [a]`.

This structure is unique to Defence and is **not parsed** by the current
tooling. What *is* captured for Defence's main department are its smaller
administered programs (superannuation, housing assistance, etc.) that
happen to live on a separate, standard-format sheet in the same workbook.

**Impact:** Defence portfolio figures in the database substantially
understate Defence's true program-level spend (its core Outcome 1-3
operational/capability programs, the bulk of its ~$50-80B annual budget,
are missing).

**If this is wanted:** a dedicated Defence-specific parser branch would
be needed to walk the `Table 12` / `Table 13+` structure. Not yet built.

## 2. Files with no data at all (19 of 1,506)

- **ASD, ASA** (intelligence agencies within the Defence portfolio) —
  missing in most years. Appear to use a Defence-style bespoke layout, or
  lack a program-level breakdown entirely.
- **Department of the Senate, Parliamentary Budget Office, Department of
  the House of Representatives (some years)** — small parliamentary
  entities that in several years appear to genuinely have no
  program-level expense table.
- **Federal Court of Australia (2018-19)**, a couple of other one-off
  small-agency files — not individually root-caused; likely further
  one-off formatting idiosyncrasies not worth chasing given their size.
- **`2022-23 October Budget/.../NQWIA PBS Tables.xlsx`** — the file
  itself is corrupt (not a valid `.xlsx`/zip archive), can't be opened at
  all.

Run `python3 build_db.py` and check the `ZERO-RECORD FILES` /
`ERROR FILES` sections of its output for the current exact list — it's
easy to reduce it further, but this list reflects the state as of this
writing.

## 3. Source-document data-quality issues (handled defensively)

Several source spreadsheets themselves contain inconsistencies. Where a
conflict is genuinely ambiguous, `build_db.py` keeps the **first**
encountered value for a given (source file, outcome, program, fiscal
year, estimate type) key and silently drops conflicting duplicates,
logging them under `CONFLICTING DUPLICATES` in its output (289 instances
in the current build). Known causes:

- **Mislabeled total rows** — a program's total row is occasionally
  labelled with the wrong adjacent program number (e.g. a total row
  captioned "Total expenses for program 2.5" that actually sits under the
  "Program 2.6" section and contains that program's figures). The parser
  trusts the most recently seen "Program N.M:" header over the number
  embedded in the total-row text, which resolves most of these — but
  some patterns (e.g. a program name reused as a label for an unrelated
  line item nested inside a *different* program's block) are not
  reliably distinguishable and fall through to the conflict-log/first-wins
  safety net instead.
- **Copy-pasted outcome titles** — some workbooks (e.g. DVA 2025-26) have
  every outcome sheet's title literally say "Budgeted expenses for
  Outcome 1" regardless of the sheet's real outcome, because the sheet
  was duplicated without updating the title. The parser cross-checks the
  title against the outcome-description line and the program numbering;
  when those two independent signals agree with each other but not with
  the title, it trusts them over the title.
- **Missing header rows** — at least one sheet (DVA 2025-26, Outcome 3 —
  War Graves / Commemorative Activities) has no column-header row at all
  above its program data. There is no reliable way to recover fiscal-year
  / estimate-type labels for such a sheet, so it is correctly skipped
  (yields zero rows) rather than guessing.

## 4. Agency name drift

`agency` is derived from the workbook's filename with light cleanup
(stripping "PBS", "cleaned", year numbers, etc.), not from a canonical
registry. The same real-world agency can appear under multiple `agency`
values across years (e.g. "DISER", "DISR", "Industry Science Energy and
Resources", "Department of Industry Science Energy and Resources").
`portfolio` has the same issue, driven by inconsistent folder naming
across years (85 distinct `portfolio` values were observed, versus the
~17-20 real portfolios). Neither field should be trusted as a stable join
key across years — use `program_name` for that instead (per the original
brief), and expect agency/portfolio to need their own normalisation pass
if you want portfolio/agency-level rollups.

## 5. `estimate_type` coverage nuance

Only `estimated_actual`, `budget`, and `forward_estimate` are extracted.
Some workbooks' `Total expenses for Outcome N` line (used only as a
single-program fallback) or reconciliation sub-tables (e.g. "Movement of
administered funds between years") are deliberately *not* captured as
program rows, to avoid misattributing outcome-level or reconciliation
figures to a specific program.

## 6. Measures (`measure_impacts` / `measure_programs`)

Parses "Table 1.2: Measures" from Budget and MYEFO documents: one row
per policy measure per agency per direction (payment/receipt) per
fiscal year, plus a separate table of which programs each
measure/agency/direction group touches. `program_number` is stored raw
(not resolved to a name) — join to `program_expenses` on
(budget_year, agency, program_number) to resolve it for that specific
year, since numbers get reused for different programs over time. A bare
outcome integer on a "Departmental" line (no decimal, meaning "this
outcome's departmental allocation as a whole") is stored as program
number `"X.0"` with `is_departmental=1`.

**Scope: all 19 Budget/MYEFO editions, 2017-18 through 2025-26** (both the
March and October 2022-23 Budgets) — expanded from an initial 3-edition
validation set (2025-26 Budget, 2024-25 Budget, 2024-25 MYEFO) once the
structural gaps found across the older/wider file variety (section 9
below) were fixed. 1,895 files scanned, 1,193 produced data (701 legitimately
have zero measures that round — a normal agency/edition combination, not
a gap), 1 error (the same corrupt `NQWIA PBS Tables.xlsx` from section 2).
31,800 `measure_impacts` rows / 11,597 `measure_programs` rows / 3,141
distinct measure names / 3,491 distinct measure ids (ids are keyed by
`(measure_name, edition)`, so a real measure recurring across editions
gets one id per edition it appears in; 0 id collisions needed a salt
bump). Cross-checked against `measure_text` (BP2, section 7): of the 22
BP2 editions, 17 now have PBS coverage too (up from 3) — the 5 that don't
(2014-15 MYEFO through 2016-17 MYEFO) simply predate the PBS data
available in `data/pbs/`, not a parsing gap. Of the 3,237 BP2 measures
whose edition PBS covers, 2,264 (69.9%) resolve to a matching PBS
`measure_impacts` entry — consistent with the same-magnitude match rate
already found and explained for the original 3-edition pass (most misses
are genuine scope differences: a measure the filing agency reported with
zero net $ impact, or under a different lead agency's own Table 1.2 with
a differently-typed name — not a bug, see section 6's own naming
discussion and section 8).

Validation method: for every sheet, the parser's own per-measure sums
were cross-checked against that sheet's own printed grand-total row
(extracted independently, not via the parser), across all 364 files. 32
sheets still show a residual mismatch after all fixes below; every one
was individually traced to either (a) a gap in the *validation script's*
own grand-total-row detection (e.g. a sheet split into two "Parts" where
the second's closing total uses a bare "Total measures", not "Total
payment/receipt measures", which the check-script's search pattern
doesn't recognize as a starting marker at all), or (b) a genuine defect
in the *source spreadsheet itself* (a stale/uncorrected total row that
doesn't even reconcile with that same sheet's own visible category
lines; a duplicated grand-total block; a total that bakes in a
`nfp`/undisclosed figure never itemized anywhere) — never a parser bug.
In every case the parser's sum was verified by hand to equal the sum of
the sheet's own real, visible line items.

**Agency naming, and why it now matches `program_expenses`:** the first
version of this parser derived a measure's default agency from the
sheet's own title text ("Table 1.2: Department of Education ...") when a
measure had no explicit agency-name row of its own. That was a real
inconsistency, not inherent source drift: `build_db.py` has always
derived `program_expenses.agency` purely from the *filename* via
`clean_agency()`, so the two tables used different agency strings for
the exact same real agency in the exact same file family (e.g.
"Department of Education" vs "Education"), breaking a same-agency join
between them for ~44% of measures files. Fixed by dropping the
title-based extraction entirely and using `clean_agency(path)` — the
same function, same precedence as `build_db.py` — as the sole default,
so a measure relying on this fallback now gets a byte-identical agency
string to `program_expenses` for the same file. Residual noise from
`clean_agency`'s own filename-cleanup edge cases (e.g. a filename with
underscores instead of spaces defeats its `\b(pb|pbs)\b` word-boundary
strip, e.g. `Infra_PBS_14_NFSA.xlsx` → "Infra PBS 14 NFSA"; a typo'd
"Satement" instead of "Statement" isn't in the strip list) is
pre-existing and was already present in `program_expenses` — not
something this change introduced — so the two tables stay consistent
with each other even where individually ugly.

What this fix does *not* cover: many measures sheets DO have an explicit
agency-name row of their own (the normal convention — Measure name →
Agency name → category lines), and that row's text is whatever the
filing agency actually typed, which tends to be the full formal name
("Department of Health and Aged Care", "Services Australia") even
though the filename convention is a short form ("Health", "SA"). This
used to be handled with a view-layer word-boundary fuzzy match (plus a
handful of hardcoded aliases for cases like "SAUS" that the heuristic
couldn't bridge at all) — a guess, and an unnecessary one: there is only
one government-published PBS document set per year, so a formal name and
a filename-derived short form for the same real agency in the same year
are both individually recoverable from that agency's *own* workbook (the
formal name from its Table 1.1 "resource statement" title, which every
agency publishes; the short form from `clean_agency()` on that same
file's path), making the correspondence between them exact, not fuzzy.
`build_agency_aliases.py` now derives this pairing directly (1,421
aliases across all 9 editions, in `agency_aliases`), and
`backend/measures/views.py`'s `_resolve_agencies()` does a plain exact
lookup against it — no substring/word-boundary heuristics or hand-
maintained alias dicts anywhere in the app now. ~41 agency strings per
edition still don't resolve: most are the "garbled fragment" parsing
artifacts described below (not real agencies at all), plus a few small
Health/Social-Services-portfolio agencies whose Table 1.1 extraction
missed for a reason not yet root-caused (worth a follow-up look, but
distinct from the agency-naming problem this section is about).

Cross-YEAR agency drift (the same real agency renamed/restructured over
time, e.g. "DET" → "ESE" → "Education") is a different, harder problem in
general, but `portfolio_profile()` and `agency_outcome_profile()`
(the portfolio/agency drilldown charts) now bridge it deterministically
for one specific case: chaining every `agency_aliases` short-name variant
that shares a formal name *within the same portfolio* (`_agency_history()`
in `views.py`), since each (formal name, short name) pairing was already
individually verified from that year's own Table 1.1 title — not a fresh
guess, just following the same exact-correspondence chain one step
further. This isn't full identity resolution: a portfolio reshuffle (the
same agency moving to a *different* portfolio in some year) still isn't
bridged, and an agency with no Table 1.1-derived alias for a given year
still shows a gap there. Confirmed the fix concretely on the Social
Services portfolio — Services Australia's own chart went from 1 usable
year (its 2024-25 spelling, "SAUS", only matching that one year) to 8;
DSS/AIFS/NDIA went from 1 and 8 years to 12 each. `program_profile()`
still deliberately doesn't attempt this at the *program* level (it joins
on `program_name` alone) — whether/when to extend bridging further (e.g.
across a portfolio reshuffle, or down to program identity) is still a
call to make later, not something to guess at now.

Known remaining data-quality issues in the parsed output, not chased
further given diminishing returns for a 3-edition validation pass:

- **A few agency values are still garbled fragments of nearby text**,
  not real agency names — e.g. `Enabling Western Sydney International
  Airport(h)(i)` (from DAFF's "Capital measures" sub-section, whose own
  sub-header collides with the parser's program-hint detection), `Media
  and Communications(a)`, `Southeast Asia Treaty
  Organisation pharmacy supplement`, `Supporting House and Senate
  Transformation`, `further reforms (a)`, `Ceasing the Modernising
  Business Registers Program`, and a handful of others. These come from
  single-file, single-row edge cases in the free-text agency/measure
  disambiguation and were judged too narrow to be worth a general rule
  each. The dollar amounts themselves are still counted correctly (that
  was checked); only the `agency` (and sometimes `measure_name`) label on
  those specific rows is wrong.
- **Undisclosed ("not for publication") figures are never counted** —
  when a measure's dollar impact is withheld in the source (`nfp`), the
  measure/program/agency/direction is still recorded, just with no
  dollar amount for that year (rather than a fabricated 0). A sheet's
  own printed grand total can therefore be a little higher in magnitude
  than the sum of everything `measure_impacts` shows for it.
- **A measure appearing under both Receipt and Payment headings** in the
  same file (documented explicitly by at least one source, DAFF, in its
  own footnotes) is stored as two separate rows differing only in
  `direction` — by design, per the brief ("track whether it appears in
  the Receipt or Payment part").

## 7. Measure text (`measure_text` / `measure_text_component` /
   `measure_text_related` / `measure_text_headline_financial`)

Parses Budget Paper No. 2's (and MYEFO Appendix A's) own narrative
measure write-ups (`parse_bp2.py` / `build_bp2_db.py`) — intro/end
prose, bulleted components with financial-impact/timeframe text,
related-measure references, portfolio, and BP2's own headline financial
table. A different source document from `measure_impacts` above (which
comes from each agency's own Table 1.2), looked up by
`(measure_name, edition)` alone.

**Coverage: 2015-16 through 2025-26, both Budget and MYEFO (21
editions, 4,366 measure write-ups)** — every Budget edition from
2015-16 on and every MYEFO edition in that span that exists (there is
no 2022-23 MYEFO; an October Budget stood in for it that year). Only
`2014-15 Budget.pdf` is excluded — see below.

**Pre-2020-21 editions use an older three-part "Part 1: Revenue
Measures" / "Part 2: Expense Measures" / "Part 3: Capital Measures"
structure** (vs. the current two-part Receipt/Payment split, with no
separate Capital section). Originally left unsupported entirely (BP2's
`SECTION_MARKER_RE` only recognised the current wording, so these 12
older documents each returned zero records) — now handled:
`SECTION_MARKER_RE`/`FIN_TABLE_HEADER_RE`/`TOTAL_ROW_RE` all accept
both eras' wording, normalized to one canonical receipt/payment/capital
value via `_canon_impact_word()`. Confirmed via direct font/structure
inspection that the underlying document architecture is otherwise
identical across both eras (portfolio heading @ 12.96pt bold, measure
heading @ 9.96pt bold, Book Antiqua body prose, bullets in
TimesNewRoman, "Related X ($m)" sub-tables) — only the terminology,
and the font *family* actually used, differ. Pre-2020-21 editions mix
Arial/Helvetica and "TimesNewRomanPSMT"/"Times New Roman" (no subset
prefix) font naming depending on which tool generated that year's PDF,
and "Italic" vs Helvetica's own "Oblique" for italics — all handled via
`_is_plain_sans()`/`_is_bullet_word()`/`_is_italic()` in `parse_bp2.py`
rather than a single hardcoded font-family string.

A MYEFO's own measures live entirely within its "Appendix A"; earlier
editions' subsequent appendices (B: Supplementary Expenses Table, C:
Federal Relations, D/E: historical/tax data — cash-flow statements,
Statement-of-Risks contingent-liability disclosures, ...) share similar
bold heading styling and, unguarded, got picked up as phantom measures
("Claims against the Department of Defence" from a Statement of Risks,
literal table captions like "Table B3: ... cash flow statement", ...).
`NEXT_APPENDIX_RE` in `parse_bp2.py` stops extraction at the next
lettered appendix after A — gated on both boldness and size (≥13pt; the
same heading text also appears harmlessly, and much smaller, in the
table of contents and in body-prose citations like "Appendix C provides
further detail on...") so it only fires on the real divider.

**`2014-15 Budget.pdf` alone remains excluded.** Its "PART N: ... MEASURES"
running-header divider uses a more extreme drop-cap effect than
`normalize_portfolio_heading()` repairs: the first-letter-of-each-word
portion and the rest-of-word portion land far enough apart vertically to
extract as two separate lines with word-interleaved fragments (e.g.
`"P 1: R M"` / `"ART EVENUE EASURES"`, needing to be zipped back
together word-by-word: P+ART, R+EVENUE, M+EASURES), not just awkward
same-line spacing. `SECTION_MARKER_RE` never matches, so the file
returns zero records. 2014-15 MYEFO is unaffected (plain, unstyled
"Revenue/Expense/Capital Measures" text) and ingests normally (138
measures). Not fixed — a fragile word-interleaving heuristic for one
document's one-time divider wasn't judged worth it relative to the 20
other editions this pass already recovered.

**`2020-21 Budget.pdf` bundles a second, foreign document as a trailing
appendix**, not a genuine duplicate: page 189 onward is "Appendix A:
Policy decisions published in the July 2020 Economic and Fiscal
Update" — 2020-21 was the COVID-delayed Budget year, so a July 2020
mini-update preceded the actual October 2020 Budget, and this file
republishes that earlier update's measures as a reference appendix
after its own content ends. 5 titles from that appendix collide with
this document's own genuinely different measures on the same topic
(same title, different dollar figures and announcement dates — e.g.
"Ageing and Aged Care" is $2.0bn over four years from 2020-21 in the
main document vs $617.7m over six years from 2019-20 in the appendix).
Checked every other ingested file for the same "Appendix A: Policy
decisions published in ..." pattern — none have it; every MYEFO's own
"Appendix A: Policy decisions taken since the &lt;prior Budget&gt;" is
that document's own primary content (the phrasing distinguishes them:
"published in" vs "taken since"), not a foreign one, so this is a
one-off specific to 2020-21 Budget. `FOREIGN_APPENDIX_RE` in
`parse_bp2.py` detects the heading (gated on already being past the
front matter, since the same heading text also appears harmlessly in
the table of contents) and stops extraction there, so `2020-21 Budget`
now yields only its own 209 genuine measures — the appendix's content
is excluded rather than colliding.

## 8. Garbled/structural measure names in `measure_impacts` (PBS side)

Triggered by users noticing obviously-broken entries while browsing
search results ("crazy measure names that are clearly just snippets of
text"). Method: cross-reference `measure_impacts` (PBS Table 1.2) against
`measure_text` (BP2, section 7 above) for the 3 editions both sources
cover (2025-26 Budget, 2024-25 Budget, 2024-25 MYEFO) — every real
measure should appear in both independently-authored sources, so a name
in one but not the other is a strong signal of either a parsing bug or
a genuine cross-source naming difference. Found and fixed several
distinct `parse_measures.py` bugs, all structural (a header/divider/
total/placeholder row misread as if it were a measure name), not
guesses:

- **Singular "measure" vs plural "measures"**: `SECTION_RE`/
  `GRAND_TOTAL_RE` required the plural ("Payment measures", "Total
  payment measures") for what's structurally the same divider/grand-
  total row; AUSTRADE and WGEA (2025-26 Budget) use the singular
  ("Payment measure", "Total payment measure"), which fell through and
  got read as real measure names. Both regexes now accept an optional
  trailing "s".
- **A bare "Measures" divider with no Receipt/Payment qualifier** (ATO
  2024-25 Budget, under its own "Part 2: Other measures not previously
  reported in a portfolio statement" — a mixed-direction section).
  `SECTION_RE`'s receipt/payment prefix is now optional; `
  section_direction` resets to `None` in this case rather than
  guessing, so category lines fall back to their own wording (existing
  behaviour for "no section declared").
- **"Other Portfolio measures since the &lt;prior update&gt;"** (Health
  2025-26 Budget): a second "Part"-equivalent divider, introducing
  measures where this agency isn't the lead but has some involvement.
  Not recognised as a boundary, it was captured as a measure name of
  its own — and worse, because the very next row (a repeated
  "Outcome/Program" mini-header) was silently skipped without resetting
  parser state, the *real* next measure's name ("Closing the Gap -
  further investments") got captured as if it were that bogus
  measure's own *agency*, corrupting everything under the divider.
  `OTHER_PORTFOLIO_MEASURES_RE` now treats it as a boundary, the same
  way `PART_RE` already is.
- **An unfilled Excel template placeholder**: `"<Enter measure name
  here> (a)"` (ACSQHC 2025-26 Budget) — a genuine $0 row whose own
  footnote says the real measure is published under a different
  agency's Table 1.2. Filtered out via `PLACEHOLDER_MEASURE_NAME_RE`
  rather than recorded as a real (empty) measure.
- **That same footnote's own explanation text**, a few rows further
  down the same ACSQHC sheet: `"(a) ACSQHC is not the lead entity for
  this measure. Full details for this measure are published under
  Table 1.2: Department of Health 2024-25 Budget Measures."` — a
  distinct bug from the placeholder above (found after a user still saw
  it in search after the placeholder fix shipped). `FOOTNOTE_MARKER_RE`
  only matched a *bare* marker sharing a row with a data value ("(a)");
  a marker followed by actual explanatory text fell through and was
  captured as a measure name of its own, and a stray trailing row of
  unlabelled bare numbers right after it (`[142, 58, 58, 58, 58, 58,
  58]` — doesn't cleanly map to the sheet's 5 fiscal-year columns, and
  the sheet's own next line says "This section is not applicable to the
  ACSQHC") got attached to it as if it were real agency $ data.
  `FOOTNOTE_EXPLANATION_RE` now skips a marker-plus-text row -- only
  that one row, not the rest of the sheet (`SPARE_SPACE_RE`'s
  approach), since a per-measure footnote placed immediately after that
  measure's own block rather than collected at the end is plausible in
  some other file's layout, and stopping the whole sheet there would
  silently drop every measure after it. This file (ACSQHC 2025-26
  Budget) now correctly produces zero measure records — it never had a
  real one, only the placeholder and its footnote.
- **A measure title's own Excel row-wrap split it across two separate
  rows**, not just one wrapped cell: TEQSA 2024-25 MYEFO has
  `"Australian Universities Accord -"` and `"further reforms (a)"` as
  two distinct rows (found after a user noticed the truncated title
  directly). For a single-agency sheet with no explicit "Agency Name"
  row (implied to be the filing agency itself), the wrapped subtitle
  landed exactly in the row position `expect_agency` was waiting to
  read next — truncating the title *and* misreading its own
  continuation as the agency name. No genuine, complete measure title
  ends in a dangling, unpaired dash, so `TRAILING_DASH_RE` uses that as
  a mechanical (not guessed) signal: if the just-captured measure name
  ends in one, the next row continues the title instead of naming an
  agency. Checked across the whole dataset after the fix — zero
  remaining measure names end in a dangling dash.
- **"Administered expenses"/"Departmental expenses" as a third wording**
  for the category-line role alongside "...payments"/"...receipts"
  (NIAA 2024-25 MYEFO) — surfaced by the same user report, since this
  file was *also* one of the "Australian Universities Accord" measure's
  three source files, and its own category lines were similarly
  misread (the very first one, before any real measure name, was
  captured as a measure named "Administered expenses" outright).
  `CATEGORY_RE` now accepts "expenses?" as a third suffix; direction
  inference already treats it as "payment" the same way BP2's own
  Revenue/Expense/Capital terminology does (section 7) — nothing
  contains the word "receipt", so it falls through to the "payment"
  default correctly, no new logic needed there.
- **A "Program N.N"-named sheet colliding with the "Table 1.2"
  detection token**: DVA 2024-25 Budget has a per-program expense-detail
  sheet literally named `"2.1.2 Prog 1.2"` — `find_measures_sheet_names()`
  already documented excluding "Program N.N"-style sheets (a known
  naming collision), but the actual exclusion regex (`^program\b`) only
  matched names *starting* with the literal word "Program", missing the
  abbreviated, not-at-the-start "Prog 1.2". That whole sheet's
  reconciliation labels ("Annual Administered Expenses:", "Special
  Appropriations:", "Total program expenses") were being read as
  measure names. Exclusion widened to `\bprog(?:ram)?\.?\s*\d` (matches
  anywhere in the name, "Prog" or "Program", optional period).
- **A sheet named "Table 1.2" whose own title says it's something else
  entirely**: ANAO and APSC (2024-25 MYEFO, both PM&C) each name their
  Table 1.2 sheet "Additional Estimates and other variations to
  outcomes since the 2024-25 Budget" — an appropriations-reconciliation
  table (Movement of Funds, Changes in Parameters, net increase/
  decrease sub-totals), not a measures list at all. Every genuine
  measures sheet observed says "measures" in its own title; a "Table
  1.2:" title that doesn't is now a reliable exclusion signal (checked
  without touching `find_measures_sheet_names()`'s existing
  name-only matching, which its own docstring explains was deliberately
  kept name-only after an earlier content-based attempt produced a
  different false positive).
- **Two more dash variants and a zero-width space** added to
  `_canon_measure_name()` (identical in both `parse_measures.py` and
  `parse_bp2.py`): "‐" (U+2010 HYPHEN, distinct from the ASCII hyphen
  already handled) and "─" (U+2500 box-drawing light horizontal, a
  font-substitution artifact — see section 7); a trailing U+200B
  zero-width space silently fragmented `"Workplace Relations"` (2024-25
  Budget) from its otherwise-identical self.

**Net effect**: distinct measure names in `measure_impacts` dropped from
441 to 414 across this whole pass (garbage/duplicate entries removed,
real ones merged); for the 3 cross-referenced editions, entries only
appearing in PBS (not BP2) dropped from 138 to 114. A full re-scan for
common breakage shapes (bare structural words, dangling dashes, leading
`<`, trailing `,`/`:`) after every fix in this section turns up exactly
one remaining hit — `"Supporting Connectivity,"` below, a confirmed
genuine source-document defect, not a parser gap.

**Confirmed NOT parser bugs — genuine source-document defects, left
as-is:**

- **`"Supporting Connectivity,"`** (DITRDCA 2024-25 Budget): the raw
  cell content is literally that — a trailing comma, nothing else, in
  the government's own published spreadsheet (appears twice, both times
  immediately followed by a row reading "Media and Communications",
  strongly suggesting the real title is "Supporting Connectivity, Media
  and Communications" that got split across two cells by whoever
  prepared the file). Not "fixed" by guessing a concatenation — that's
  exactly the kind of heuristic this project has deliberately avoided
  throughout.
- **`"Department of the Senate"`** (DoS 2024-25 Budget) is itself an
  agency name, not a measure — this one small file (2 measures total)
  uses an inverted layout (agency name on its own row with no values,
  immediately followed by measure-name rows that carry their $ values
  *inline*, with no separate Administered/Departmental category
  sub-row at all) that the state machine doesn't handle. Narrow enough
  (one file, ~$1.1m total) that bespoke handling for this one layout
  variant wasn't judged worth it; documented rather than fixed.
- **Pervasive typo/spelling variants of "Savings from External Labour -
  (further) extension"** — a measure evidently touching many agencies,
  each typing its own title independently: **16 distinct strings**
  observed across 2024-25 Budget and 2025-26 Budget combined for what
  is unambiguously one real measure ("Saving"/"Savings", "for"/"from"/
  "on" External Labour, "External Labour Savings" word-order reversed,
  missing-space "-further", case differences, "labour"/"Labour").
  None of this is a parsing bug — every variant is genuinely present,
  character-for-character, in some agency's own independently-typed
  Table 1.2 sheet. Deliberately not consolidated: doing so would mean
  case-insensitive or edit-distance matching on `measure_name`, which
  is exactly the fuzzy-matching approach this project has consistently
  rejected elsewhere in favour of deterministic, source-verifiable
  joins (see section 6 and `agency_aliases`) — worth a decision from
  the project owner on whether/how to handle it, not something to
  guess at unilaterally.

## 9. Structural gaps found expanding PBS measures ingestion to all 19 editions

Section 8's fixes were found and verified against the original 3-edition
validation set. Expanding `EDITIONS` in `build_measures_db.py` to all 19
Budget/MYEFO editions (2017-18 through 2025-26) exposed a much wider
variety of file structure than that set alone showed — older editions'
Revenue/Expense/Capital terminology (already handled for BP2, section 7,
but not yet for PBS), one edition's flat directory layout, and several
more one-off agency-specific quirks. Method: iterate `parse_workbook_measures`
across all 19 editions, flag obviously-broken measure names (short
strings, bare structural words, leading `<`, trailing `,`/`:`, a dangling
dash) via a heuristic scan, root-cause each via direct `openpyxl` cell
dumps (never guessed), fix, and re-scan until the only survivor is the
already-documented `"Supporting Connectivity,"` genuine source defect
(section 8). All fixes below are in `parse_measures.py` and
`build_measures_db.py` unless noted.

- **`2017-18 MYEFO` has no portfolio subdirectory layer at all** — every
  other edition organizes agency files one level down inside a portfolio
  directory; this one's 48 agency files sit flat in the edition root.
  `iter_files()` in `build_measures_db.py` now detects a flat layout and
  falls back to that same fiscal year's own Budget edition's directory
  structure for a portfolio label (`_portfolio_fallback_index()`) — purely
  cosmetic (`portfolio` is a display-only field, never part of the
  `measure_impacts`/`measure_programs` join key), so a miss there (19 of
  44 files) is harmless.
- **Revenue/Expense/Capital terminology** (pre-~2022-23 editions, the same
  shift already handled for BP2 via `_canon_impact_word`) — `SECTION_RE`,
  `GRAND_TOTAL_RE`, and `TOTAL_RE` all widened to accept these alongside
  Receipt/Payment; a new `_is_receipt_like()` helper centralizes the
  "revenue counts as receipt" check (expense already fell through to the
  payment default correctly on its own).
- **A trailing footnote marker on a *section* header** — DHS 2017-18's
  own section line is `"Expense measures (a)"`; `SECTION_RE` required an
  exact-end match with no room for one (already handled elsewhere for
  measure/category lines). Now optional here too.
- **DVA's own "Total Outcome N" / "Total All Outcomes"** recurs almost
  every edition, closing that outcome's own measures block and immediately
  followed by its own Administered/Departmental/Total breakdown rows —
  the same structural role `GRAND_TOTAL_RE`'s own `in_grand_breakdown`
  skip-mode exists for, but keyed by outcome rather than direction.
  `TOTAL_OUTCOME_RE` added and wired into the same skip-mode.
- **A recurring sheet-name collision with the "1.2" detection token**,
  seen in several distinct spellings across DVA and CCEEW files across
  multiple editions (`"2.1.2 Prog 1.2"`, `"Program Expenses 1.2"`,
  `"Table 2.1.2 1.2"`, `"T2.1.2-1.1"`/`"T2.1.2-1.2"`, `"Prog Exp 1.2"`) —
  all confirmed via direct sheet dumps to be genuine per-program
  expense-detail tables, not measures lists. Rather than adding one
  pattern per spelling, consolidated into two general signals in
  `find_measures_sheet_names()`: a literal `2.1.<n>` substring, or
  "prog" near "exp" anywhere in the sheet name.
- **A "Table 1.2 Levies" sub-table** (Agriculture, 2017-18 and 2019-20) —
  a per-commodity breakdown of *one* measure's own sub-components
  (`"Avocado"`, `"Banana"`, `"Total revenue impact"` per commodity, etc.),
  not a measures list at all. Excluded by sheet name (`"levies"`).
- **Comma-separated multiple footnote markers** (`"...Package (b),(d)"`,
  Agriculture 2019-20) — `MEASURE_NAME_FOOTNOTE_RE`'s repeated-group match
  broke on the literal comma, stripping only the last marker and leaving
  a dangling `"...Package (b),"`. Now tolerates an optional comma between
  markers.
- **Two placeholder-template wordings**: `"<Enter measure name here>"`
  (ACSQHC, section 8) and `"<insert measure name here>"` (ARPANSA) —
  `PLACEHOLDER_MEASURE_NAME_RE` now accepts either verb.
- **A sheet literally named "Table 1.2" whose own title row says
  something else** — OSI's 2021-22 Budget file is the clearest case: the
  sheet is named "Table 1.2" but its actual title row says `"Table 1.1:
  OSI resource statement..."` (this small, newly-created agency had no
  Table 1.2 measures that round, and the placeholder sheet was never
  removed or replaced). The existing title-vs-"measure" guard (section 8)
  only checked when the title itself said "Table 1.2" — a title that says
  a *different* table number entirely slipped past it. `ANY_TABLE_TITLE_RE`
  (any `"Table N.N"` title, not just 1.2) now feeds that same guard.
- **A short leading "Adm"/"Dep" marker column**, unique to Home Affairs'
  own 2022-23 March Budget file — every category row there is prefixed
  with this abbreviation in its own leading column, ahead of the real
  `"Administered receipt"`/`"Departmental receipt"` text a couple of
  columns later (confirmed via a full 19-edition scan to be the only file
  with this layout). Unrecognised, `"Dep"` alone doesn't match `CATEGORY_RE`
  and fell through as a bogus one-word measure name. Now detected and
  re-pointed at the real category cell wherever it sits in the row.
- **That same file's own "&"-separated program-hint notation**
  (`"2.2 & 3.3"`, vs. the comma-separated convention `PROGRAM_LIST_RE` was
  built around) and its own **`"Receipt measures (if applicable)"`**
  section wording (the space inside the parens meant it wasn't a footnote
  marker, so `SECTION_RE` didn't recognise it either) — both needed fixing
  together: unrecognised, the measure-name-with-program-hint row and the
  section header both fell through as bogus "agency" lines, corrupting
  `measure_name`/`agency` for the entire sheet (each ended up holding the
  *other* field's real value). `PROGRAM_LIST_RE` now accepts `&` as an
  alternate separator (and `_parse_program_hint()` splits on it); `SECTION_RE`
  now accepts a trailing `"(if applicable)"` qualifier.
- **A row of border/divider characters mid-sheet**, seen in DSS's own
  2025-26 Budget file: an all-`"+"` (occasionally `"+"`/`"-"` mixed) row,
  a leftover from a repeated print-layout header block appearing several
  times through the sheet. Every cell reads as "data-like" (non-empty,
  non-numeric, not a footnote marker), so unskipped this got folded in as
  a bogus zero-dollar continuation row of whatever measure was still open
  — and its own label sometimes got misread as a new measure name
  outright. Now skipped outright before it can touch parser state at all.
- **Social Services/SAus's own closing lines** (2022-23 MYEFO): `"Net
  impact on appropriations for Outcome 1 (departmental)"` (a sub-total
  closing the Departmental section) and `"Total net impact on
  appropriations for Outcome 1"` (the sheet's own grand total right after
  it) matched neither `GRAND_TOTAL_RE` (no "measures" in either) nor
  `TOTAL_OUTCOME_RE` (no bare "Total Outcome N"). The first fell through
  as a free-text "agency" line carrying that outcome's *entire* $ total
  inline, double-counting it into whatever measure was still open; the
  second then got misread as a new measure name of its own.
  `NET_IMPACT_TOTAL_RE` added, wired the same way `TOTAL_OUTCOME_RE` is.
- **That same file's own `"Other Variations"` sub-header**, right before
  the two lines above — a parameter-change/appropriations reconciliation
  (`"(net increase)"`/`"(net decrease)"`), not a real announced measure,
  structurally the same "footer breakdown before this section's own
  total" role `GRAND_TOTAL_RE`'s skip-mode exists for. Unrecognised,
  `"Other Variations"` became a bogus measure name and `"(net decrease)"`
  — the entry with no matching `"(net increase)"` row directly adjacent
  to it in this file's specific layout — a second, entirely separate bogus
  measure. `OTHER_VARIATIONS_RE` added, wired the same way.
- **A second, bespoke 2022-23 MYEFO-wide format**: `"Table 1.2: Additional
  estimates and variations to outcomes from measures and other
  variations"` — confirmed via a direct programmatic title check to
  affect ~88% (22 of 25) of that edition's own files. Unlike the ANAO/APSC
  case (section 8), this title *does* contain the word "measure", so
  needed its own, more targeted exclusion (checking for the phrase
  `"variations to outcomes"`) layered on top of the existing "measure not
  in title" guard. Deliberately **not** given a dedicated parser: its
  actual layout (Outcome → Administered/Departmental →
  Annual-appropriations/Movement-of-Funds/Other-Variations/Special-
  appropriations sub-groups, each with named $ line items) is a
  genuinely different structure from the Measure→Agency→category-lines
  shape this parser is built around, not just different wording — the
  same call already made for Defence's own bespoke format (section 1).
  Force-fitting it would risk misattributing real $ figures to the wrong
  measure/agency, worse than a clean, documented gap. **This edition's
  own PBS measures are therefore under-covered** (19 distinct measures
  recovered for 2022-23 MYEFO overall, entirely from the ~12% of files
  that don't use this format) — a real, accepted gap, not a bug.
- **A section-labelled grand-total row**, found after the fact by
  inspecting the live `measure_list` API response post-rebuild (the
  smoke-test heuristic's `^total` anchor missed it, since none of these
  start with the word "total"): PM&C's own 2022-23 March Budget file
  labels each of its sheet's multiple totals with a letter, e.g. `"(A)
  Total 2022-23 Budget measures"` (closing its "Payment measures"
  section — note the edition name sitting where a receipt/payment word
  usually would), `"(A+B) Total payment measures"`, `"(B) Total other
  measures"` (closing a later "Other measures previously provided for"
  section); 2023-24 MYEFO separately has a plain `"Sub-Total payment
  measures"` / `"Sub-Total payment measures (Capital)"` variant.
  `GRAND_TOTAL_RE`'s anchor required the row to *start* with "total" —
  none of these five do. Widened to accept an optional leading
  `"(A)"`/`"(A+B)"`/`"Sub-Total"`-style label, and an optional edition
  name (`"2022-23 Budget"`) between "total" and "measures" alongside the
  existing receipt/payment word. Found and fixed after the main
  19-edition rebuild (which is why this document's own row/name counts
  above already reflect it, from a second rebuild) — a reminder that the
  suspicious-name heuristic used throughout this section is a net, not a
  guarantee; spot-checking the live API after a bulk rebuild is still
  worth doing.

## 10. Topic search (`chroma_measures/`, `measure_topic_search`)

A third search mode, alongside the existing exact-name and exact-
substring-in-text ones (sections 6-9 above), for finding measures by
what they're *about* rather than by matching specific words -- e.g. a
query for "child care" surfacing "Building Australia's Future -
delivering pay rises for early educators" (2024-25 MYEFO), whose own
write-up only ever says "Early Childhood Education and Care (ECEC)",
never the literal query words, so `measure_text_search`'s substring
match can't find it at all.

**How it works:** `build_measure_embeddings.py` embeds every
`measure_text` row (its own name + intro/end prose + every bulleted
component, concatenated) into a persistent chromadb collection at
`chroma_measures/`, one document per `measure_id`. Uses chromadb's own
default local embedding model (`all-MiniLM-L6-v2` via `onnxruntime`,
384-dim, downloaded once to `~/.cache/chroma` on first run) rather than
a paid embedding API -- no API key needed, works offline after that
first download. `backend/measures/views.py`'s `measure_topic_search`
(routed at `/api/measures/search-topic/`) loads that same collection
(as a lazy module-level singleton, since the embedding model is
expensive to load) and returns the closest matches, resolving
portfolios/agencies/`has_financial_data` the same way
`measure_text_search` does by joining back to `MeasureImpact`/
`MeasureProgram`. Wired into the frontend as a third "Topic" toggle on
`SearchPage.svelte`, alongside "Measure name"/"Measure text", sharing
the same debounced-server-search plumbing as the existing text mode.

**Rebuilding the index:** run `backend/.venv/bin/python
build_measure_embeddings.py` (needs that specific venv, since that's
the same one the Django server reads the collection from at query
time) any time `measure_text` changes -- a new BP2 edition ingested, a
text-parsing fix, etc. Safe to re-run: `upsert()`s by `measure_id`, so
it only overwrites changed documents rather than duplicating anything.
Took ~4,366 documents in one run, a few minutes on a laptop CPU.

**Known limitation -- a small local model has real ceiling on
cross-vocabulary bridging for longer documents.** Concretely verified:
for the query "child care", the National Partnership Agreement ECEC
measure above ranks #7 out of ~4,300 (comfortably surfaced) but the
"early educators pay rise" measure used as the motivating example above
ranks only ~213th (i.e. *not* surfaced within a normal-sized results
page) -- its own write-up is long (~650 words) and dominated by
payment-mechanics detail (opt-in dates, fee caps, dollar figures) that
dilutes the mean-pooled embedding's topical signal from the one
ECEC-mentioning sentence. More specific queries ("ECEC", "childcare
workforce wages", "early educators pay rise") all rank that same
measure in the top handful, so the gap is specifically "a two-word,
maximally-generic query against a long, detail-heavy document," not a
wholesale failure of the approach -- confirmed by checking the actual
top-30 results for "child care" (`curl
'localhost:8000/api/measures/search-topic/?q=child+care'`), which are
all genuinely on-topic, several without the literal query words
anywhere in their own text (e.g. "Closing the Gap Partnership on Early
Childhood Care and Development", "Australian Children's Education and
Care Quality Authority - additional funding"). Tried and rejected as a
fix: per-chunk embedding (name + intro as one chunk, each component as
another, taking the best-matching chunk per measure at query time) --
prototyped directly against this same test case and it only moved the
distance from 1.47 to 1.27, still far outside a normal results page, so
the added complexity (multi-chunk dedup logic at both ingest and query
time) wasn't judged worth it for the improvement actually measured.
Swapping in a larger embedding model (e.g. `all-mpnet-base-v2` via
`sentence-transformers`, which pulls in a ~2GB PyTorch dependency) was
not tried -- a reasonable next step if this specific failure mode turns
out to matter in practice.

## 11. Portfolio naming (`_canon_portfolio`, `measure_text`'s 2016-17 MYEFO phantom entries)

Triggered by a user noticing the search page's new portfolio filter
(section 10's pips UI) listing entries with a year baked in
("2020-21 PAES AGs") alongside obvious spelling/case duplicates
("AGRICULTURE" next to "Agriculture"). Investigating surfaced two
distinct problems, one cosmetic and one a genuine correctness bug.

**The correctness bug: 93 phantom "measures" in 2016-17 MYEFO.**
`parse_bp2.py`'s `NEXT_APPENDIX_RE` (the mechanism that's supposed to
stop extraction at "Appendix B" onward, so that appendix's own
government-wide financial-statement/contingent-liability content
doesn't get read as measures -- see section 7) never fires for this one
edition's own Appendix B divider, because that divider is drop-cap
styled ("AGFS" at 12.96pt bold, then "USTRALIAN OVERNMENT INANCIAL
TATEMENTS" at 10.56pt bold, on the same line -- the same first-letter/
rest-of-word split section 7 already documents for 2014-15 Budget's own
divider, confirmed via direct `pdfplumber` char-level font/size
inspection). The literal string "Appendix B: Australian Government
Budget Financial Statements" only ever appears as a small 9.96pt
italic running header, correctly excluded by the existing size>13 gate
as not a real divider -- so nothing on the page matches
`NEXT_APPENDIX_RE`'s "^Appendix [B-Z]" pattern at the required size,
and extraction ran straight through into Appendix B's own tables.
Result: 93 phantom "measures" for 2016-17 MYEFO with `portfolio` values
that are actually document-structure headings ("FISCAL RISKS",
"AUSTRALIAN GOVERNMENT FINANCIAL STATEMENTS", "SIGNIFICANT BUT REMOTE
CONTINGENCIES", ...), 32 of them literally titled "Table B1: ...",
"Table B2: ..." etc, the rest genuine-looking titles from the
Statement of Risks section within that same appendix ("Murray Darling
Basin Plan", "Same-Sex Marriage Plebiscite") that are contingent-
liability disclosures, not measures. Confirmed isolated to this one
edition alone (checked: no other of the 21 ingested editions has any
measure named "Table %", and no other edition's phantom-style portfolio
values appear anywhere in the dataset).

Fixed with a second, independent boundary signal rather than trying to
parse the drop-cap text back together (fragile, and the reason 2014-15
Budget was left unfixed instead, per section 7): `NEXT_APPENDIX_TABLE_RE`
(`^Table\s+[B-Z]\d`) recognises that Appendix B onward's own tables are
always captioned "Table B1:"/"Table C3:"/etc -- a letter B or later
directly followed by a digit, a shape no genuine measure's own
within-Appendix-A table caption uses (those use the existing
`TABLE_CAPTION_RE`'s "Table 1:"/"Table A.1:" shape instead). Wired in
right alongside the existing `NEXT_APPENDIX_RE` check. Rebuilding
`measure_text` after the fix: 4,366 rows -> 4,273 (exactly the 93
phantom entries removed, confirmed by re-checking both the specific
junk-portfolio values and the "Table %"-named measures are now zero);
the last genuine 2016-17 MYEFO measure by page number is now
"Australia-Singapore Comprehensive Strategic Partnership..." (Defence,
page 214) -- exactly where the real Appendix A content actually ends.

**The cosmetic problem: portfolio naming inconsistency across sources.**
Across `measure_text`/`measure_impacts`/`measure_programs` combined, 157
distinct raw `portfolio` strings were observed for what is really only
about 50 actual portfolios (a fixed, well-known small enumeration --
Australian Government portfolios don't number in the hundreds). Two
separate root causes:

- Some PBS editions' own directory layout embeds the edition and
  document type directly in the folder name used as `portfolio`
  ("2020-21 PAES AGs", "2021-22 PBS PM&C1") instead of a clean label --
  confirmed via a direct directory listing that other editions (e.g.
  2024-25 Budget) use a bare short code ("AG", "DAFF") with no such
  prefix, so this is a genuine per-edition source inconsistency in how
  each year's PBS bundle happened to be organised, not a uniform
  convention `build_measures_db.py`'s `iter_files()` could rely on.
- The same real portfolio is independently spelled differently by BP2
  (measure_text, often ALL CAPS in older editions) vs. PBS (short
  agency-style codes) vs. genuine typos in the source documents
  ("Foriegn Affairs & Trade", "Infrastructure adn Regional
  Development", "Attorney-Generals").

Fixed at the API layer (`backend/measures/views.py`'s `_canon_portfolio`),
not by rewriting the raw stored values -- the same choice already made
for agency naming via `agency_aliases` (section 6) and consistent with
how `program_expenses.portfolio` is documented (section 4) as its own,
separately messy, deliberately-not-yet-normalised field. Two steps:
`_strip_portfolio_prefix()` regexes off a leading `"YYYY-YY PAES/PBS/
MYEFO "` token (plus a stray trailing digit, the "PM&C1" artifact), then
`_PORTFOLIO_ALIASES` maps the ~40 verified case/typo/abbreviation
variants (built by direct inspection of the full 157-string list, not
guessed) to one canonical spelling each. Result: 157 raw strings ->
50 canonical portfolios.

**Deliberately NOT merged**, even though they look similar at a glance:
genuinely different portfolio names from different eras/machinery-of-
government changes -- "Agriculture" vs "Agriculture, Water and the
Environment" vs "Agriculture, Fisheries and Forestry"; "Health" vs
"Health and Aged Care"; "Industry, Innovation and Science" vs
"Industry, Science and Resources" vs "INDUSTRY AND SCIENCE"; the three
different-length "Infrastructure, Transport, ..." names. Collapsing
these would misrepresent history the same way cross-portfolio-reshuffle
agency bridging is deliberately not attempted in section 6. A handful
of genuinely ambiguous bare forms ("Foreign Affairs", "Communications"
was itself fixed to at least correct its case, but not merged into
"...and the Arts") were left unmapped rather than guessed at.

**Deliberately scoped to display/filtering only, not `measure_detail`.**
`measure_list`/`measure_text_search`/`measure_topic_search` all run
their `portfolios` through `_canon_portfolio` -- safe, since nothing
downstream depends on that exact string (a search result navigates by
`measure_id`, never by portfolio). `measure_detail`'s own `portfolios`
field is deliberately left raw: `MeasurePage.svelte`'s portfolio badges
feed that exact string into `portfolio_profile()`'s `portfolio` query
param, which does an **exact-string match** against
`program_expenses.portfolio` -- a different table, built by a different
pipeline (`build_db.py`), whose own portfolio strings are independently
messy in a way `_canon_portfolio` has no visibility into. Canonicalizing
`measure_detail`'s display string would silently break that join for
any portfolio whose canonical form doesn't happen to already match
whatever `program_expenses` stored -- a real risk, not a hypothetical
one, since that table's own portfolio drift is separately documented
(section 4) and not fixed by this pass.

Also fixed in passing: `build_measure_embeddings.py` (section 10) only
ever `upsert()`d, never deleting a chromadb document whose source
`measure_text` row disappeared -- meaning the 93 phantom entries above
would have silently lived on in topic search forever, even after being
correctly removed from `measure_text` itself. Now diffs the current
`measure_text` id set against the collection's own ids and deletes
anything no longer present before upserting the rest.
