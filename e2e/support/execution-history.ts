export type HistoricalView = {
  at: string;
  approvals: Array<{ decision: string | null; status: string | null }>;
  artifacts: unknown[];
  run: { status: string; public_summary: string | null };
};
/** Input order must be the server timeline's observed order; opaque strings are never sorted. */
export function selectHistoryCuts<T extends HistoricalView>(views: T[]) {
  const before = views.find(
    (v) =>
      v.approvals.length === 0 &&
      v.artifacts.length === 0 &&
      v.run.public_summary === null,
  );
  const pending = views.find(
    (v) =>
      v.run.status === "waiting" &&
      v.approvals.some((a) => a.status === "waiting" && a.decision === null) &&
      v.artifacts.length === 0 &&
      v.run.public_summary === null,
  );
  const decided = views.find(
    (v) =>
      v.approvals.some((a) => a.decision === "approved") &&
      v.artifacts.length === 0 &&
      v.run.public_summary === null,
  );
  const produced = views.find(
    (v) =>
      v.run.status === "completed" &&
      v.artifacts.length > 0 &&
      v.approvals.some((a) => a.decision === "approved"),
  );
  if (
    !before ||
    !pending ||
    !decided ||
    !produced ||
    !(
      views.indexOf(before) < views.indexOf(pending) &&
      views.indexOf(pending) < views.indexOf(decided) &&
      views.indexOf(decided) < views.indexOf(produced)
    )
  )
    throw new Error(
      "required historical boundary absent or future facts visible",
    );
  return { before, pending, decided, produced };
}
