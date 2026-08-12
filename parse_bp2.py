"""
Parse Budget Paper No. 2 (BP2) / MYEFO "Appendix A" measure write-ups --
the government-wide, narrative measures document, as opposed to the
per-agency PBS/PAES Table 1.2 spreadsheets parse_measures.py handles.

Every measure gets one detailed write-up: a Portfolio heading, a Measure
heading, a headline financial table (by agency, by year -- optionally
followed by a "Related receipts"/"Related payments" table for the
opposite direction), one or more "intro sentence + bulleted list" prose
blocks (components marked with "*"/*bullet*, sub-components with "-"/
*dash*), and closing prose that may reference a related program or prior
measure by name (rendered in italics).

The document also opens with government-wide SUMMARY tables (Table 1/2,
or Table A.1/A.2 in a MYEFO's Appendix A) recapping every measure in one
place -- these are skipped entirely; only the individual write-ups are
parsed.

Row-hierarchy / structural notes (see KNOWN_GAPS.md for the write-up of
how these were derived by inspecting the raw PDF text+font layer, not
just the rendered page):

- Bullet characters are real "*"/*bullet* and "-"/*en dash* glyphs, not
  literal asterisk/hyphen characters -- and "-"/*en dash* is heavily
  overloaded elsewhere in the document (date ranges, title separators),
  so bullets are identified by BOTH the character and the fact that
  bullet glyphs render in a distinct font family (Times New Roman) from
  ordinary body text (Book Antiqua), not by character alone.
- Portfolio headings and Measure headings are both bold Arial, but at
  different sizes (~13pt vs ~10pt) -- that size difference, not just
  boldness, is what distinguishes them.
- Italics (font name containing "Italic") mark two different things that
  look identical structurally: a referenced *program* name, and a
  referenced *prior measure* name. Both are captured the same way here
  (as "related_measures", the raw phrase) -- disambiguating which is
  which is deferred to run time, not attempted here.
"""
import re
import sqlite3
from collections import defaultdict

import pdfplumber

# Page-margin running headers/footers ("Budget 2025-26 | ...", "Appendix
# A: ... | Page NNN") sit well outside this vertical band on every page
# observed; anything within the band is real content.
HEADER_Y_CUTOFF = 110
FOOTER_Y_CUTOFF = 730

PORTFOLIO_HEADING_SIZE = 12.96
MEASURE_HEADING_SIZE = 9.96
SIZE_TOL = 0.5

TABLE_CAPTION_RE = re.compile(r"^Table\s+(?:A\.)?\d+[:.]", re.I)
# 2020-21 onward calls the two measure categories Receipt/Payment (this
# era has no distinct Capital section -- see the "Related capital" note
# on FIN_TABLE_HEADER_RE below). Pre-2020-21 (2014-15 through 2019-20)
# uses three: Revenue/Expense/Capital -- Revenue and Expense are the same
# real categories as Receipt and Payment under an older name, just
# renamed at some point between the 2019-20 and 2020-21 editions;
# Capital is a genuine third category that era's format broke out
# separately. _canon_impact_word() below maps both eras' words onto one
# canonical (receipt|payment|capital) key.
SECTION_MARKER_RE = re.compile(
    r"^(?:Part\s+\d+:\s*)?(Receipt|Payment|Revenue|Expense|Capital)\s+Measures$", re.I
)
FIN_TABLE_HEADER_RE = re.compile(
    r"^(Related\s+)?(Receipts?|Payments?|Revenue|Expenses?|Capital)\s*\(\$m\)$", re.I
)
# Em dash (2020-21+ editions use "Total – Payments"/"Total - Payments"
# with a plain hyphen or en dash) alongside en dash/hyphen: pre-2020-21
# editions use an em dash ("Total — Expense").
TOTAL_ROW_RE = re.compile(r"^Total\s*[–—-]\s*(Receipts?|Payments?|Revenue|Expenses?|Capital)$", re.I)
YEAR_COL_RE = re.compile(r"^\d{4}-\d{2}$")

_IMPACT_WORD_CANON = {
    "receipt": "receipt", "receipts": "receipt", "revenue": "receipt",
    "payment": "payment", "payments": "payment", "expense": "payment", "expenses": "payment",
    "capital": "capital",
}


