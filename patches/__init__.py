"""
Document-specific patches for source-workbook defects that generic,
content-based parsing (parse_pbs.py) cannot correctly resolve on its own.

Most PBS workbooks parse cleanly under parse_pbs.py's content-based rules.
A small number of specific editions have a genuine defect or quirk baked
into that one document -- a mid-year program restructure that leaves a
column of zeros, a table that mislabels a row, etc. Fixing those belongs
here, not in parse_pbs.py: parse_pbs.py's job is to correctly read what a
*normal* workbook says; a patch's job is to know that *this one file* is
lying about something and to say exactly why.

Each patch is a module in this package that declares:

    TARGET_FILE = "<edition>/<portfolio folder>/<workbook filename>"
        Relative to data/pbs/Budget, exactly matching the `rel` path
        build_db.py computes for each file. This is what makes the patch's
        scope self-evident to a reader -- open the file, see the exact
        document it touches.

    def apply(records: list[dict]) -> list[dict]:
        Takes parse_workbook()'s full, un-deduped record list for that one
        workbook and returns a replacement list. Called after parsing, before
        build_db.py's own first-wins conflict resolution, so a patch can
        still rely on that final safety net for anything it doesn't touch.

Add a new patch by dropping in a new module with those two names -- nothing
else needs to be registered or imported anywhere.
"""
import glob
import importlib
import os

_PATCHES = None


def _load_patches():
    global _PATCHES
    if _PATCHES is not None:
        return _PATCHES
    patches = {}
    package_dir = os.path.dirname(__file__)
    for path in sorted(glob.glob(os.path.join(package_dir, "*.py"))):
        stem = os.path.splitext(os.path.basename(path))[0]
        if stem == "__init__":
            continue
        mod = importlib.import_module(f"{__name__}.{stem}")
        target = getattr(mod, "TARGET_FILE", None)
        if target is None:
            continue
        patches[target] = mod
    _PATCHES = patches
    return patches


def apply_patches(rel_path, records):
    """Run any patch registered for `rel_path` (as computed by build_db.py's
    `os.path.relpath(path, BUDGET_DIR)`) over that workbook's records."""
    mod = _load_patches().get(rel_path)
    if mod is None:
        return records
    return mod.apply(records)
