import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./fetch", () => ({ get: vi.fn(), getBlob: vi.fn(), post: vi.fn(), put: vi.fn() }));
import { analysisApi } from "./execution-analysis";
import { get, post, put } from "./fetch";

beforeEach(() => vi.clearAllMocks());

describe("canonical analysis API", () => {
  it("serializes safe filters and preserves workspace options", () => {
    const options = { workspaceId: "team-1" };
    analysisApi.summary({ filters: { status: "completed" }, timezone: "Asia/Shanghai" }, options);
    expect(get).toHaveBeenCalledWith(
      "/execution-analysis/summary",
      { filters: '{"status":"completed"}', timezone: "Asia/Shanghai" },
      options,
    );
  });
  it("requires and forwards the fixed comparison revision without following latest", () => {
    analysisApi.getComparison("a/b", { revision: 7, limit: 50 });
    expect(get).toHaveBeenCalledWith(
      "/execution-comparisons/a%2Fb",
      { revision: 7, limit: 50 },
      undefined,
    );
  });
  it("keeps source revision separate from alignment CAS and receipt", () => {
    const payload = { request_id: "r", revision: 7, expected_revision: 2, edits: [] };
    analysisApi.align("c", payload);
    expect(post).toHaveBeenCalledWith("/execution-comparisons/c/alignments", payload, undefined);
  });
  it("updates scoped preference through fetch and explicit CAS", () => {
    const payload = { request_id: "r", expected_revision: 1, timezone: null };
    analysisApi.updatePreferences(payload, { workspaceId: "team" });
    expect(put).toHaveBeenCalledWith("/execution-analysis/preferences", payload, {
      workspaceId: "team",
    });
  });
});

it("creates and polls caller-private fixed exports through fetch", () => {
  const payload = {
    source_kind: "comparison" as const,
    request_id: "once",
    format: "csv" as const,
    comparison_id: "c",
    revision: 3,
  };
  analysisApi.createExport(payload, { workspaceId: "team" });
  expect(post).toHaveBeenCalledWith("/execution-analysis/exports", payload, {
    workspaceId: "team",
  });
  analysisApi.getExport("a/b");
  expect(get).toHaveBeenCalledWith("/execution-analysis/exports/a%2Fb", undefined, undefined);
});
