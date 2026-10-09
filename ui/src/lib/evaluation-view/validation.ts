export function canApplyImport(errorCount: number, revisionMatches: boolean): boolean {
  return errorCount === 0 && revisionMatches;
}
export function matrixQuantity(cases: number, configs: number, repeat: number): number | null {
  if (
    ![cases, configs, repeat].every(Number.isInteger) ||
    cases < 1 ||
    cases > 1000 ||
    configs < 1 ||
    configs > 5 ||
    repeat < 1 ||
    repeat > 5
  )
    return null;
  const quantity = cases * configs * repeat;
  return quantity <= 5000 ? quantity : null;
}
