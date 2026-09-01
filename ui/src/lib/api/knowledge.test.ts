import { afterEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  get: vi.fn(() => Promise.resolve({ knowledge_bases: [] })),
  post: vi.fn(() => Promise.resolve()),
  del: vi.fn(() => Promise.resolve()),
}));

vi.mock("./fetch", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./fetch")>();
  return { ...actual, get: mocks.get, post: mocks.post, del: mocks.del };
});

import { knowledgeApi } from "./knowledge";

describe("knowledgeApi recycle bin", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("lists deleted knowledge bases from the recycle-bin endpoint", async () => {
    await knowledgeApi.listDeleted();
    expect(mocks.get).toHaveBeenCalledWith("/knowledge-bases/deleted");
  });

  it("restores a knowledge base via POST /restore", async () => {
    await knowledgeApi.restore("kb1");
    expect(mocks.post).toHaveBeenCalledWith("/knowledge-bases/kb1/restore", {});
  });

  it("purges a knowledge base via DELETE /purge", async () => {
    await knowledgeApi.purge("kb1");
    expect(mocks.del).toHaveBeenCalledWith("/knowledge-bases/kb1/purge");
  });
});
