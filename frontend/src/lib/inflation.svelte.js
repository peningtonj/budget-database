// Whole-economy GDP deflator index, one value per fiscal year, used to
// express every dollar figure in the app in CURRENT_FY-equivalent terms
// when the toggle is on. Financial-year figures (Jul-Jun) are derived
// from World Bank's NY.GDP.DEFL.ZS calendar-year series for Australia
// (itself sourced from the same ABS/UN National Accounts data Treasury
// uses), averaging the two calendar years each fiscal year spans -- the
// standard approximation for a mid-year-split FY average absent ABS's
// own quarterly series. 2025-26 onward has no realised figure yet, so
// those years are chained forward from Budget Paper No. 1 2026-27,
// Statement 1, Table 1.1's own Real GDP / Nominal GDP forecasts (the
// deflator isn't published as its own row there, but is exactly what's
// implied: growth = (1 + nominal%) / (1 + real%) - 1).
export const DEFLATOR_INDEX = {
  "2016-17": 75.8419,
  "2017-18": 77.9124,
  "2018-19": 79.9918,
  "2019-20": 82.1176,
  "2020-21": 84.1449,
  "2021-22": 88.4878,
  "2022-23": 94.5197,
  "2023-24": 98.7385,
  "2024-25": 101.1538,
  "2025-26": 105.6056,
  "2026-27": 108.2003,
  "2027-28": 108.7294,
  "2028-29": 111.1162,
  "2029-30": 114.0973,
};

// The "current dollars" every other year gets expressed in terms of --
// the year the app's own LATEST_BUDGET_EDITION targets, not simply
// "whichever year is highest" (the DB's data runs forward estimates all
// the way to 2029-30, which are themselves nominal future-year dollars,
// not "today").
export const CURRENT_FY = "2026-27";

let enabled = $state(false);

export function isInflationAdjustEnabled() {
  return enabled;
}

export function toggleInflationAdjust() {
  enabled = !enabled;
}

// Returns amountThousands unchanged when the toggle is off, the fiscal
// year is outside the deflator table, or the amount is falsy -- the
// last so a `?? 0`/undefined placeholder used for "no data" in a table
// cell doesn't quietly become a real zero-dollar figure.
export function adjustAmount(amountThousands, fiscalYear) {
  if (!enabled || !amountThousands) return amountThousands;
  const base = DEFLATOR_INDEX[CURRENT_FY];
  const idx = DEFLATOR_INDEX[fiscalYear];
  if (!idx) return amountThousands;
  return (amountThousands * base) / idx;
}
