// measure_programs has one row per (program, funding channel): the same
// program often has a separate "Administered payment" line and its own
// "Departmental payment" line in the source sheet (the department's own
// running cost to administer the measure, alongside the money the
// measure actually delivers) -- both genuinely touch the same program,
// so merge them into one row with both channels tagged, rather than
// showing what looks like an unexplained duplicate. Then group by
// agency -- a flat one-row-per-program table reads as a wall of
// repeated agency names once a measure touches more than one; grouping
// makes "which agencies, and what does each one touch" scannable.
//
// `programs` rows may optionally carry `_measureId`/`_measureName`
// (tagged on by the caller before grouping, e.g. CombinedProgramsTouched
// flatMapping several measures' own program lists together) -- when
// present, each merged row also accumulates a `touchedBy` set of the
// distinct measures that touch it, so a program shared across several
// selected measures can show that instead of appearing once per measure.
// For a single-measure caller (ProgramsTouched), every row's `touchedBy`
// is naturally empty/size-1 and simply isn't rendered.
export function groupByAgency(programs) {
  const byProgramKey = new Map();
  for (const p of programs) {
    const key = [p.agency, p.direction, p.program_number, p.program_name].join("|");
    if (!byProgramKey.has(key)) {
      byProgramKey.set(key, { ...p, channels: new Set(), touchedBy: new Map() });
    }
    const row = byProgramKey.get(key);
    row.channels.add(p.is_departmental ? "Departmental" : "Administered");
    if (p._measureId != null) {
      row.touchedBy.set(p._measureId, p._measureName);
    }
  }
  const merged = [...byProgramKey.values()].map((p) => ({
    ...p,
    channels: [...p.channels].sort(),
    touchedBy: [...p.touchedBy].map(([measure_id, measure_name]) => ({ measure_id, measure_name })),
  }));

  const byAgency = new Map();
  for (const p of merged) {
    if (!byAgency.has(p.agency)) {
      byAgency.set(p.agency, { agency: p.agency, portfolio: p.portfolio, programs: [] });
    }
    byAgency.get(p.agency).programs.push(p);
  }
  return [...byAgency.values()].sort((a, b) => a.agency.localeCompare(b.agency));
}
