import { expect, test } from "vitest";

import { canSeekTime, reconcileVisibleSelection, visibleArtifactKey } from "./playback-selection";
import { parseSelection } from "./url-state";

test("future version is not retained by selection", () => {
  expect(visibleArtifactKey("a", 2, new Set(["a:1"]))).toBeNull();
});
test("authoritatively absent step clears dependent selection", () => {
  const selection = parseSelection("run=r&step=s&artifact=a&version=2&panel=artifact").selection;
  expect(reconcileVisibleSelection(selection, new Set(), new Set())).toMatchObject({
    stepId: null,
    artifactId: null,
    version: null,
    panel: null,
  });
});
test("unknown visibility never erases a paged-out deep selection", () => {
  const selection = parseSelection("run=r&step=s&artifact=a&version=2").selection;
  expect(reconcileVisibleSelection(selection, null, null)).toEqual(selection);
});
test("known missing and unknown coverage cannot be scrubbed", () => {
  expect(
    canSeekTime("2026-01-01T00:00:05Z", {
      state: "partial",
      missing_fields: [],
      missing_intervals: [
        { start: "2026-01-01T00:00:04Z", end: "2026-01-01T00:00:06Z", reason: "retained" },
      ],
    }),
  ).toBe(false);
  expect(
    canSeekTime("2026-01-01T00:00:05Z", {
      state: "partial",
      missing_fields: [],
      missing_intervals: [{ start: null, end: null, reason: "retained" }],
    }),
  ).toBe(false);
});
