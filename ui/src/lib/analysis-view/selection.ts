export type AnalysisSelection = {
  mode: "explicit" | "all_matching";
  runIds: string[];
  excludedIds: string[];
  details: string[];
};
export const emptySelection = (): AnalysisSelection => ({
  mode: "explicit",
  runIds: [],
  excludedIds: [],
  details: [],
});
export function toggleRun(s: AnalysisSelection, id: string, selected: boolean): AnalysisSelection {
  const update = (ids: string[], add: boolean) =>
    add ? [...new Set([...ids, id])] : ids.filter((value) => value !== id);
  s = { ...s, details: selected ? s.details : s.details.filter((value) => value !== id) };
  return s.mode === "all_matching"
    ? { ...s, excludedIds: update(s.excludedIds, !selected) }
    : { ...s, runIds: update(s.runIds, selected) };
}
export function toggleDetail(s: AnalysisSelection, id: string): AnalysisSelection {
  const eligible = !s.details.includes(id) && s.details.length < 5 ? toggleRun(s, id, true) : s;
  return {
    ...eligible,
    details: s.details.includes(id)
      ? s.details.filter((value) => value !== id)
      : s.details.length < 5
        ? [...s.details, id]
        : s.details,
  };
}
export function analysisReturn(value: string | null): string | null {
  if (!value || !value.startsWith("/analysis") || value.includes("\\")) return null;
  try {
    const url = new URL(value, "https://native.invalid");
    return url.origin === "https://native.invalid" &&
      /^\/analysis(?:\/comparisons\/[\w-]+)?$/.test(url.pathname)
      ? url.pathname + url.search + url.hash
      : null;
  } catch {
    return null;
  }
}