def _canon_impact_word(word):
    """Maps either era's word for a measure category onto one canonical
    lowercase key (receipt/payment/capital) -- see the note on
    SECTION_MARKER_RE above for why Revenue/Expense collapse into
    receipt/payment while Capital stays its own category."""
    return _IMPACT_WORD_CANON[word.lower()]

# 2020-21 Budget.pdf is the one file (of every Budget/MYEFO edition
# checked) that bundles a second, foreign document after its own content
# ends: a full appendix restating the *previous* update's measures
# ("Appendix A: Policy decisions published in the July 2020 Economic and
# Fiscal Update" -- 2020-21 was the COVID-delayed year, so a July 2020
# mini-update preceded the actual October Budget). Left unhandled, its
# measures collide in title with the real Budget's own -- same topic,
# different dollar figures/dates, silently dropped by the duplicate-title
# guard in build_bp2_db.py. Distinguished from a MYEFO's own (wanted)
# "Appendix A: Policy decisions taken since the <prior Budget>" -- that
# phrasing is this document's own primary content, not a foreign one --
# by "published in" vs "taken since".
FOREIGN_APPENDIX_RE = re.compile(
    r"^Appendix\s+[A-Z]:\s*Policy\s+decisions\s+published\s+in\b", re.I
)

# A MYEFO's own measures live entirely within "Appendix A"; whatever
# appendix follows it -- "Appendix B: Supplementary Expenses Table and
# the Contingency Reserve", "Appendix C: Australia's Federal Relations",
# "Appendix D/E: Historical/Tax data", the cash-flow/Statement-of-Risks
# tables within them -- is different government-wide content that
# happens to share the same bold-heading styling, not measures. Without
# a boundary, extraction ran straight into it once current_section was
# set from Appendix A's own divider and never got reset, picking up
# things like "Claims against the Department of Defence" (a contingent-
# liability line from a Statement of Risks) as if it were a genuine
# measure title. NEXT_APPENDIX_RE matches "Appendix B" onward but not
# "Appendix A" itself (a MYEFO's own measures section, wanted) --
# gated the same way as FOREIGN_APPENDIX_RE, on current_section already
# being set, so it doesn't false-positive on this exact same heading
# text appearing earlier on the table-of-contents page.
NEXT_APPENDIX_RE = re.compile(r"^Appendix\s+[B-Z]\b", re.I)
# 2016-17 MYEFO alone (confirmed: no other of the 21 ingested editions):
# NEXT_APPENDIX_RE never fires for its own "Appendix B: Australian
# Government Budget Financial Statements" divider, because that divider
# is drop-cap styled ("AGFS" at 12.96pt bold, then "USTRALIAN OVERNMENT
# INANCIAL TATEMENTS" at 10.56pt bold, on the same line -- the same
# first-letter/rest-of-word split KNOWN_GAPS.md #7 already documents for
# 2014-15 Budget's own divider) -- the literal string "Appendix B:
# Australian Government Budget Financial Statements" only ever appears
# as a small 9.96pt italic running header, which the size>13 gate
# correctly excludes as not a real divider. Extraction ran straight
# through into Appendix B's own tables as a result: 93 phantom
# "measures" (portfolio values like "AUSTRALIAN GOVERNMENT FINANCIAL
# STATEMENTS", "FISCAL RISKS", "SIGNIFICANT BUT REMOTE CONTINGENCIES"),
# 32 of them literally named "Table B1: ...", "Table B2: ..." etc, the
# rest genuine-looking titles from the Statement of Risks section within
# that same appendix ("Murray Darling Basin Plan", "Same-Sex Marriage
# Plebiscite") that are contingent-liability disclosures, not measures.
# Rather than parse the drop-cap text back together (fragile, and the
# reason 2014-15 Budget was left unfixed instead), this catches the
# same boundary a different, more reliable way: Appendix B onward's own
# tables are always captioned "Table B1:"/"Table C3:"/etc (a letter,
# B or later, directly followed by a digit) -- a shape no genuine
# measure's own within-Appendix-A table caption uses (those use
# TABLE_CAPTION_RE's "Table 1:"/"Table A.1:" shape instead).
NEXT_APPENDIX_TABLE_RE = re.compile(r"^Table\s+[B-Z]\d", re.I)


