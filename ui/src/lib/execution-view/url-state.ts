import { WORKBENCH_PANELS, type WorkbenchPanel, type WorkbenchSelection } from "./state";

const KEYS = ["run", "view", "at", "step", "panel", "artifact", "version", "citation"] as const;
export function parseSelection(search: string | URLSearchParams): {
  selection: WorkbenchSelection;
  issues: string[];
} {
  const params = new URLSearchParams(search);
  const issues: string[] = [];
  const read = (key: string) => {
    const values = params.getAll(key);
    if (values.length > 1) {
      issues.push(key);
      return null;
    }
    return values[0]?.trim() ? values[0] : null;
  };
  const runId = read("run") ?? "";
  const view = read("view");
  if (view && view !== "task" && view !== "debug") issues.push("view");
  const at = read("at");
  const stepId = read("step");
  const panel = read("panel");
  const validPanel = WORKBENCH_PANELS.includes(panel as WorkbenchPanel);
  if (panel && !validPanel) issues.push("panel");
  const artifactId = read("artifact");
  const rawVersion = read("version");
  const parsedVersion = rawVersion && /^[1-9]\d*$/.test(rawVersion) ? Number(rawVersion) : null;
  const version = artifactId && Number.isSafeInteger(parsedVersion) ? parsedVersion : null;
  if (rawVersion && version === null) issues.push("version");
  let citationId = read("citation");
  if (citationId && artifactId) {
    citationId = null;
    issues.push("citation");
  }
  return {
    selection: {
      runId,
      view: view === "debug" ? "debug" : "task",
      at,
      stepId,
      panel: validPanel ? (panel as WorkbenchPanel) : null,
      artifactId,
      version,
      citationId,
    },
    issues,
  };
}
export function serializeSelection(
  selection: WorkbenchSelection,
  existing: string | URLSearchParams = "",
): string {
  const params = new URLSearchParams(existing);
  for (const key of KEYS) params.delete(key);
  const values = [
    selection.runId,
    selection.view,
    selection.at,
    selection.stepId,
    selection.panel,
    selection.artifactId,
    selection.version,
    selection.citationId,
  ];
  KEYS.forEach((key, index) => {
    const value = values[index];
    if (value !== null && value !== "") params.set(key, String(value));
  });
  return params.toString();
}
/** Remove the consumed bootstrap message only; preserve current location and hash. */
export function removeInitialMessage(href: string): string {
  const url = new URL(href, "https://workbench.invalid");
  url.searchParams.delete("init");
  return `${url.pathname}${url.search}${url.hash}`;
}
