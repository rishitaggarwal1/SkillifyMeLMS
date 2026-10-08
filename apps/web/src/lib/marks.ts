/** Exact hundredths for author validation and frozen grade previews. The API grades. */
export function cents(value: string | number): bigint | null {
  const text = String(value).trim();
  if (!/^\d+(?:\.\d{1,2})?$/.test(text)) return null;
  const [whole, fraction = ""] = text.split(".");
  return BigInt(whole!) * 100n + BigInt(fraction.padEnd(2, "0"));
}

export function marks(value: bigint): string {
  return String(value / 100n) + "." + String(value % 100n).padStart(2, "0");
}

export function gradePreview(values: (string | number)[], percent: string | number) {
  const scores = values.map(cents);
  const rate = cents(percent);
  if (scores.some((s) => s === null) || rate === null || rate > 10000n) return null;
  const raw = scores.reduce<bigint>((sum, v) => sum + v!, 0n);
  const penalty = (raw * rate + 5000n) / 10000n;
  return {
    raw_score: marks(raw),
    penalty_percent: marks(rate),
    penalty_marks: marks(penalty),
    score: marks(raw - penalty),
  };
}