def normalize_portfolio_heading(text):
    """Repair a drop-cap text-extraction artifact seen in some editions'
    portfolio headings, where the first letter of (each word of) a
    heading is pulled out with a stray space -- "T REASURY" -> "TREASURY",
    "A TTORNEY -G ENERAL'S" -> "ATTORNEY-GENERAL'S". A no-op on headings
    that don't have the artifact."""
    text = re.sub(r"\s+-", "-", text)
    text = re.sub(r"\b([A-Z])\s+(?=[A-Z])", r"\1", text)
    return text


MEASURE_NAME_FOOTNOTE_RE = re.compile(r"(\s*\([a-z]{1,3}\))+$")


def _canon_measure_name(name):
    """Same normalization parse_measures.py applies to PBS-sourced measure
    names: en/em/non-breaking dash -> plain hyphen, curly apostrophe ->
    straight, trailing footnote marker(s) stripped. Measure titles are
    typed independently in BP2's prose vs a PBS agency's own Table 1.2
    sheet, and differ in exactly this cosmetic way for the same real
    measure ("Building Australia's Future - ..." vs "...Future – ...",
    "Attorney-General's" vs "Attorney‑General's" with a non-breaking
    hyphen, "Implementation of Aged Care Reforms (a)" in one PBS file vs
    plain "Implementation of Aged Care Reforms" in BP2's own prose) --
    applying the identical canonicalization here is what makes the two
    joinable on measure_name. BP2's own titles don't carry a footnote
    suffix in practice (confirmed directly against the 2025-26 Budget),
    but stripping it here too costs nothing and keeps both sides of the
    join using the exact same rule rather than two similar-but-not-
    identical ones."""
    if name is None:
        return name
    # "─" (U+2500 box-drawing light horizontal) shows up in place of a
    # dash in at least one pre-2020-21 title ("Higher Education Loan
    # Program ─ partial cost recovery delay", 2019-20 Budget) -- a
    # one-off font-substitution artifact in the source PDF itself, not
    # something worth a special case; folded in with the other dash
    # variants since it means the same thing here.
    name = name.replace("–", "-").replace("—", "-").replace("‑", "-").replace("─", "-").replace("‐", "-")
    name = name.replace("’", "'").replace("‘", "'")
    name = MEASURE_NAME_FOOTNOTE_RE.sub("", name).strip()
    return name


# A line ending in one of these (after stripping a trailing quote/bracket)
# is a genuine sentence/paragraph end; anything else is just where the PDF
# happened to wrap, and should rejoin with a space rather than a newline.
_PARAGRAPH_END_CHARS = (".", ":", "!", "?")


def _join_paragraph_lines(lines):
    """_MeasureBuilder.text_lines holds one entry per physical PDF line,
    which wraps mid-sentence at the page margin -- joining them with "\n"
    verbatim (the previous behaviour) turned every line wrap into a
    visible paragraph break once rendered. Rejoin wrapped lines with a
    space and only start a new paragraph where a line actually ends a
    sentence."""
    paragraphs = []
    current = []
    for line in lines:
        current.append(line)
        probe = line.rstrip().rstrip("\"”’)")
        if probe.endswith(_PARAGRAPH_END_CHARS):
            paragraphs.append(" ".join(current))
            current = []
    if current:
        paragraphs.append(" ".join(current))
    return paragraphs


def _is_bold(fontname):
    return "Bold" in fontname


def _is_italic(fontname):
    # "Oblique" is Helvetica's own name for its italic style -- used by
    # some pre-2020-21 editions (2014-15/2015-16/2018-19 Budget, at
    # least) instead of "Italic".
    return "Italic" in fontname or "Oblique" in fontname


def _is_plain_sans(fontname):
    """The financial-table/heading sans-serif font family -- "Arial" in
    2020-21+ editions and some pre-2020-21 ones, "Helvetica" in others
    (different PDF-generation tooling across a decade of editions, same
    visual role: a financial-table row is always this family, never
    Book Antiqua/Times New Roman)."""
    return "Arial" in fontname or "Helvetica" in fontname


def _is_bullet_word(word):
    # "TimesNewRomanPSMT" (2020-21+ and some older editions) vs "Times
    # New Roman" with spaces and no subset prefix (other older editions)
    # -- same font family, different naming convention depending on
    # whatever tool generated that year's PDF.
    fontname = word["fontname"].replace(" ", "")
    return "TimesNewRoman" in fontname and word["text"] in ("•", "–", "-", "*")


