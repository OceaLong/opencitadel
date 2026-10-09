import { expect, test } from "vitest";

import { readDiffDocument } from "./diff";
test("diff chunks are one bounded JSON document, incomplete chunks cannot prove equality", () => {
  const raw = JSON.stringify({
    format: "text",
    left: { artifact_id: "a" },
    right: { artifact_id: "b" },
    diff: {
      content_changed: null,
      complete: false,
      reason: "input_limit",
      content: "",
      operations: [],
    },
  });
  expect(readDiffDocument([raw.slice(0, 12), raw.slice(12)]).diff.complete).toBe(false);
  expect(() => readDiffDocument([raw.slice(0, 12)])).toThrow();
  expect(() => readDiffDocument(Array(17).fill(""))).toThrow();
});
