import { expect, test } from "vitest";

import { ApiError } from "@/lib/api/fetch";

import { analysisFailure } from "./failure";
test("typed export failures keep expiration capacity and quota distinct from source invalidation", () => {
  expect(analysisFailure(new ApiError(410, "", { code: "export_expired" }))).toBe("expired");
  expect(analysisFailure(new ApiError(413, "", { code: "export_capacity_exceeded" }))).toBe(
    "capacity",
  );
  expect(analysisFailure(new ApiError(429, "", { code: "export_quota_exceeded" }))).toBe("quota");
  expect(analysisFailure(new ApiError(409, "", { code: "export_authorization_changed" }))).toBe(
    "sourceChanged",
  );
  expect(
    analysisFailure(new ApiError(409, "", { code: "comparison_alignment_conflict" })),
  ).toBeNull();
});