def _extract_lines(page, y_tol=2.5):
    """Group words into visual lines (by y-position), each line's words
    left-to-right, filtered to the page's real content band."""
    words = page.extract_words(extra_attrs=["fontname", "size"], keep_blank_chars=False)
    words = [w for w in words if HEADER_Y_CUTOFF <= w["top"] <= FOOTER_Y_CUTOFF]
    words.sort(key=lambda w: (round(w["top"]), w["x0"]))

    lines = []
    current = []
    current_top = None
    for w in words:
        if current_top is None or abs(w["top"] - current_top) <= y_tol:
            current.append(w)
            current_top = w["top"] if current_top is None else current_top
        else:
            lines.append(sorted(current, key=lambda w: w["x0"]))
            current = [w]
            current_top = w["top"]
    if current:
        lines.append(sorted(current, key=lambda w: w["x0"]))
    return lines


def _join_words(words):
    """Join word dicts left-to-right, inserting a space only where the
    gap between consecutive words is wide enough to be a real word
    break. A word like "Australia's" or "cost-of-living" is commonly
    split into several "words" by pdfplumber wherever the font changes
    mid-word (e.g. a curly apostrophe or en-dash sits in a different font
    run) -- those sit hard against their neighbour with ~0 gap, unlike a
    genuine space between words, so a fixed small gap threshold tells
    them apart reliably."""
    if not words:
        return ""
    out = [words[0]["text"]]
    for prev, cur in zip(words, words[1:]):
        gap = cur["x0"] - prev["x1"]
        if gap > 1.0:
            out.append(" ")
        out.append(cur["text"])
    return "".join(out)


def _line_text(line):
    return _join_words(line)


def _numeric_value(raw):
    """'-' -> 0.0 (genuinely nil); a real number -> float; anything else
    ('..', 'nfp', '*', ...) is a placeholder, not a number -- kept as its
    own raw marker rather than coerced to 0, since it means something
    different (negligible-but-nonzero, undisclosed, etc.)."""
    if raw == "-":
        return ("numeric", 0.0)
    cleaned = raw.replace(",", "")
    if re.fullmatch(r"-?\d+(\.\d+)?", cleaned):
        return ("numeric", float(cleaned))
    return ("special", raw)


def _parse_financial_table(lines, start_idx, impact_type, is_related):
    """lines[start_idx] is the 'Receipts ($m)'/'Payments ($m)' (or
    'Related ...') header. Returns (rows, next_idx) -- next_idx is the
    line index right after the table's Total row. Skips the year-header
    row (picked up once, used only to know the column count) and the
    Total row itself (not itself a data row)."""
    idx = start_idx + 1
    year_cols = None
    rows = []
    while idx < len(lines):
        line = lines[idx]
        text = _line_text(line)
        if not text.strip():
            idx += 1
            continue

        first_word = line[0]
        # A table row is always plain (non-bold) Arial. A single-agency
        # measure's table sometimes has no explicit Total row at all (one
        # data row is its own total), so the real end-of-table signal is
        # a line that isn't plain-sans-styled (Arial or Helvetica,
        # depending on edition -- see _is_plain_sans) -- not bold (a
        # heading), not a bullet, and not some other font entirely (the
        # intro paragraph's Book Antiqua) -- rather than relying on
        # TOTAL_ROW_RE alone. Bail out here rather than misreading
        # prose/headings as phantom rows (see the Family Law System
        # regression this guards against: no Total row meant the table
        # parser ran straight into the next paragraph).
        if (
            not _is_plain_sans(first_word["fontname"])
            or _is_bold(first_word["fontname"])
            or _is_bullet_word(first_word)
        ):
            break

        # A "Related receipts ($m)"/"Related payments ($m)" header is
        # italic Arial -- still contains "Arial", so the check above
        # doesn't catch it -- and marks the start of a *second* table,
        # not a continuation of this one. Stop without consuming it so
        # the caller's own FIN_TABLE_HEADER_RE check gets a chance to
        # start parsing it (otherwise it silently folds into the
        # previous row's label as a phantom continuation, and that whole
        # second table's data -- often the only place a receipt like
        # ATO's shows up -- never gets parsed at all).
        if FIN_TABLE_HEADER_RE.match(text.strip()):
            break

        if year_cols is None and YEAR_COL_RE.match(first_word["text"]):
            year_cols = len(line)
            idx += 1
            continue

        # Split label vs value words first, then check the label alone
        # against TOTAL_ROW_RE -- the Total row carries its own values on
        # the same line ("Total - Payments  -  15.6  21.3  1.3  -"), so
        # matching against the raw line text (values included) never
        # matches and the table parse runs away consuming everything
        # after it, including the next measure's own heading.
        label_words = []
        value_words = []
        for w in line:
            v_kind, _ = _numeric_value(w["text"])
            if v_kind == "numeric" or w["text"] in ("..", "nfp", "*"):
                value_words.append(w["text"])
            else:
                label_words.append(w)
        department_name = _canon_measure_name(_join_words(label_words))

        if TOTAL_ROW_RE.match(department_name.strip()):
            idx += 1
            break

        if not value_words:
            # A wrapped agency-name continuation line: the values always
            # sit on the row's *first* physical line (even if the label
            # itself is incomplete there), with any overflow label text
            # wrapping onto label-only lines below -- "Department of
            # Infrastructure, ... 2.1 56.6 55.8 54.9" / "Transport,
            # Regional" / "Development, Communications" / "and the Arts"
            # is one row, not four. So a label-only line belongs to the
            # row already emitted, not one still to come.
            if rows:
                rows[-1]["department_name"] += " " + department_name
            idx += 1
            continue

        values = []
        for raw in value_words:
            kind, val = _numeric_value(raw)
            if kind == "numeric":
                values.append({"value_kind": "numeric", "value_numeric_million": val, "value_raw": raw})
            else:
                values.append({"value_kind": "special", "value_numeric_million": None, "value_raw": raw})
        rows.append(
            {
                "impact_type": impact_type,
                "is_related": is_related,
                "department_name": department_name,
                "values": values,
            }
        )
        idx += 1
    return rows, idx


