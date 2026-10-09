import type { RunView } from "@/lib/api/types/execution-view";

import type { WorkbenchSelection } from "./state";

export function visibleArtifactKey(
  id: string,
  version: number,
  visible: Set<string>,
): string | null {
  const key = `${id}:${version}`;
  return visible.has(key) ? key : null;
}
/** null means existence has not been established, including partial page sets. */
export function reconcileVisibleSelection(
  selection: WorkbenchSelection,
  visibleStepIds: Set<string> | null,
  visibleArtifactVersions: Set<string> | null,
): WorkbenchSelection {
  if (selection.stepId && visibleStepIds && !visibleStepIds.has(selection.stepId))
    return {
      ...selection,
      stepId: null,
      panel: null,
      artifactId: null,
      version: null,
      citationId: null,
    };
  if (
    selection.artifactId &&
    selection.version !== null &&
    visibleArtifactVersions &&
    !visibleArtifactKey(selection.artifactId, selection.version, visibleArtifactVersions)
  )
    return {
      ...selection,
      artifactId: null,
      version: null,
      panel: selection.panel === "artifact" ? null : selection.panel,
    };
  return selection;
}
export function canSeekTime(
  time: string,
  coverage: RunView["completeness"] | null | undefined,
): boolean {
  const target = Date.parse(time);
  if (
    !Number.isFinite(target) ||
    !coverage ||
    (coverage.state !== "complete" && coverage.state !== "partial")
  )
    return false;
  return !coverage.missing_intervals.some(
    (gap) =>
      !gap.start || !gap.end || (target >= Date.parse(gap.start) && target <= Date.parse(gap.end)),
  );
}
