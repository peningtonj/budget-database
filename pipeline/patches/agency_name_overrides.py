"""
One-off overrides for clean_agency()'s (build_db.py) filename-derived
agency name, for filenames whose boilerplate is specific to that one
document rather than a repeated pattern across the corpus.

Contrast with AGENCY_STRIP in build_db.py: that regex strips boilerplate
seen across many files ("OCT", "clean", "Budget", ...), so it's worth the
shared cost of every filename running through it. A quirk seen in exactly
one file doesn't earn a place in that shared regex -- it belongs here
instead, keyed by exact basename (unique enough across the ~2,000 source
files) so it's obvious which single document each entry targets.
"""

# DVA's own 2023-24 MYEFO PAES workbook was filed with a "(for Finance)"
# parenthetical noting who it was prepared for -- unlike every other
# agency's PAES filename that MYEFO, and unlike DVA's own filings in every
# other edition. Confirmed this is DVA's normal PAES submission, not a
# distinct dataset, so it should carry the same "DVA" identity as those.
#
# Two more of DVA's own MYEFO PAES filings carry a similar one-off
# descriptive suffix rather than just the agency name: 2019-20's notes it
# was prepared "for web publishing", and 2021-22's calls itself the
# "Financial Tables" (that edition's actual content, not a distinct
# agency). Both confirmed to be DVA's normal PAES submission for that
# edition.
OVERRIDES = {
    "2023-24 PAES tables - DVA (for Finance).xlsx": "DVA",
    "DVA PAES 2019-20 - for web publishing.xlsx": "DVA",
    "2021-22 DVA PAES - Financial Tables.xlsx": "DVA",
}