class _MeasureBuilder:
    """Accumulates one measure's components/text across however many
    lines it spans, across however many pages."""

    def __init__(self, portfolio, section, source_page):
        self.portfolio = portfolio
        self.section = section
        self.source_page = source_page
        self.title_parts = []
        self.headline_financials = []
        self._text_buffer = []  # raw prose lines not yet flushed to self.components
        # Flat list, in document order -- prose paragraphs (marker="text",
        # level=0) interleaved with bullets (marker="dot"/"dash"), so a
        # consumer replaying it in ordinal order reproduces the paper's
        # actual intro/bullets/end-text sequence rather than grouping all
        # prose before all bullets.
        self.components = []
        self._last_component_ordinal = None  # last dot -- subcomponent parenting
        self._last_bullet_ordinal = None  # last dot OR dash -- wrap continuation
        self.related_measures = []
        self._related_seen = set()
        # An italic run touching the end of one line might be the first
        # half of a phrase that wraps onto the next line (e.g. "Indigenous
        # Broadcasting and Media" / "Program") -- held here until either
        # the following line's own first run continues it, or a
        # non-continuing event (a new bullet, or end of measure) forces
        # it to finalize as-is.
        self._pending_italic = None

    def add_title_part(self, text):
        self.title_parts.append(text)

    @property
    def title(self):
        return " ".join(self.title_parts).strip()

    def add_financial_rows(self, rows):
        self.headline_financials.extend(rows)

    def _commit_related(self, phrase):
        phrase = phrase.strip()
        # A sentence-final period sometimes shares the italic run with
        # the last word of the phrase before it ("...Agreement."), which
        # after the run splits on the following non-italic word leaves a
        # stray "." as its own trailing run -- not a real reference.
        if phrase and any(ch.isalnum() for ch in phrase) and phrase not in self._related_seen:
            self._related_seen.add(phrase)
            self.related_measures.append(phrase)

    def _flush_pending_italic(self):
        if self._pending_italic is not None:
            self._commit_related(self._pending_italic)
            self._pending_italic = None

    def note_italic_runs(self, runs):
        for starts_first, ends_last, phrase in runs:
            if starts_first and self._pending_italic is not None:
                phrase = self._pending_italic + " " + phrase
                self._pending_italic = None
            if ends_last:
                # Might continue on the next line -- hold, don't commit yet.
                self._flush_pending_italic()
                self._pending_italic = phrase
            else:
                self._flush_pending_italic()
                self._commit_related(phrase)
        # A run that neither starts at word 0 nor was itself just set as
        # pending means the line had trailing non-italic text after it --
        # nothing left open into the next line.
        if not runs:
            self._flush_pending_italic()

    def add_text_line(self, text, italic_runs=()):
        self._text_buffer.append(text)
        self.note_italic_runs(italic_runs)

    def _flush_text_buffer(self):
        # Rejoin wrapped lines into real paragraphs (see
        # _join_paragraph_lines) and drop each one into self.components in
        # place, right where it actually occurred relative to the bullets
        # around it -- not off to one side.
        if not self._text_buffer:
            return
        for paragraph in _join_paragraph_lines(self._text_buffer):
            ordinal = len(self.components)
            self.components.append(
                {"level": 0, "marker": "text", "parent_ordinal": None, "ordinal": ordinal, "text": paragraph}
            )
        self._text_buffer = []

    def add_component(self, text, italic_runs=()):
        self._flush_pending_italic()
        self._flush_text_buffer()
        ordinal = len(self.components)
        self.components.append(
            {"level": 1, "marker": "dot", "parent_ordinal": None, "ordinal": ordinal, "text": text}
        )
        self._last_component_ordinal = ordinal
        self._last_bullet_ordinal = ordinal
        self.note_italic_runs(italic_runs)
        return ordinal

    def add_subcomponent(self, text, italic_runs=()):
        self._flush_pending_italic()
        self._flush_text_buffer()
        ordinal = len(self.components)
        parent = self._last_component_ordinal
        self.components.append(
            {"level": 2, "marker": "dash", "parent_ordinal": parent, "ordinal": ordinal, "text": text}
        )
        self._last_bullet_ordinal = ordinal
        self.note_italic_runs(italic_runs)
        return ordinal

    def extend_last_component_text(self, text, italic_runs=()):
        # A wrapped continuation of whichever bullet (dot or dash) was
        # added most recently -- looked up by ordinal rather than by
        # list position, since self.components may hold flushed prose
        # paragraphs after that bullet and before this continuation line.
        if self._last_bullet_ordinal is not None:
            self.components[self._last_bullet_ordinal]["text"] += " " + text
            self.note_italic_runs(italic_runs)
        else:
            self.add_text_line(text, italic_runs)

    def finalize(self):
        self._flush_pending_italic()
        self._flush_text_buffer()
        full_text = "\n\n".join(c["text"] for c in self.components if c["marker"] == "text").strip()
        return {
            "portfolio_name": self.portfolio,
            "measure_title": _canon_measure_name(self.title),
            "document_section": self.section,
            "source_page": self.source_page,
            "full_measure_text": full_text,
            "headline_financials": self.headline_financials,
            "components": [
                {k: v for k, v in c.items() if k != "ordinal"} for c in self.components
            ],
            "related_measures": self.related_measures,
        }


