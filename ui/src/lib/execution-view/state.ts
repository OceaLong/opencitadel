/** Detail tabs shared by both presentations. IDs always identify exact attempts. */
export const WORKBENCH_PANELS = [
  "overview",
  "input-output",
  "approval",
  "artifact",
  "source",
] as const;
export type WorkbenchPanel = (typeof WORKBENCH_PANELS)[number];
export type WorkbenchSelection = {
  runId: string;
  view: "task" | "debug";
  at: string | null;
  stepId: string | null;
  panel: WorkbenchPanel | null;
  artifactId: string | null;
  version: number | null;
  citationId: string | null;
};
export function setView<T extends { view: "task" | "debug" }, V extends "task" | "debug">(
  selection: T,
  view: V,
): Omit<T, "view"> & { view: V } {
  return { ...selection, view };
}
export type SelectionAction =
  | { type: "select"; selection: WorkbenchSelection }
  | { type: "view"; view: WorkbenchSelection["view"] }
  | { type: "return-to-live" };
export function selectionReducer(
  selection: WorkbenchSelection,
  action: SelectionAction,
): WorkbenchSelection {
  switch (action.type) {
    case "select":
      return action.selection;
    case "view":
      return setView(selection, action.view);
    case "return-to-live":
      return { ...selection, at: null };
  }
}
