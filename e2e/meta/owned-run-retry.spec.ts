import { expect, test } from "@playwright/test";
import { readOwnedRunRetry } from "../support/owned-run-retry";
import type { RunView } from "../../ui/src/lib/api/types/execution-view";

test("run retry observer refuses unowned input before Docker access", () => {
  const prior = process.env.ACCEPTANCE_RUN_ID;
  try {
    process.env.ACCEPTANCE_RUN_ID = "test-owned-run";
    expect(() =>
      readOwnedRunRetry({ run_id: "'; SELECT" } as RunView, "x", "x"),
    ).toThrow("invalid run identity");
    expect(() =>
      readOwnedRunRetry(
        {
          run_id: "00000000-0000-0000-0000-000000000001",
          scope: { owner_user_id: "other", team_id: null },
        } as RunView,
        "00000000-0000-0000-0000-000000000002",
        "00000000-0000-0000-0000-000000000003",
      ),
    ).toThrow("owned personal session scope");
  } finally {
    if (prior === undefined) delete process.env.ACCEPTANCE_RUN_ID;
    else process.env.ACCEPTANCE_RUN_ID = prior;
  }
});
