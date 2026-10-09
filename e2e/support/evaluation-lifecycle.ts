import { randomUUID } from "node:crypto";
import type { Page } from "@playwright/test";
import { expect } from "@playwright/test";
import { appApi } from "./api";
import { registerCleanupAction } from "./cleanup-journal";
import { pollProjection } from "./poll";

export async function recordOwnedRun(page: Page, run: string) {
  const candidates: any[] = [];
  let cursor: string | undefined;
  do {
    const value = (
      await appApi<any>(
        page,
        `/evaluation/recordings/sources/${run}${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""}`,
      )
    ).data;
    candidates.push(...value.items);
    cursor = value.next_cursor;
  } while (cursor);
  expect(candidates.length).toBeGreaterThan(0);
  // This helper is for read-only seeded tasks; redacted shell arguments need an explicit fixture.
  expect(candidates.every((item) => !item.requires_argument_replacement)).toBe(
    true,
  );
  const job = (
    await appApi<any>(page, "/evaluation/recordings", {
      method: "POST",
      expectStatus: 202,
      body: {
        request_id: randomUUID(),
        run_id: run,
        selections: candidates.map((item) => ({
          tool: item.tool,
          allowed_fields: item.result_fields.map((field: any) => field.name),
        })),
      },
    })
  ).data;
  registerCleanupAction({
    action: "delete-resource",
    resource: "evaluation-recording",
    resource_id: job.id,
  });
  await pollProjection(
    () =>
      appApi<any>(page, `/evaluation/recordings/${job.id}`).then(
        (value) => value.data,
      ),
    (value) => value.status === "ready",
    { timeout: 120_000, message: "owned recording published" },
  );
  return (await appApi<any>(page, `/evaluation/recordings/${job.id}/result`))
    .data;
}

export async function completedEvaluation(
  page: Page,
  suite: string,
  options: { retainedAccounting?: boolean } = {},
) {
  const check = (
    await appApi<any>(page, "/evaluation/batches/preflight", {
      method: "POST",
      body: { suite_version: suite },
    })
  ).data;
  expect(check.allowed, JSON.stringify(check.errors)).toBe(true);
  const batch = (
    await appApi<any>(page, "/evaluation/batches", {
      method: "POST",
      expectStatus: 202,
      body: {
        request_id: randomUUID(),
        suite_version: suite,
        preflight_revision: check.revision,
      },
    })
  ).data;
  registerCleanupAction({
    action: "delete-resource",
    resource: "evaluation-batch",
    resource_id: batch.id,
    retained_accounting: options.retainedAccounting,
  });
  await pollProjection(
    () =>
      appApi<any>(page, `/evaluation/batches/${batch.id}`).then(
        (value) => value.data,
      ),
    (value) => value.status === "completed" && value.cleanup_status === "clean",
    { timeout: 240_000, message: "read-only evaluation settles and cleans" },
  );
  const results = (
    await appApi<any>(page, `/evaluation/batches/${batch.id}/results?limit=200`)
  ).data;
  expect(results.next_cursor).toBeNull();
  expect(
    results.items.every((item: any) => item.execution_status === "succeeded"),
  ).toBe(true);
  return { batch, results: results.items };
}

export async function humanScore(page: Page, result: string, value: number) {
  const context = (
    await appApi<any>(page, `/evaluation/results/${result}/review-context`)
  ).data;
  await appApi(page, `/evaluation/results/${result}/scores`, {
    method: "POST",
    body: {
      request_id: randomUUID(),
      expected_revision: context.evaluation_revision,
      expected_result_revision: context.result_revision,
      rubric_version: context.rubric.id,
      scores: [
        {
          dimension: "correctness",
          status: "valid",
          value,
          reason: "Owned acceptance case weighting observation",
          evidence: [],
        },
      ],
    },
  });
}
