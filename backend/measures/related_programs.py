"""
Curated groups of programs that are almost certainly the same real-world
function under a succession of different legal names -- a genuine
machinery-of-government reform (an entity abolished and replaced, or
merged with another), not the accidental spelling/typo drift
parse_pbs.py's clean_program_name() already fixes at ingest time.

This is deliberately NOT a data-cleaning table: each entry keeps its own
distinct (program_name, portfolio) identity everywhere else in the app
(program_profile(), program_estimate_history(), the audit page) exactly
as program_profile()'s own docstring already argues a real MoG change
should. What this module adds is a *suggestion* -- surfaced only on
ProgramDeepDivePage, only for the specific programs listed here -- to view
a likely-related program's history alongside the one currently open,
letting a human judge whether they really are the same underlying
function before treating them as one continuous story.

Why curated rather than detected: name similarity alone can't reliably
tell a real succession ("Administrative Appeals Tribunal" -> "...and
Immigration Assessment Authority" -> "Administrative Review Tribunal",
confirmed via KNOWN_GAPS-style verification: chronologically adjacent,
non-overlapping edition ranges, same portfolio throughout) apart from two
programs that just happen to read alike (see parse_pbs.py's own
_KNOWN_PROGRAM_NAME_TYPOS docstring: "Program Support for Outcome 1" vs
"...Outcome 2", "ABC" vs "SBS General Operational Activities" -- high
text similarity, completely unrelated). Add a new group here only after
checking, the same way: do the edition ranges actually meet cleanly with
no overlap, and is there a real, citable reason (an abolition, a merger)
for the name to have changed.
"""

RELATED_PROGRAM_GROUPS = [
    {
        "note": (
            "The Administrative Appeals Tribunal merged with the Immigration "
            "Assessment Authority in 2019, then was abolished and replaced by "
            "the Administrative Review Tribunal from 1 July 2024."
        ),
        "programs": [
            {"program_name": "Administrative Appeals Tribunal", "portfolio": "Attorney-General's"},
            {
                "program_name": "Administrative Appeals Tribunal and Immigration Assessment Authority",
                "portfolio": "Attorney-General's",
            },
            {"program_name": "Administrative Review Tribunal", "portfolio": "Attorney-General's"},
        ],
    },
]


def find_related(program_name, portfolio):
    """Every other program in the same curated group as (program_name,
    portfolio), plus that group's note -- or (None, []) if this program
    isn't part of any known group."""
    for group in RELATED_PROGRAM_GROUPS:
        members = group["programs"]
        if any(m["program_name"] == program_name and m["portfolio"] == portfolio for m in members):
            others = [
                m for m in members
                if not (m["program_name"] == program_name and m["portfolio"] == portfolio)
            ]
            return group["note"], others
    return None, []
