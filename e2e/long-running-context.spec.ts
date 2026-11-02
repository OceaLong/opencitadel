import { randomUUID } from "node:crypto";

import { appApi, expect, test } from "./fixtures/acceptance.fixture";
import { createSession, readChatStream } from "./support/execution";
import { withStoppedContextWorker } from "./support/context-worker-fault";
import { acceptanceId } from "./support/ids";
import type {
  RunViewPage,
  StepDetail,
  StepView,
  StepViewPage,
  ViewPage,
} from "../ui/src/lib/api/types/execution-view";

// This browser case proves the product surface using controlled inference.
// The 200/150-round workloads and real-model quality have separate evidence.
test("retained conversation exposes real compact steps and bounded context details", async ({
  operatorPage: page,
}) => {
  test.setTimeout(600_000);
  const session = await createSession(
    page,
    acceptanceId("long-context"),
    "ask",
  );
  const observed: Array<{ run: string; step: StepView; at: string }> = [];
  for (let turn = 0; turn < 32; turn++) {
    const message =
      `Context turn ${turn}. ARCHIVE-Z19 amount=7341; never renew. ` +
      "Routine retained conversation evidence. ".repeat(50);
    await readChatStream(page, session, {
      message,
      request_id: randomUUID(),
      mode: "ask",
    });
    const runs = (
      await appApi<RunViewPage>(
        page,
        `/execution-runs?source_entity_type=session&source_entity_id=${encodeURIComponent(session)}`,
      )
    ).data;
    expect(runs.items.length).toBeGreaterThan(0);
    const run = runs.items[0];
    await expect
      .poll(
        async () =>
          (await appApi<ViewPage>(page, `/execution-runs/${run.run_id}/view`))
            .data.run.status,
      )
      .toBe("completed");
    let cursor: string | null = null;
    let at: string | null = null;
    do {
      const params = new URLSearchParams({ limit: "100" });
      if (cursor) params.set("cursor", cursor);
      if (at) params.set("at", at);
      const contexts: StepViewPage = (
        await appApi<StepViewPage>(
          page,
          `/execution-runs/${run.run_id}/contexts?${params}`,
        )
      ).data;
      at = contexts.at;
      observed.push(
        ...contexts.items.map((step) => ({
          run: run.run_id,
          step,
          at: contexts.at,
        })),
      );
      cursor = contexts.next_cursor;
    } while (cursor);
  }
  // Read the first admitted original after later compactions, through the
  // same authorized public content endpoint used by the detail panel.
  const firstView = (
    await appApi<ViewPage>(page, `/execution-runs/${observed[0].run}/view`)
  ).data;
  const originalStep = firstView.steps.find(
    (step) =>
      step.kind === "model" && step.input_ref?.availability === "available",
  );
  expect(originalStep).toBeDefined();
  const original = (
    await appApi<{ availability: string; content: string }>(
      page,
      `/execution-runs/${firstView.run.run_id}/steps/${encodeURIComponent(originalStep!.step_id)}/content?at=${encodeURIComponent(firstView.at)}&content_kind=input`,
    )
  ).data;
  expect(original.availability).toBe("available");
  expect(original.content).toContain("ARCHIVE-Z19 amount=7341; never renew.");
  const compact = observed.find(
    (item) => item.step.context?.state === "compacted",
  );
  expect(
    compact,
    "the real kernel must complete at least one context.compact",
  ).toBeDefined();
  const selected = compact!;
  const detail = (
    await appApi<StepDetail>(
      page,
      `/execution-runs/${selected.run}/contexts/${encodeURIComponent(selected.step.step_id)}?at=${encodeURIComponent(selected.at)}`,
    )
  ).data;
  expect(detail.context?.definition_version).toBe(2);
  expect(detail.context?.definition_digest).toMatch(/^[0-9a-f]{64}$/);
  expect(JSON.stringify(detail)).not.toMatch(
    /execution\/(?:inputs|results|context_documents|context_sources)\//,
  );
  await page.goto(
    `/sessions/${session}?run=${selected.run}&view=trace&step=${encodeURIComponent(selected.step.step_id)}&at=${encodeURIComponent(selected.at)}&panel=overview`,
  );
  const panel = page.getByTestId("detail-panel");
  await expect(
    panel.getByRole("heading", { name: /^Context$|^上下文$/ }),
  ).toBeVisible();
  await expect(panel).toContainText(/Summary completed|摘要已完成/);
  await panel.getByText(/Advanced details|高级详情/, { exact: true }).click();
  await expect(panel).toContainText(detail.context!.definition_digest!);
  await test.info().attach("long-context-public-evidence", {
    body: JSON.stringify({
      session,
      turns: 32,
      contextSteps: observed,
      selected: detail,
    }),
    contentType: "application/json",
  });
});

test("version availability follows actual worker expiry and recovery", async ({
  operatorPage: page,
}) => {
  test.setTimeout(180_000);
  const session = await createSession(
    page,
    acceptanceId("context-worker-expiry"),
    "agent",
  );
  await readChatStream(page, session, {
    message: "[acceptance:tool:memory_save]",
    mode: "agent",
    request_id: randomUUID(),
  });
  const runs = (
    await appApi<RunViewPage>(
      page,
      `/execution-runs?source_entity_type=session&source_entity_id=${session}`,
    )
  ).data;
  const run = runs.items[0].run_id;
  await expect
    .poll(
      async () =>
        (await appApi<ViewPage>(page, `/execution-runs/${run}/view`)).data.run
          .status,
    )
    .toBe("waiting");
  const availability = async () =>
    (
      await appApi<{
        state: string;
        definition_digest: string;
        reason: string | null;
      }>(page, `/execution-runs/${run}/definition-availability`)
    ).data;
  const original = await availability();
  expect(original.state).toBe("available");
  const message = /Waiting for a compatible worker|等待兼容版本的 Worker/;
  await withStoppedContextWorker(async () => {
    await expect
      .poll(async () => (await availability()).state, { timeout: 60_000 })
      .toBe("version_unavailable");
    const unavailable = await availability();
    expect(unavailable.definition_digest).toBe(original.definition_digest);
    expect(unavailable.reason).toBe("VERSION_UNAVAILABLE");
    await page.goto(`/sessions/${session}?run=${run}&view=trace`);
    await expect(page.getByText(message, { exact: true })).toBeVisible();
    await test
      .info()
      .attach("actual-worker-expiry", {
        body: JSON.stringify({ run, original, unavailable }),
        contentType: "application/json",
      });
  });
  await expect
    .poll(async () => (await availability()).state, { timeout: 60_000 })
    .toBe("available");
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page.getByText(message, { exact: true })).toHaveCount(0);
  expect((await availability()).definition_digest).toBe(
    original.definition_digest,
  );
});
