import { describe, expect, it } from "vitest";

import { modelErrorMessage } from "./inference-errors";

describe("modelErrorMessage", () => {
  it("maps known inference failures and ignores unrelated codes", () => {
    expect(modelErrorMessage("MODEL_QUOTA_EXCEEDED")).toBeTruthy();
    expect(modelErrorMessage("MODEL_INVALID_REQUEST")).toBeTruthy();
    expect(modelErrorMessage("EMBEDDING_UNAVAILABLE")).toBeTruthy();
    expect(modelErrorMessage("INFRASTRUCTURE_FAILED", "zh")).toBeTruthy();
    expect(modelErrorMessage("UNKNOWN")).toBeNull();
  });
});
