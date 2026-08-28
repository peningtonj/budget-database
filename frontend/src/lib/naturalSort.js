// Program numbers ("1.1", "1.9", "1.10", "1.11", a bare "1"/"2"/"3" for a
// few agencies) are dotted numeric strings, not names -- plain string
// comparison (localeCompare, <, sort()'s own default) puts "1.10" right
// after "1.1", ahead of "1.2".."1.9", since '1' < '9' character-by-
// character. Splits on "." and compares each segment as a number
// instead, so "1.9" sorts before "1.10" the way a person reading the
// Budget papers would expect.
export function compareProgramNumbers(a, b) {
  const partsA = String(a ?? "").split(".");
  const partsB = String(b ?? "").split(".");
  const len = Math.max(partsA.length, partsB.length);
  for (let i = 0; i < len; i++) {
    const na = Number(partsA[i]);
    const nb = Number(partsB[i]);
    // A genuinely non-numeric segment (none seen in this dataset, but
    // don't crash if one ever shows up) falls back to a string compare
    // for that one segment rather than NaN swallowing the whole result.
    if (Number.isNaN(na) || Number.isNaN(nb)) {
      const sa = partsA[i] ?? "";
      const sb = partsB[i] ?? "";
      if (sa !== sb) return sa < sb ? -1 : 1;
      continue;
    }
    if (na !== nb) return na - nb;
  }
  return 0;
}