def _line_plain_and_italics(line):
    """Join a line's words into plain text, and separately collect every
    contiguous italic word-run on that line (joined back into phrases) --
    these mark a referenced program or prior-measure name. Kept apart
    from the plain text rather than embedded as inline markers, which is
    fragile round-tripped through string literals.

    Each run is (starts_at_first_word, ends_at_last_word, phrase) -- a
    run touching either edge of the line might be one half of a phrase
    that wraps across the line break (e.g. "Indigenous Broadcasting and
    Media" / "Program"), which the caller stitches back together using
    those two flags rather than this function guessing across lines."""
    text = _join_words(line)

    runs = []
    current = []
    current_starts_first = False
    for i, w in enumerate(line):
        if _is_italic(w["fontname"]):
            if not current:
                current_starts_first = i == 0
            current.append(w)
        elif current:
            runs.append((current_starts_first, False, _join_words(current)))
            current = []
    if current:
        runs.append((current_starts_first, True, _join_words(current)))
    return text, runs


def extract_measure_records(pdf_path):
    """Parse a BP2/MYEFO-Appendix-A PDF into one record per measure. See
    the module docstring for the record shape and structural notes."""
    records = []
    current_section = None
    current_portfolio = None
    builder = None
    # None | "title" | "financial" | "prose"
    mode = None

    def flush():
        nonlocal builder
        if builder is not None and builder.title:
            records.append(builder.finalize())
        builder = None

    with pdfplumber.open(pdf_path) as pdf:
        for page_index, page in enumerate(pdf.pages):
            page_number = page_index + 1
            lines = _extract_lines(page)
            page_text = "\n".join(_line_text(l) for l in lines)

            for line in lines:
                text = _line_text(line)
                if not text.strip():
                    continue
                m = SECTION_MARKER_RE.match(text.strip())
                if m:
                    current_section = _canon_impact_word(m.group(1))

            if current_section is not None and any(
                FOREIGN_APPENDIX_RE.match(_line_text(l).strip()) for l in lines
            ):
                # A foreign document's own measures bundled in as a
                # trailing appendix (see FOREIGN_APPENDIX_RE) -- stop
                # entirely rather than extracting them under this file's
                # edition, where they'd collide in title with this
                # document's own genuinely different measures. Gated on
                # current_section (i.e. already past the front matter):
                # the same heading text also appears earlier as a plain
                # table-of-contents entry, which must NOT trigger this.
                flush()
                break

            if current_section is not None and any(
                NEXT_APPENDIX_RE.match(_line_text(l).strip())
                and _is_bold(l[0]["fontname"])
                and l[0]["size"] > 13
                for l in lines
            ):
                # A MYEFO's own next appendix after Appendix A -- see
                # NEXT_APPENDIX_RE -- stop entirely rather than picking
                # up its own government-wide content as phantom measures.
                # The real divider is always bold and >=15pt (observed
                # 15.00-18.00 across editions); a bold-but-small hit is
                # just this same heading text repeated on the table of
                # contents (~11pt, already excluded by the
                # current_section gate in practice, but size-gated here
                # too for safety), and a non-bold ~10pt hit is a body-
                # prose citation ("Appendix C provides further detail on
                # contingent liabilities...", "Appendix D.") -- neither
                # is a real section boundary.
                flush()
                break

            if current_section is not None and any(
                NEXT_APPENDIX_TABLE_RE.match(_line_text(l).strip()) for l in lines
            ):
                # See NEXT_APPENDIX_TABLE_RE's own docstring -- a second,
                # more reliable signal for the same "next appendix"
                # boundary NEXT_APPENDIX_RE targets above, for the one
                # edition whose own divider heading doesn't literally
                # spell out "Appendix B" in bold text.
                flush()
                break

            is_summary_page = any(
                TABLE_CAPTION_RE.match(_line_text(l).strip()) for l in lines
            )
            if is_summary_page:
                # A page belonging to the government-wide summary table
                # (Table 1/2 or Table A.1/A.2, incl. "(continued)" pages)
                # -- not an individual measure write-up. Section-marker
                # scan above still applies (a Part heading can share the
                # page with the table it introduces).
                continue

            idx = 0
            while idx < len(lines):
                line = lines[idx]
                text = _line_text(line)
                if not text.strip():
                    idx += 1
                    continue

                if current_section is None:
                    # Still in front matter (foreword, ministerial
                    # signatures, copyright notice, contents/table-of-
                    # measures-by-portfolio pages) -- some of this text is
                    # styled with the same bold weight/size as a real
                    # portfolio or measure heading, so nothing gets
                    # extracted until we've actually passed a real "Part
                    # 1: Receipt Measures"-style divider (which is a
                    # clean standalone line -- the contents page's own
                    # near-identical text always has a page number or
                    # more content trailing on the same line, so it
                    # doesn't match SECTION_MARKER_RE's end anchor).
                    idx += 1
                    continue

                first_word = line[0]
                size = first_word["size"]
                bold = _is_bold(first_word["fontname"])

                if bold and abs(size - PORTFOLIO_HEADING_SIZE) < SIZE_TOL:
                    if mode != "portfolio":
                        flush()
                        current_portfolio = text.strip()
                        mode = "portfolio"
                    else:
                        # A wrapped portfolio heading continues on the next
                        # line at the same size -- e.g. "Infrastructure,
                        # Transport, Regional Development," / "Communications
                        # and the Arts" -- accumulate rather than overwrite.
                        current_portfolio += " " + text.strip()
                    current_portfolio = normalize_portfolio_heading(current_portfolio)
                    idx += 1
                    continue

                if bold and abs(size - MEASURE_HEADING_SIZE) < SIZE_TOL:
                    if mode != "title":
                        flush()
                        builder = _MeasureBuilder(current_portfolio, current_section, page_number)
                        mode = "title"
                    builder.add_title_part(text.strip())
                    idx += 1
                    continue

                if builder is None:
                    idx += 1
                    continue

                fin_match = FIN_TABLE_HEADER_RE.match(text.strip())
                if fin_match:
                    mode = "financial"
                    is_related = 1 if fin_match.group(1) else 0
                    impact_type = _canon_impact_word(fin_match.group(2)).capitalize()
                    rows, idx = _parse_financial_table(lines, idx, impact_type, is_related)
                    builder.add_financial_rows(rows)
                    continue

                if mode == "financial":
                    # Shouldn't normally get here (the table parser
                    # consumes through its own Total row), but guard
                    # against stray table remnants by just falling
                    # through to prose handling below.
                    mode = "prose"

                # Bullet / sub-bullet / prose line.
                if _is_bullet_word(first_word):
                    text_part, italics = _line_plain_and_italics(line[1:])
                    x0 = first_word["x0"]
                    if x0 < 112:
                        builder.add_component(text_part, italics)
                    else:
                        builder.add_subcomponent(text_part, italics)
                    mode = "prose"
                    idx += 1
                    continue

                text_part, italics = _line_plain_and_italics(line)
                x0 = first_word["x0"]
                if x0 > 112:
                    builder.extend_last_component_text(text_part, italics)
                else:
                    builder.add_text_line(text_part, italics)
                mode = "prose"
                idx += 1

    flush()
    return records


