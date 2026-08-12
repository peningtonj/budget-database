# TODO

Planned work, not yet started. `KNOWN_GAPS.md` documents limitations of
what's already built; this tracks what's coming next.

## Ingest Budget Paper No. 2 (BP2)

Goal: BP2 has the government-wide, consolidated measure text/tables --
likely the source for figures that don't appear in any individual
agency's own PBS/PAES Table 1.2 (e.g. the Australian Taxation Office's
receipt-side contribution to "Building Australia's Future - delivering
pay rises for early educators", present in the official published
measure table but absent from ATO's own 2024-25 MYEFO PBS file --
confirmed by direct inspection, not a parsing bug).

Once ingestion starts, also need to cover:

- **Financial profiles for BP2-sourced measures/agencies.** BP2 may
  supply $ figures for an agency/direction combination with no
  corresponding entry in the per-agency Table 1.2 data at all (like the
  ATO example above). Decide how these merge with the existing
  `measure_impacts` rows for the same measure_name -- same table,
  additional rows, or something else -- and how the frontend
  distinguishes "from this agency's own PBS" vs "from BP2 only" if that
  distinction matters.
- **Unknown programs/outcomes in the frontend.** BP2 measure entries may
  not resolve to a specific program_number the way Table 1.2 entries do
  (BP2 is portfolio/agency-level, not necessarily program-level), so the
  frontend's "Programs touched" and program-profile views need a graceful
  path for "this agency is impacted, but we don't know which program" --
  not just assume every impact resolves to a program the way it does
  today.
