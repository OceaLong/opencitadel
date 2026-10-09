import { expect, test } from "vitest";

import { sourceTarget } from "./source-target";

test("citation uses its original immutable revision and chunk", () => {
  expect(
    sourceTarget({
      version_id: "v1",
      document_revision_id: "r1",
      doc_id: "d",
      chunk_id: "c",
      page_no: 2,
    }),
  ).toEqual({ versionId: "v1", revisionId: "r1", docId: "d", chunkId: "c", page: 2 });
});
test("missing canonical identities stay missing without a current pin", () => {
  expect(sourceTarget({ version_id: null, doc_id: "d" })).toEqual({
    versionId: null,
    revisionId: null,
    docId: "d",
    chunkId: null,
    page: null,
  });
});