def write_measure_records_sqlite(pdf_path, db_path, budget_year, paper_code, title):
    """Extract and persist to a small standalone schema (measure /
    measure_headline_financial / measure_component) -- separate from
    programs.db's measure_impacts/measure_programs, which stay PBS-
    Table-1.2-sourced; merging BP2 data into those is a distinct step."""
    records = extract_measure_records(pdf_path)

    con = sqlite3.connect(db_path)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS measure (
            id                INTEGER PRIMARY KEY,
            budget_year       TEXT NOT NULL,
            paper_code        TEXT NOT NULL,
            title             TEXT NOT NULL,
            portfolio_name    TEXT NOT NULL,
            measure_title     TEXT NOT NULL,
            document_section  TEXT NOT NULL,
            source_page       INTEGER NOT NULL,
            full_measure_text TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS measure_headline_financial (
            id                    INTEGER PRIMARY KEY,
            measure_id            INTEGER NOT NULL REFERENCES measure(id),
            impact_type           TEXT NOT NULL,
            is_related            INTEGER NOT NULL,
            department_name       TEXT NOT NULL,
            year_index            INTEGER NOT NULL,
            value_kind            TEXT NOT NULL,
            value_numeric_million REAL,
            value_raw             TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS measure_component (
            id              INTEGER PRIMARY KEY,
            measure_id      INTEGER NOT NULL REFERENCES measure(id),
            ordinal         INTEGER NOT NULL,
            level           INTEGER NOT NULL,
            marker          TEXT NOT NULL,
            parent_ordinal  INTEGER,
            text            TEXT NOT NULL
        );
        """
    )

    inserted = 0
    for rec in records:
        cur = con.execute(
            """INSERT INTO measure
               (budget_year, paper_code, title, portfolio_name, measure_title,
                document_section, source_page, full_measure_text)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                budget_year,
                paper_code,
                title,
                rec["portfolio_name"],
                rec["measure_title"],
                rec["document_section"],
                rec["source_page"],
                rec["full_measure_text"],
            ),
        )
        measure_id = cur.lastrowid
        inserted += 1

        for row in rec["headline_financials"]:
            for year_index, value in enumerate(row["values"]):
                con.execute(
                    """INSERT INTO measure_headline_financial
                       (measure_id, impact_type, is_related, department_name,
                        year_index, value_kind, value_numeric_million, value_raw)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        measure_id,
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
                """INSERT INTO measure_component
                   (measure_id, ordinal, level, marker, parent_ordinal, text)
                   VALUES (?,?,?,?,?,?)""",
                (measure_id, ordinal, c["level"], c["marker"], c["parent_ordinal"], c["text"]),
            )

    con.commit()
    con.close()
    return inserted


if __name__ == "__main__":
    import sys
    import json

    for rec in extract_measure_records(sys.argv[1]):
        print(json.dumps(rec, indent=2))
