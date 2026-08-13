"""
Stable 8-digit id for a (measure_name, edition) pair, for a short
shareable URL (?m=<id>) instead of the full name+edition in the query
string. Shared between build_measures_db.py (measure_impacts/
measure_programs, 3 editions with full PBS financial data) and
build_bp2_db.py (measure_text, 21 editions of BP2 write-ups) so the
exact same key produces the exact same id regardless of which build
script computed it -- a measure covered by both sources doesn't end up
with two different ids depending on which table you looked it up in.
"""
import hashlib


def _measure_id(measure_name, edition, salt=0):
    """Derived from a hash of the pair itself, not an autoincrement
    counter, so it stays the same across rebuilds regardless of file
    iteration order -- a previously-shared link keeps working even after
    re-ingesting. `salt` only matters if two different (measure_name,
    edition) pairs happen to collide (see MeasureIdAssigner, which
    detects this and bumps salt deterministically until unique -- with
    a few thousand measures in a 10**8 id space this is expected to
    almost never fire, but it's handled rather than assumed away)."""
    digest = hashlib.sha256(f"{measure_name}\x1f{edition}\x1f{salt}".encode("utf-8")).hexdigest()
    return str(int(digest[:12], 16) % 10**8).zfill(8)


class MeasureIdAssigner:
    """Caches one id per (measure_name, edition) so every row for the
    same measure gets the identical id, and detects/resolves collisions
    within its own run. Encounter order needs to be deterministic (a
    fixed EDITIONS list; iter_files() walking sorted() directories/
    files) for a given source tree to always produce the same ids,
    collision resolution included.

    Each build script (build_measures_db.py, build_bp2_db.py) uses its
    own instance/run -- collision resolution is only guaranteed
    consistent *within* one run, not coordinated across the two. A
    (measure_name, edition) key covered by both sources gets the same
    id in the near-certain case neither run's own key set collides on
    it; an actual cross-script id mismatch would require a hash
    collision inside at least one run, empirically not observed even
    with several thousand keys."""

    def __init__(self):
        self._ids = {}
        self._used = set()
        self.collisions_resolved = 0

    def get(self, measure_name, edition):
        key = (measure_name, edition)
        if key in self._ids:
            return self._ids[key]
        salt = 0
        measure_id = _measure_id(measure_name, edition, salt=salt)
        while measure_id in self._used:
            salt += 1
            measure_id = _measure_id(measure_name, edition, salt=salt)
        if salt:
            self.collisions_resolved += 1
        self._used.add(measure_id)
        self._ids[key] = measure_id
        return measure_id

    def count(self):
        return len(self._ids)

    def seed_known(self, rows):
        """Pre-loads (measure_name, edition, measure_id) triples already
        assigned outside this run -- e.g. by an earlier full build, when
        this run only re-ingests a subset of editions/files (see
        build_measures_db.py's --edition/--file). Populates BOTH _ids
        and _used, not just _used: a measure spanning several files of
        the same edition (the common case -- most measures touch more
        than one agency) means get() can be called for the exact same
        key this run even though only ONE of its several source files
        is being reprocessed. Without the _ids entry too, that call
        would recompute the hash fresh, find its own already-correct id
        sitting in _used (seeded from the *other*, untouched file's
        row), read that as "collision against a different key", and
        salt-bump to a NEW id -- leaving the measure split across two
        different ids depending on which row you look at (confirmed:
        this exact split happened before _ids seeding was added here).
        A straight cache hit for an already-known key avoids ever
        reaching the hash/collision-check path for it at all."""
        for measure_name, edition, measure_id in rows:
            self._ids[(measure_name, edition)] = measure_id
            self._used.add(measure_id)
