import { expect, test } from "@playwright/test";
import {
  readOwnedRunRetry,
  validateOwnedRetryProject,
} from "../support/owned-run-retry";
import type { RunView } from "../../ui/src/lib/api/types/execution-view";

test("retry observer binds both default and explicit projects to the current lifecycle", () => {
  for (const project of [
    "opencitadel-acceptance-current-run",
    "oc-e2e-current-run",
  ]) {
    const environment = {
      ACCEPTANCE_RUN_ID: "current-run",
      ACCEPTANCE_PROJECT_ID: project,
    };
    const lifecycle = {
      run_id: "current-run",
      project_name: project,
      state: "stack_ready",
    };
    const bootstrap = { run_id: "current-run", cleanup_completed: false };
    expect(validateOwnedRetryProject(lifecycle, bootstrap, environment)).toBe(
      project,
    );
    for (const foreign of [
      { ...lifecycle, run_id: "old-run" },
      { ...lifecycle, project_name: "foreign-project" },
      { ...lifecycle, state: "complete" },
    ])
      expect(() =>
        validateOwnedRetryProject(foreign, bootstrap, environment),
      ).toThrow("active owned stack required");
    expect(() =>
      validateOwnedRetryProject(
        lifecycle,
        { ...bootstrap, cleanup_completed: true },
        environment,
      ),
    ).toThrow("active matching bootstrap required");
    expect(() =>
      validateOwnedRetryProject(lifecycle, bootstrap, {
        ...environment,
        ACCEPTANCE_PROJECT_ID: "unsafe_project",
      }),
    ).toThrow("validated acceptance project required");
  }
});

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
