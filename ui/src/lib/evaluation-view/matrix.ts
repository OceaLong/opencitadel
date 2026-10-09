/** Missing evaluation observations are never structural zeroes. */
export function numericScore(value: number | null): string | null {
  return value === null ? null : String(value);
}
export function equalCaseValues(
  rows: readonly { caseId: string; value: number | null }[],
): number[] {
  const cases = new Map<string, number[]>();
  for (const row of rows)
    if (row.value !== null) cases.set(row.caseId, [...(cases.get(row.caseId) ?? []), row.value]);
  return [...cases.values()].map((values) => values.reduce((a, b) => a + b, 0) / values.length);
}
export function summarizeDistribution(values: readonly number[]) {
  if (values.length < 5) return null;
  const ordered = [...values].sort((a, b) => a - b);
  const quantile = (p: number) => {
    const index = (ordered.length - 1) * p;
    const lo = Math.floor(index);
    return ordered[lo] + (ordered[Math.ceil(index)] - ordered[lo]) * (index - lo);
  };
  return {
    min: ordered[0],
    q1: quantile(0.25),
    median: quantile(0.5),
    q3: quantile(0.75),
    max: ordered.at(-1)!,
    n: ordered.length,
  };
}
