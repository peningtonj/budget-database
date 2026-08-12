// amount is in $'000s, matching measure_impacts.amount_thousands.
export function formatDollars(amountThousands) {
  const dollars = amountThousands * 1000;
  const abs = Math.abs(dollars);
  const sign = dollars < 0 ? "-" : "";
  if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `${sign}$${(abs / 1e3).toFixed(0)}K`;
  return `${sign}$${abs.toFixed(0)}`;
}

// PBS-style $m table cell -- one decimal, thousands separators, and a
// bare "-" for zero (the convention every official Budget Paper table
// uses for a nil figure), not "0.0".
export function formatMillionsCell(amountThousands) {
  if (!amountThousands) return "-";
  const millions = amountThousands / 1000;
  return millions.toLocaleString(undefined, {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  });
}
