import { afterEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  get: vi.fn(() => Promise.resolve({ runs: [] })),
}));

vi.mock("./fetch", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./fetch")>();
  return { ...actual, get: mocks.get };
});

import { scheduledJobsApi } from "./scheduled-jobs";

describe("scheduledJobsApi.listRuns", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("requests the runs endpoint with pagination params", async () => {
    await scheduledJobsApi.listRuns("job-1", { limit: 20, offset: 40 });
    expect(mocks.get).toHaveBeenCalledWith("/scheduled-jobs/job-1/runs", { limit: 20, offset: 40 });
  });

  it("omits the query object when no pagination is provided", async () => {
    await scheduledJobsApi.listRuns("job-1");
    expect(mocks.get).toHaveBeenCalledWith("/scheduled-jobs/job-1/runs", undefined);
  });

  it("passes only the provided pagination field", async () => {
    await scheduledJobsApi.listRuns("job-1", { offset: 10 });
    expect(mocks.get).toHaveBeenCalledWith("/scheduled-jobs/job-1/runs", { offset: 10 });
  });
});
