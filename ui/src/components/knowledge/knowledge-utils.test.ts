import { describe, expect, it } from "vitest";

import { translate } from "@/i18n/translate";

import {
  appendDocumentsPage,
  canStartAsk,
  formatIngestStreamError,
  groupFileIdsBySourceType,
  inferSourceType,
  isChatSendBlocked,
  isStaleRequest,
  parseKbDocHref,
} from "./knowledge-utils";
import type { KnowledgeDocument } from "@/lib/api/types";

describe("parseKbDocHref", () => {
  it("parses doc id and page", () => {
    expect(parseKbDocHref("kbdoc://doc-1?page=3&chunk=c1")).toEqual({
      docId: "doc-1",
      page: 3,
      chunkId: "c1",
    });
  });

  it("returns null for non-kb links", () => {
    expect(parseKbDocHref("https://example.com")).toBeNull();
  });
});

describe("inferSourceType", () => {
  it("detects zip archives", () => {
    expect(inferSourceType("bundle.ZIP")).toBe("zip");
    expect(inferSourceType("readme.pdf")).toBe("upload");
  });
});

describe("request race guards", () => {
  it("marks stale tokens", () => {
    expect(isStaleRequest(1, 2)).toBe(true);
    expect(isStaleRequest(2, 2)).toBe(false);
  });

  it("blocks chat until session is ready", () => {
    expect(isChatSendBlocked(null, false)).toBe(true);
    expect(isChatSendBlocked("sess-1", true)).toBe(true);
    expect(isChatSendBlocked("sess-1", false)).toBe(false);
  });
});

describe("groupFileIdsBySourceType", () => {
  it("groups mixed uploads for separate submission", () => {
    const grouped = groupFileIdsBySourceType([
      { id: "f1", sourceType: "upload" },
      { id: "f2", sourceType: "zip" },
      { id: "f3", sourceType: "upload" },
    ]);
    expect(grouped.upload).toEqual(["f1", "f3"]);
    expect(grouped.zip).toEqual(["f2"]);
  });
});

describe("formatIngestStreamError", () => {
  it("returns server error when present", () => {
    expect(formatIngestStreamError({ error: "索引超时" })).toBe("索引超时");
  });

  it("falls back to default message", () => {
    expect(formatIngestStreamError({})).toBe(
      translate("knowledge.indexFailed", undefined, "en"),
    );
  });
});

const doc = (id: string): KnowledgeDocument =>
  ({ id, kb_id: "kb1", title: id, source_type: "upload", mime: "", page_count: 0, status: "ready" }) as KnowledgeDocument;

describe("canStartAsk", () => {
  it("allows when ready_doc_count > 0", () => {
    expect(canStartAsk({ ready_doc_count: 1 })).toBe(true);
  });
  it("blocks when ready_doc_count is 0 or missing", () => {
    expect(canStartAsk({ ready_doc_count: 0 })).toBe(false);
    expect(canStartAsk({})).toBe(false);
  });
});

describe("appendDocumentsPage", () => {
  it("appends new docs and dedupes by id", () => {
    const merged = appendDocumentsPage([doc("a"), doc("b")], [doc("b"), doc("c")]);
    expect(merged.map((d) => d.id)).toEqual(["a", "b", "c"]);
  });
  it("handles empty previous list", () => {
    expect(appendDocumentsPage([], [doc("a")]).map((d) => d.id)).toEqual(["a"]);
  });
});
