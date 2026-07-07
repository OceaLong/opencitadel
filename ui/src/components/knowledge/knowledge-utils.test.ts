import { describe, expect, it } from "vitest";

import { translate } from "@/i18n/translate";

import {
  formatIngestStreamError,
  groupFileIdsBySourceType,
  inferSourceType,
  isChatSendBlocked,
  isStaleRequest,
  parseKbDocHref,
} from "./knowledge-utils";

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
