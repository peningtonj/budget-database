// Shared CSV-download helper for chart data. Every chart in this app
// ends up with the same underlying shape once built -- a set of fiscal
// years (columns) and a set of named series, each an array of points
// carrying fiscal_year + amount_thousands -- so one small function turns
// that into CSV rows and triggers the download, rather than repeating
// the same Blob/anchor boilerplate in every chart component.

function csvCell(value) {
  const s = String(value ?? "");
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function downloadCsv(filename, rows) {
  const csv = rows.map((row) => row.map(csvCell).join(",")).join("\r\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename.toLowerCase().endsWith(".csv") ? filename : `${filename}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// series: [{label, points: [{fiscal_year, amount_thousands}]}] -- exactly
// the shape every chart's own buildChart() already produces per line, so
// callers mostly just relabel their own `lines`/`points` arrays into this
// before passing them here. One row per series, one column per fiscal
// year, in the same $'000 unit (and same inflation-adjustment state,
// since these points are the chart's own already-adjusted ones) the
// chart itself is showing.
export function seriesToCsvRows(fiscalYears, series, { adjustedNote = null } = {}) {
  const header = ["", ...fiscalYears.map((fy) => `${fy} ($'000)`)];
  const body = series.map((s) => {
    const byYear = new Map(s.points.map((p) => [p.fiscal_year, p.amount_thousands]));
    return [s.label, ...fiscalYears.map((fy) => (byYear.has(fy) ? byYear.get(fy) : ""))];
  });
  const rows = [header, ...body];
  return adjustedNote ? [[adjustedNote], [], ...rows] : rows;
}

// A safe filename component from arbitrary label text (program/measure/
// agency names routinely contain "/", ",", etc.) -- collapse anything
// that isn't alphanumeric/space/hyphen into a single hyphen.
export function slugForFilename(text) {
  return String(text ?? "")
    .trim()
    .replace(/[^a-z0-9]+/gi, "-")
    .replace(/^-+|-+$/g, "")
    .toLowerCase();
}
