import { expect, test } from "vitest";

import { selectionReducer, setView } from "./state";
import { parseSelection, removeInitialMessage, serializeSelection } from "./url-state";

const selection = {
  runId: "r",
  view: "task" as const,
  at: "opaque+/=_-边界",
  stepId: "attempt/2",
  panel: "artifact" as const,
  artifactId: "a",
  version: 2,
  citationId: null,
};
test("switches views without mutating exact playback identity", () => {
  expect(setView(selection, "debug")).toEqual({ ...selection, view: "debug" });
  expect(selection.view).toBe("task");
  expect(selectionReducer(selection, { type: "return-to-live" })).toEqual({
    ...selection,
    at: null,
  });
});
test("round trips opaque locations and preserves unrelated query keys", () => {
  const query = serializeSelection(selection, "init=hello&other=keep");
  expect(parseSelection(query).selection).toEqual(selection);
  expect(new URLSearchParams(query).get("other")).toBe("keep");
  expect(removeInitialMessage(`/sessions/s?${query}#anchor`)).toBe(
    `/sessions/s?${query.replace("init=hello&", "")}#anchor`,
  );
});
test.each(["0", "-1", "1.5", "9007199254740992", "wrong"])(
  "recovers independent fields from invalid version %s",
  (version) => {
    const result = parseSelection(`run=r&at=opaque&step=s&artifact=a&version=${version}`);
    expect(result.selection).toMatchObject({
      runId: "r",
      at: "opaque",
      stepId: "s",
      artifactId: "a",
      version: null,
    });
    expect(result.issues).toContain("version");
  },
);
test("rejects ambiguous duplicates and conflicting leaves without losing boundary", () => {
  const result = parseSelection(
    "run=r&at=opaque&step=s&step=t&view=no&panel=secret&artifact=a&citation=c&version=2",
  );
  expect(result.selection).toEqual({
    runId: "r",
    at: "opaque",
    stepId: null,
    view: "task",
    panel: null,
    artifactId: "a",
    citationId: null,
    version: 2,
  });
  expect(parseSelection("run=&at=a&version=2").selection).toMatchObject({
    runId: "",
    at: "a",
    version: null,
  });
  expect(parseSelection("run=r&run=s&at=a").selection.runId).toBe("");
});

test("layout storage filters unknown business fields and isolates both identities", async () => {
  const { layoutStorageKey, readLayout, saveLayout } = await import("./layout-preferences");
  const values = new Map<string, string>();
  const storage = {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => {
      values.set(key, value);
    },
  };
  saveLayout(
    { userId: "u1", workspaceId: "t1" },
    {
      view: "debug",
      detailWidth: 420,
      conversationOpen: false,
      body: "secret",
      at: "cursor",
    } as never,
    storage,
  );
  expect(readLayout({ userId: "u1", workspaceId: "t1" }, storage)).toEqual({
    view: "debug",
    detailWidth: 420,
    conversationOpen: false,
  });
  expect(readLayout({ userId: "u1", workspaceId: "t2" }, storage)).toEqual({});
  expect(readLayout({ userId: "u2", workspaceId: "t1" }, storage)).toEqual({});
  expect(values.get(layoutStorageKey({ userId: "u1", workspaceId: "t1" }))).not.toContain("secret");
  expect(values.get(layoutStorageKey({ userId: "u1", workspaceId: "t1" }))).not.toContain("cursor");
});
