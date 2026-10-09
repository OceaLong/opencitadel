import { randomUUID } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import type { Page } from "@playwright/test";

import { appApi, expect, test } from "./fixtures/acceptance.fixture";
import { registerCleanupAction } from "./support/cleanup-journal";
import {
  createSession,
  readChatStream,
  waitForTerminalProfile,
} from "./support/execution";
import { acceptanceId } from "./support/ids";
import { readOwnedRunRetry } from "./support/owned-run-retry";
import { pollProjection } from "./support/poll";
import type {
  ViewPage,
  RunViewPage,
} from "../ui/src/lib/api/types/execution-view";

async function viewFor(page: Page, session: string): Promise<ViewPage> {
  const runs = (
    await appApi<RunViewPage>(
      page,
      `/execution-runs?source_entity_type=session&source_entity_id=${encodeURIComponent(session)}`,
    )
  ).data;
  expect(runs.items.length).toBeGreaterThan(0);
  return (
    await appApi<ViewPage>(page, `/execution-runs/${runs.items[0].run_id}/view`)
  ).data;
}
async function closeConversation(page: Page) {
  const collapse = page.getByRole("button", { name: /^Collapse$|^收起$/ });
  if (await collapse.isVisible()) await collapse.click();
}
async function assertLayout(page: Page) {
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth + 1,
    ),
  ).toBe(true);
  const workspace = page.getByTestId("workbench");
  await expect(workspace).toBeVisible();
  const box = await workspace.boundingBox();
  expect(box?.height).toBeGreaterThan(200);
}

test("real workbench retains run, retry attempt, historical cut and artifact across views and back", async ({
  operatorPage: page,
}) => {
  test.setTimeout(300_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  const session = await createSession(
    page,
    acceptanceId("workbench-artifact"),
    "agent",
  );
  const prompt = `[acceptance:workbench:retry:${randomUUID()}] [acceptance:tool:artifact_write] [acceptance:workbench:artifact]`;
  await readChatStream(page, session, {
    message: prompt,
    mode: "agent",
    request_id: randomUUID(),
  });
  await page.goto(`/sessions/${session}`);
  await page
    .getByRole("button", { name: /^Approve$|^批准$/ })
    .click({ timeout: 60_000 });
  await waitForTerminalProfile(page, session, "completed");
  const view = await pollProjection(
    () => viewFor(page, session),
    (value) => Boolean(value.artifacts?.length),
    {
      timeout: 60_000,
      message: "real produced artifact reaches execution projection",
    },
  );
  const actor = (await appApi<{ id: string }>(page, "/auth/me")).data;
  const facts = readOwnedRunRetry(view.run, session, actor.id);
  const failedAttempt = facts.find(
    (fact) => fact.event_type === "RunAttemptFailed",
  )!;
  const retried = facts.find((fact) => fact.event_type === "RunRetried")!;
  expect(failedAttempt).toBeTruthy();
  expect(retried).toBeTruthy();
  expect(retried.stream_version).toBeGreaterThan(failedAttempt.stream_version);
  const request = facts.find(
    (fact) =>
      fact.event_type === "ActivityRequested" &&
      fact.activity_type === "model.call" &&
      fact.stream_version > retried.stream_version,
  )!;
  expect(request).toBeTruthy();
  const failed = view.steps.find(
    (step) => step.kind === "model" && step.status === "failed",
  )!;
  const retry = view.steps.find(
    (step) => step.activity_id === request.activity_id,
  )!;
  expect(failed).toBeTruthy();
  expect(retry).toBeTruthy();
  expect(retry.step_id).not.toBe(failed.step_id);
  expect(retry.attempt_id).not.toBe(failed.attempt_id);
  await writeFile(
    resolve(process.env.ACCEPTANCE_EVIDENCE_DIR!, "workbench-run-retry.json"),
    JSON.stringify(
      {
        run_id: view.run.run_id,
        facts,
        failed_step: failed.step_id,
        retry_step: retry.step_id,
        retry_attempt: retry.attempt_id,
      },
      null,
      2,
    ),
  );
  const artifact = view.artifacts![0];
  const detail = page.getByTestId("detail-panel");
  const expectSelection = async (expected: Record<string, string>) => {
    await expect
      .poll(() => {
        const params = new URL(page.url()).searchParams;
        return Object.fromEntries(
          Object.keys(expected).map((key) => [key, params.get(key)]),
        );
      })
      .toEqual(expected);
  };
  await page.goto(
    `/sessions/${session}?run=${view.run.run_id}&view=task&at=${encodeURIComponent(view.at!)}&step=${encodeURIComponent(retry!.step_id)}&panel=overview`,
  );
  await closeConversation(page);
  await expect(page.getByTestId("task-view")).toBeVisible();
  await expect(detail).toContainText(retry.step_id);
  await expect(detail).toContainText(retry.attempt_id!);
  const rows = page.getByTestId("task-view").locator("li > button");
  await rows.first().focus();
  await page.keyboard.press("ArrowDown");
  await expect(rows.nth(1)).toBeFocused();
  const previous = page.getByRole("button", {
    name: /^Previous event$|^上一事件$/,
  });
  await previous.focus();
  await page.keyboard.press("ArrowLeft");
  await expect
    .poll(() => new URL(page.url()).searchParams.get("at"))
    .not.toBe(view.at);
  await page.goto(
    `/sessions/${session}?run=${view.run.run_id}&view=task&at=${encodeURIComponent(view.at!)}&step=${encodeURIComponent(retry!.step_id)}&panel=overview`,
  );
  await closeConversation(page);
  await expect(detail).toContainText(retry.step_id);
  await expect(detail).toContainText(retry.attempt_id!);
  for (const name of [/^Debug$|^调试$/, /^Task$|^任务$/]) {
    await page.getByRole("tab", { name, exact: true }).click();
    await expectSelection({
      run: view.run.run_id,
      at: view.at!,
      step: retry.step_id,
      view: name.source.includes("Debug") ? "debug" : "task",
    });
    await expect(
      page.getByTestId(
        name.source.includes("Debug") ? "trace-view" : "task-view",
      ),
    ).toBeVisible();
    await expect(detail).toContainText(retry.step_id);
    await expect(detail).toContainText(retry.attempt_id!);
  }
  await page.goto(
    `/sessions/${session}?run=${view.run.run_id}&view=task&at=${encodeURIComponent(view.at!)}&artifact=${artifact.artifact_id}&version=${artifact.version}&panel=artifact`,
  );
  await page
    .getByRole("button", { name: /^Load artifact$|^加载成果$/ })
    .click();
  await expect(page.getByTestId("detail-panel")).toContainText(
    "Workbench evidence",
  );
  await page.getByRole("tab", { name: /^Debug$|^调试$/ }).click();
  await expect(page.getByTestId("trace-view")).toBeVisible();
  await page.goBack();
  await expectSelection({
    run: view.run.run_id,
    at: view.at!,
    view: "task",
    artifact: artifact.artifact_id,
    version: String(artifact.version),
  });
  await expect(
    detail.getByRole("heading", { name: artifact.artifact_id, exact: true }),
  ).toBeVisible();
  const reload = page.getByRole("button", {
    name: /^Load artifact$|^加载成果$/,
  });
  // The fixed-version body may remain cached when only the view changes.
  await expect
    .poll(
      async () =>
        (await reload.isVisible()) ||
        (await detail
          .getByText("Workbench evidence", { exact: true })
          .isVisible()),
    )
    .toBe(true);
  if (await reload.isVisible()) await reload.click();
  await expect(page.getByTestId("detail-panel")).toContainText(
    "Workbench evidence",
  );
  // Registered only after the complete scenario succeeds; catalog owns AC01 once.
  await page.screenshot({
    path: resolve(
      process.env.ACCEPTANCE_EVIDENCE_DIR!,
      "workbench-ac01-restored-artifact.png",
    ),
    fullPage: true,
  });
  test.info().annotations.push({ type: "acceptance", description: "AC01" });
});

test("real running, waiting, failed, empty and missing-data workbench visual and keyboard matrix", async ({
  operatorPage: page,
  bootstrapState,
}) => {
  test.setTimeout(600_000);
  const output = resolve(
    process.env.ACCEPTANCE_EVIDENCE_DIR ?? "../tmp/u07-browser",
    "workbench-visuals",
  );
  await mkdir(output, { recursive: true });
  const model = (
    await appApi<{ id: string }>(page, "/inference/models", {
      method: "POST",
      body: {
        endpoint_id: bootstrapState.endpoint_id,
        display_name: acceptanceId("workbench-failure"),
        model_name: "acceptance-failure",
        kind: "chat",
        settings: { kind: "chat", temperature: 0, max_output_tokens: 4096 },
        input_price_per_million: 0,
        output_price_per_million: 0,
        extra_params: {},
        capabilities: {},
        visibility: "global",
      },
    })
  ).data;
  registerCleanupAction({
    action: "delete-resource",
    resource: "inference-model",
    resource_id: model.id,
  });
  const empty = await createSession(
    page,
    acceptanceId("workbench-empty"),
    "ask",
  );
  const waiting = await createSession(
    page,
    acceptanceId("workbench-waiting"),
    "agent",
  );
  await readChatStream(page, waiting, {
    message: "[acceptance:tool:memory_save]",
    mode: "agent",
    request_id: randomUUID(),
  });
  const failed = await createSession(
    page,
    acceptanceId("workbench-failed"),
    "ask",
    model.id,
  );
  await readChatStream(page, failed, {
    message: "[acceptance:terminal]",
    mode: "ask",
    model_id: model.id,
    request_id: randomUUID(),
  });
  await waitForTerminalProfile(page, failed, "failed");
  const missingTitle =
    acceptanceId("workbench-missing") + " Long observed label 长标签".repeat(4);
  const missing = await createSession(page, missingTitle, "ask");
  await readChatStream(page, missing, {
    message: `Long observed activity 长标签 ${"review_without_manufactured_metrics_".repeat(6)}`,
    mode: "ask",
    request_id: randomUUID(),
  });
  await waitForTerminalProfile(page, missing, "completed");
  const missingView = await viewFor(page, missing);
  expect(
    missingView.steps.some((step) => step.first_persisted_output_at == null),
  ).toBe(true);
  const running = await createSession(
    page,
    acceptanceId("workbench-running"),
    "ask",
    model.id,
  );
  await readChatStream(
    page,
    running,
    {
      message: "[acceptance:timeout]",
      mode: "ask",
      model_id: model.id,
      request_id: randomUUID(),
    },
    { stopAfter: 2 },
  );
  try {
    for (const [width, height] of [
      [1440, 900],
      [1024, 768],
      [390, 844],
    ]) {
      await page.setViewportSize({ width, height });
      for (const locale of ["en", "zh"])
        for (const theme of ["light", "dark"] as const) {
          await page.context().addCookies([
            {
              name: "NEXT_LOCALE",
              value: locale,
              url: new URL(page.url()).origin,
            },
          ]);
          await page.evaluate(
            (value) => localStorage.setItem("opencitadel-theme", value),
            theme,
          );
          await page.emulateMedia({
            colorScheme: theme,
            reducedMotion: "reduce",
          });
          for (const [state, session] of Object.entries({
            running,
            waiting,
            failed,
            empty,
            missing,
          })) {
            await page.goto(`/sessions/${session}`);
            await expect(page.getByTestId("workbench")).toBeVisible();
            if (state !== "empty") {
              await expect(page).toHaveURL(/run=/);
              await expect(
                page
                  .getByTestId("task-view")
                  .getByRole("heading", { level: 1 }),
              ).toBeVisible();
            }
            await closeConversation(page);
            await assertLayout(page);
            await page.getByRole("tab", { name: /^Debug$|^调试$/ }).click();
            await expect(page.getByTestId("trace-view")).toBeVisible();
            if (state !== "empty")
              await expect(
                page
                  .getByTestId("trace-view")
                  .getByRole("heading", { level: 2 })
                  .first(),
              ).toBeVisible();
            await page.screenshot({
              path: resolve(
                output,
                `${width}-${locale}-${theme}-${state}-debug.png`,
              ),
              fullPage: true,
            });
            await page.getByRole("tab", { name: /^Task$|^任务$/ }).click();
            await expect(page.getByTestId("task-view")).toBeVisible();
            if (state === "failed" && width <= 1024) {
              const main = page.getByTestId("workbench").locator("main");
              const scrollTop = await main.evaluate((element) => {
                element.scrollTop = element.scrollHeight;
                return element.scrollTop;
              });
              expect(scrollTop).toBeGreaterThan(0);
            }
            await page.screenshot({
              path: resolve(
                output,
                `${width}-${locale}-${theme}-${state}-task.png`,
              ),
              fullPage: true,
            });
          }
        }
      await page.goto(`/sessions/${missing}`);
      await expect(page).toHaveURL(/run=/);
      await expect(
        page.getByTestId("task-view").getByRole("heading", { level: 1 }),
      ).toBeVisible();
      await closeConversation(page);
      const title = page.getByRole("button", {
        name: /^Show full title:|^查看完整标题:/,
      });
      await title.focus();
      await page.keyboard.press("Enter");
      const fullTitle = page
        .getByRole("dialog")
        .getByText(missingTitle, { exact: true });
      await expect(fullTitle).toBeVisible();
      await expect
        .poll(() =>
          page.getByRole("dialog").evaluate((element) => ({
            animation: getComputedStyle(element).animationName,
            opacity: getComputedStyle(element).opacity,
          })),
        )
        .toEqual({ animation: "none", opacity: "1" });
      expect(
        await fullTitle.evaluate(
          (element) => element.scrollWidth <= element.clientWidth + 1,
        ),
      ).toBe(true);
      const titleBox = await page.getByRole("dialog").boundingBox();
      expect(titleBox!.x).toBeGreaterThanOrEqual(0);
      expect(titleBox!.x + titleBox!.width).toBeLessThanOrEqual(width);
      await page.screenshot({
        path: resolve(output, `${width}-full-title.png`),
        fullPage: true,
      });
      await page.keyboard.press("Escape");
      await expect(page.getByRole("dialog")).toBeHidden();
      await expect(title).toBeFocused();
      if (width === 1440) {
        await page.emulateMedia({ reducedMotion: "no-preference" });
        await title.click();
        await expect
          .poll(() =>
            page
              .getByRole("dialog")
              .evaluate(
                (element) =>
                  getComputedStyle(element).opacity === "1" &&
                  element
                    .getAnimations()
                    .every((animation) => animation.playState === "finished"),
              ),
          )
          .toBe(true);
        await page.screenshot({
          path: resolve(output, "1440-full-title-normal-motion.png"),
          fullPage: true,
        });
        await page.keyboard.press("Escape");
        await expect(page.getByRole("dialog")).toBeHidden();
        await expect(title).toBeFocused();
        await page.emulateMedia({ reducedMotion: "reduce" });
      }
      const task = page.getByRole("tab", { name: /^Task$|^任务$/ });
      await task.focus();
      await page.keyboard.press("ArrowRight");
      await expect(
        page.getByRole("tab", { name: /^Debug$|^调试$/ }),
      ).toBeFocused();
      await page.keyboard.press("Alt+1");
      await expect(page.getByTestId("task-view")).toBeVisible();
      expect(
        await page
          .getByTestId("workbench")
          .evaluate((element) => getComputedStyle(element).animationName),
      ).toBe("none");
      await assertLayout(page);
      const row = page.getByTestId("task-view").locator("li > button").last();
      await row.scrollIntoViewIfNeeded({ timeout: 5000 });
      await row.focus();
      await page.keyboard.press("Enter");
      await expect(page.getByTestId("detail-panel")).toBeVisible();
      await page.getByTestId("detail-panel").evaluate((element) => {
        element.scrollTop = element.scrollHeight;
      });
      await page.screenshot({
        path: resolve(output, `${width}-keyboard-detail-scroll.png`),
        fullPage: true,
      });
      if (width < 1280) {
        await page.keyboard.press("Escape");
        await expect(page.getByTestId("detail-panel")).toBeHidden();
        await expect(row).toBeFocused();
      } else
        await page
          .getByRole("button", { name: /^Close detail$|^关闭详情$/ })
          .click();
      await expect(page.getByTestId("playback-controls")).toBeVisible();
      await assertLayout(page);
    }
  } finally {
    await appApi(page, `/sessions/${running}/stop`, {
      method: "POST",
      body: {},
    });
  }
});

// Transport faults below forward real authenticated responses. They do not stub DTOs.
test(
  "AC03 actual execution delivery duplicates, reconnects, and retires delayed bodies on selection and workspace changes",
  { annotation: { type: "acceptance", description: "AC03" } },
  async ({ operatorPage: page, bootstrapState }) => {
    test.setTimeout(300_000);
    const { installExecutionDeliveryFault } =
      await import("./support/execution-delivery-fault");
    const model = bootstrapState.model_ids.chat!;
    const session = await createSession(
      page,
      acceptanceId("delivery"),
      "agent",
      model,
    );
    await readChatStream(page, session, {
      message:
        "[acceptance:workbench:artifact-versions] [acceptance:tool:artifact_write]",
      mode: "agent",
      model_id: model,
      request_id: randomUUID(),
    });
    const pending = await pollProjection(
      () => viewFor(page, session),
      (value) => value.run.status === "waiting",
      { timeout: 60_000, message: "first actual approval" },
    );
    const run = pending.run.run_id;
    const pendingApproval = async (excluded: string[] = []) =>
      pollProjection(
        async () => {
          for (let offset = 0; ; offset += 200) {
            const inbox = (
              await appApi<any>(
                page,
                `/approvals?limit=200&offset=${offset}&status=pending`,
              )
            ).data.items;
            const match = inbox.find(
              (row: any) =>
                row.run_id === run && !excluded.includes(row.approval_id),
            );
            if (match) return match;
            if (inbox.length < 200) return null;
          }
        },
        (value) => value !== null,
        { timeout: 60_000, message: "owned pending approval" },
      );
    const first = await pendingApproval();
    const streamUrl = new URL(
      `/api/execution-runs/${run}/events/stream`,
      page.url(),
    ).href;
    await page.addInitScript(installExecutionDeliveryFault, { streamUrl });
    await page.goto(`/sessions/${session}?run=${run}&view=debug`);
    await expect(page.getByTestId("workbench")).toBeVisible();
    // Wait for actual subscription before producing another persisted event.
    await expect
      .poll(() =>
        page.evaluate(
          () => (globalThis as any).__executionDeliveryFault.state.requests,
        ),
      )
      .toBe(1);
    try {
      await appApi(
        page,
        `/approval-batches/${first.approval_id}/commands/decide`,
        {
          method: "POST",
          body: { decision: "approved", feedback: "owned first version" },
        },
      );
      const second = await pendingApproval([first.approval_id]);
      await expect
        .poll(() =>
          page.evaluate(
            () =>
              (globalThis as any).__executionDeliveryFault.state.disconnected,
          ),
        )
        .toBe(1);
      await expect
        .poll(() =>
          page.evaluate(
            () =>
              (globalThis as any).__executionDeliveryFault.state.resumes.length,
          ),
        )
        .toBeGreaterThan(0);
      const delivery = await page.evaluate(
        () => (globalThis as any).__executionDeliveryFault.state,
      );
      expect(delivery.duplicated).toBe(1);
      expect(delivery.frameSha256).toMatch(/^[a-f0-9]{64}$/);
      expect(delivery.resumes[0]).toBe(delivery.cursor);
      // EOF during a pending second write must not invent completed/failed status.
      const waiting = await pollProjection(
        () => viewFor(page, session),
        (value) => value.run.status === "waiting",
        {
          timeout: 60_000,
          message: "second approval remains nonterminal after EOF",
        },
      );
      expect(waiting.run.terminal_at).toBeNull();
      await expect(page.getByTestId("workbench")).toContainText(/Waiting|等待/);
      const rows = await page
        .locator("[data-row-key]")
        .evaluateAll((elements) =>
          elements.map((element) => element.getAttribute("data-row-key")),
        );
      expect(rows.length).toBeGreaterThan(0);
      expect(new Set(rows).size).toBe(rows.length);
      expect(new Set(waiting.steps.map((step) => step.step_id)).size).toBe(
        waiting.steps.length,
      );
      await appApi(
        page,
        `/approval-batches/${second.approval_id}/commands/decide`,
        {
          method: "POST",
          body: { decision: "approved", feedback: "owned second version" },
        },
      );
      await waitForTerminalProfile(page, session, "completed");
      const final = await pollProjection(
        () => viewFor(page, session),
        (value) =>
          value.run.status === "completed" &&
          value.artifacts.length === 2 &&
          value.artifacts
            .map((artifact) => artifact.version)
            .sort()
            .join(",") === "1,2",
        {
          timeout: 60_000,
          message:
            "terminal run and reconciled artifact versions after resumed stream",
        },
      );
      expect(new Set(final.steps.map((step) => step.step_id)).size).toBe(
        final.steps.length,
      );
      const events = (
        await appApi<any>(
          page,
          `/execution-runs/${run}/events?after=${encodeURIComponent(delivery.cursor)}&limit=200`,
        )
      ).data;
      expect(
        events.events.every((event: any) => event.cursor !== delivery.cursor),
      ).toBe(true);
      expect(final.artifacts).toHaveLength(2);
      expect(
        final.artifacts.map((artifact) => artifact.version).sort(),
      ).toEqual([1, 2]);
      await test.info().attach("actual-delivery-injection", {
        body: JSON.stringify({
          run,
          delivery,
          steps: final.steps.map((step) => ({
            step_id: step.step_id,
            activity_id: step.activity_id,
            attempt_id: step.attempt_id,
          })),
        }),
        contentType: "application/json",
      });

      const bodySteps = final.steps.filter(
        (step) => step.output_ref?.availability === "available",
      );
      expect(bodySteps.length).toBeGreaterThan(1);
      const oldStep = bodySteps[0],
        newStep = bodySteps.at(-1)!;
      const contentPath = `/api/execution-runs/${run}/steps/${encodeURIComponent(oldStep.step_id)}/content`;
      let release!: () => void;
      let captured!: () => void;
      const held = new Promise<void>((resolve) => {
        release = resolve;
      });
      const gotResponse = new Promise<void>((resolve) => {
        captured = resolve;
      });
      let disposition = "pending";
      const routeHandler = async (route: import("@playwright/test").Route) => {
        const original = await route.fetch();
        expect(original.status()).toBe(200);
        captured();
        await held;
        try {
          await route.fulfill({ response: original });
          disposition = "delivered";
        } catch {
          disposition = "selection-aborted";
        }
      };
      const matchContent = (url: URL) => url.pathname === contentPath;
      await page.route(matchContent, routeHandler);
      try {
        await page.goto(
          `/runs/${run}?at=${encodeURIComponent(final.at!)}&view=debug&step=${encodeURIComponent(oldStep.step_id)}&panel=input-output`,
        );
        await page
          .getByRole("button", { name: /^Load output$|^加载输出$/ })
          .click();
        await gotResponse;
        for (let index = 0; index < final.steps.length; index++) {
          if (await page.locator(`[data-step="${newStep.step_id}"]`).count())
            break;
          const expand = page
            .getByTestId("trace-view")
            .getByRole("button", { name: /^Expand |^展开/ });
          if (!(await expand.count())) break;
          await expand.first().click();
        }
        await page.locator(`[data-step="${newStep.step_id}"]`).click();
        await expect(page).toHaveURL(
          new RegExp(`step=${encodeURIComponent(newStep.step_id)}`),
        );
        await page
          .locator("[data-execution-detail]")
          .getByRole("tab", { name: /^Input \/ output$|^输入.*输出$/ })
          .click();
        await page
          .getByRole("button", { name: /^Load output$|^加载输出$/ })
          .click();
        await expect(page.locator("[data-execution-detail]")).toContainText(
          newStep.step_id,
        );
        await expect(
          page.getByRole("button", { name: /^Load output$|^加载输出$/ }),
        ).toHaveCount(0);
        const currentBody = await page
          .locator("[data-execution-detail]")
          .innerText();
        release();
        await expect.poll(() => disposition).not.toBe("pending");
        await expect
          .poll(() => page.locator("[data-execution-detail]").innerText())
          .toBe(currentBody);
        await test.info().attach("actual-delayed-detail", {
          body: JSON.stringify({
            run,
            oldStep: oldStep.step_id,
            newStep: newStep.step_id,
            disposition,
          }),
          contentType: "application/json",
        });
        await expect(page.locator("[data-execution-detail]")).toContainText(
          newStep.step_id,
        );
        await expect(page.locator("[data-execution-detail]")).not.toContainText(
          oldStep.step_id,
        );
      } finally {
        release();
        await page.unroute(matchContent, routeHandler);
      }

      // Real scope B, with its own resource, retires a real delayed A detail response.
      const team = (
        await appApi<any>(page, "/teams", {
          method: "POST",
          body: {
            name: acceptanceId("delivery-scope"),
            description: "Owned response isolation",
          },
        })
      ).data;
      registerCleanupAction({
        action: "delete-resource",
        resource: "team",
        resource_id: team.id,
      });
      const scoped = (
        await appApi<any>(page, "/sessions", {
          method: "POST",
          headers: { "X-Workspace-Id": team.id },
          body: { title: acceptanceId("scope-b") },
        })
      ).data;
      registerCleanupAction({
        action: "delete-resource",
        resource: "session",
        resource_id: scoped.session_id,
        workspace_id: team.id,
      });
      let releaseScope!: () => void, capturedScope!: () => void;
      const scopeHeld = new Promise<void>((resolve) => {
        releaseScope = resolve;
      });
      const scopeCaptured = new Promise<void>((resolve) => {
        capturedScope = resolve;
      });
      let scopeDisposition = "pending";
      const scopeRoute = async (route: import("@playwright/test").Route) => {
        const original = await route.fetch();
        expect(original.status()).toBe(200);
        capturedScope();
        await scopeHeld;
        try {
          await route.fulfill({ response: original });
          scopeDisposition = "delivered";
        } catch {
          scopeDisposition = "scope-aborted";
        }
      };
      const matchScope = (url: URL) => url.pathname === contentPath;
      await page.route(matchScope, scopeRoute);
      try {
        await page.goto(
          `/runs/${run}?at=${encodeURIComponent(final.at!)}&view=debug&step=${encodeURIComponent(oldStep.step_id)}&panel=input-output`,
        );
        await page
          .getByRole("button", { name: /^Load output$|^加载输出$/ })
          .click();
        await scopeCaptured;
        await page.getByRole("button", { name: /Workspace|工作区/ }).click();
        await page
          .getByRole("button", { name: team.name, exact: true })
          .click();
        await expect
          .poll(() =>
            page.evaluate(() =>
              localStorage.getItem("opencitadel-active-workspace"),
            ),
          )
          .toBe(team.id);
        releaseScope();
        await expect.poll(() => scopeDisposition).not.toBe("pending");
        await test.info().attach("actual-delayed-scope", {
          body: JSON.stringify({
            run,
            workspaceB: team.id,
            scopeDisposition,
          }),
          contentType: "application/json",
        });
        await expect(page.locator("body")).not.toContainText(oldStep.step_id);
        await appApi(page, `/execution-runs/${run}/view`, {
          expectStatus: 404,
        });
        const own = (await appApi<any>(page, `/sessions/${scoped.session_id}`))
          .data;
        expect(JSON.stringify(own)).toContain(scoped.session_id);
        const bRequest = page.waitForRequest(
          (request) =>
            request.url().includes(`/api/sessions/${scoped.session_id}`) &&
            request.headers()["x-workspace-id"] === team.id,
        );
        await page.goto(`/sessions/${scoped.session_id}`);
        await bRequest;
        await expect(page.locator("body")).not.toContainText(
          "Owned immutable original.",
        );
      } finally {
        releaseScope();
        await page.unroute(matchScope, scopeRoute);
      }
    } finally {
      await page.evaluate(async () => {
        await (globalThis as any).__executionDeliveryFault?.restore();
      });
    }
  },
);

// AC06 source subset: actual command races and command delivery uncertainty.
// Different-user victory and physical write unknown have separate producer obligations.
test("current approval command races settle once, and a lost real response remains unconfirmed without retry", async ({
  operatorPage: page,
  bootstrapState,
  browser,
}) => {
  test.setTimeout(300_000);
  const model = bootstrapState.model_ids.chat!;
  async function pendingRun() {
    const session = await createSession(
      page,
      acceptanceId("approval-race"),
      "agent",
      model,
    );
    await readChatStream(page, session, {
      message:
        "[acceptance:tool:artifact_write] [acceptance:workbench:artifact]",
      mode: "agent",
      model_id: model,
      request_id: randomUUID(),
    });
    const view = await pollProjection(
      () => viewFor(page, session),
      (value) => value.run.status === "waiting",
      { timeout: 60_000, message: "owned approval race boundary" },
    );
    const approval = await find(view.run.run_id);
    expect(approval.status).toBe("pending");
    return { session, view, approval };
  }
  async function find(run: string): Promise<any> {
    for (let offset = 0; ; offset += 200) {
      const items = (
        await appApi<any>(page, `/approvals?limit=200&offset=${offset}`)
      ).data.items;
      const item = items.find((row: any) => row.run_id === run);
      if (item) return item;
      if (items.length < 200) throw new Error("owned current approval missing");
    }
  }
  const first = await pendingRun();
  const path = `/approval-batches/${first.approval.approval_id}/commands/decide`;
  const competitor = await browser.newContext({
    storageState: await page.context().storageState(),
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:8088",
  });
  const competingPage = await competitor.newPage();
  let decisions: any[];
  try {
    await competingPage.goto("/");
    expect((await appApi<any>(competingPage, "/auth/me")).data.id).toBe(
      (await appApi<any>(page, "/auth/me")).data.id,
    );
    decisions = await Promise.all(
      [
        [page, "approved"],
        [competingPage, "rejected"],
      ].map(([actor, decision]) =>
        appApi<any>(actor as Page, path, {
          method: "POST",
          expectStatus: [200, 404],
          body: { decision, feedback: "bounded concurrent current commands" },
        }),
      ),
    );
  } finally {
    await competitor.close();
  }
  expect(decisions.some((response) => response.status === 200)).toBe(true);
  const durable = await pollProjection(
    () => find(first.view.run.run_id),
    (value) => value.status !== "pending",
    { timeout: 60_000, message: "authoritative winner, not HTTP echo" },
  );
  expect(["approved", "rejected"]).toContain(durable.decision);
  expect(durable.decided_by_user_id).toBe(
    (await appApi<any>(page, "/auth/me")).data.id,
  );
  const duplicate = await appApi<any>(page, path, {
    method: "POST",
    expectStatus: [200, 404],
    body: {
      decision: durable.decision,
      feedback: "bounded concurrent current commands",
    },
  });
  expect([200, 404]).toContain(duplicate.status);
  expect(await find(first.view.run.run_id)).toEqual(durable);
  const settled = await pollProjection(
    () => viewFor(page, first.session),
    (value) =>
      ["completed", "failed", "cancelled"].includes(value.run.status) &&
      value.artifacts.length === (durable.decision === "approved" ? 1 : 0),
    {
      timeout: 90_000,
      message: "settled execution and artifact projection after decision race",
    },
  );
  expect(
    settled.approvals.filter(
      (item) => item.approval_id === durable.approval_id,
    ),
  ).toHaveLength(1);
  const toolActivities = new Set(
    settled.steps
      .filter((step) => step.kind === "tool" && step.status === "completed")
      .map((step) => step.activity_id),
  );
  expect(toolActivities.size).toBe(durable.decision === "approved" ? 1 : 0);
  expect(settled.artifacts.length).toBe(
    durable.decision === "approved" ? 1 : 0,
  );

  const uncertain = await pendingRun();
  const ownPath = `/api/approval-batches/${uncertain.approval.approval_id}/commands/decide`;
  let posts = 0,
    blockedReads = 0,
    withholdReads = false;
  let realStatus: number | undefined;
  const ownMatch = (url: URL) => url.pathname === ownPath;
  const inboxMatch = (url: URL) => url.pathname === "/api/approvals";
  const postRoute = async (route: import("@playwright/test").Route) => {
    if (route.request().method() !== "POST") return route.continue();
    posts++;
    const actual = await route.fetch();
    realStatus = actual.status();
    expect(actual.status()).toBe(200);
    withholdReads = true;
    await route.abort("connectionreset"); // Actual command completed; only its receipt is discarded.
  };
  const inboxRoute = async (route: import("@playwright/test").Route) => {
    if (withholdReads) {
      blockedReads++;
      await route.abort("connectionreset");
    } else await route.continue();
  };
  await page.goto(
    `/sessions/${uncertain.session}?run=${uncertain.view.run.run_id}&panel=approval`,
  );
  await closeConversation(page);
  const detail = page.locator("[data-execution-detail]");
  await expect(
    detail.getByRole("button", { name: /^Approve$|^批准$/ }),
  ).toBeEnabled();
  await page.route(ownMatch, postRoute);
  await page.route(inboxMatch, inboxRoute);
  try {
    await detail.getByRole("button", { name: /^Approve$|^批准$/ }).click();
    await expect(detail).toContainText(
      /command outcome is unconfirmed|命令结果.*确认|结果尚未确认/,
    );
    const decisions = detail.getByRole("button", {
      name: /^Approve$|^批准$|^Reject$|^拒绝$/,
    });
    await expect
      .poll(async () => {
        for (let index = 0; index < (await decisions.count()); index++)
          if (await decisions.nth(index).isEnabled()) return false;
        return true;
      })
      .toBe(true);
    expect(posts).toBe(1);
    expect(realStatus).toBe(200);
    expect(blockedReads).toBeGreaterThan(0);
    withholdReads = false;
    const persisted = await pollProjection(
      () => find(uncertain.view.run.run_id),
      (value) => value.decision === "approved",
      {
        timeout: 60_000,
        message: "restore real inbox and reconcile persisted decision",
      },
    );
    await page.reload();
    await expect(page.locator("[data-execution-detail]")).toContainText(
      "approved",
    );
    expect(posts).toBe(1);
    expect(persisted.subject_activity_id).toBe(
      uncertain.approval.subject_activity_id,
    );
    await test.info().attach("approval-command-delivery", {
      body: JSON.stringify({
        run: uncertain.view.run.run_id,
        approval: uncertain.approval.approval_id,
        posts,
        blockedReads,
        realStatus,
        decision: persisted.decision,
      }),
      contentType: "application/json",
    });
  } finally {
    withholdReads = false;
    await page.unroute(ownMatch, postRoute);
    await page.unroute(inboxMatch, inboxRoute);
  }
});

// AC06 physical-write clause. Other-actor approval clause remains a separate scenario.
test("real completed shell write loses its receipt and stays durably unknown without retry", async ({
  operatorPage: page,
  bootstrapState,
}) => {
  test.setTimeout(360_000);
  const { physicalFault, unchangedFault, acquirePhysicalFaultLifecycle } =
    await import("./support/physical-fault");
  const session = await createSession(
    page,
    acceptanceId("physical-unknown"),
    "agent",
    bootstrapState.model_ids.chat!,
  );
  await readChatStream(page, session, {
    message: "[acceptance:physical-prewarm] [acceptance:tool:read_file]",
    mode: "agent",
    model_id: bootstrapState.model_ids.chat!,
    request_id: randomUUID(),
  });
  await waitForTerminalProfile(page, session, "completed");
  const stream = await readChatStream(page, session, {
    message: `[acceptance:physical-unknown:${session}] [acceptance:tool:shell_execute]`,
    mode: "agent",
    model_id: bootstrapState.model_ids.chat!,
    request_id: randomUUID(),
  });
  expect(stream.at(-1)?.type).toBe("approval");
  const pending = await viewFor(page, session);
  const inbox = (await appApi<any>(page, "/approvals?status=pending&limit=200"))
    .data;
  const approvals = inbox.items.filter(
    (entry: any) => entry.run_id === pending.run.run_id,
  );
  expect(approvals).toHaveLength(1);
  const approval = approvals[0];
  let fault: any;
  const lifecycle = await acquirePhysicalFaultLifecycle();
  try {
    fault = lifecycle.arm({
      kind: "shell_receipt_loss",
      "execution-run-id": pending.run.run_id,
      "activity-id": approval.subject_activity_id,
    });
    await page.goto(
      `/sessions/${session}?run=${pending.run.run_id}&panel=approval`,
    );
    await closeConversation(page);
    await page
      .locator("[data-execution-detail]")
      .getByRole("button", { name: /^Approve$|^批准$/ })
      .click();
    const unknown = await pollProjection(
      () => viewFor(page, session),
      (view) =>
        view.run.status === "failed" &&
        view.steps.some(
          (step) =>
            step.activity_id === approval.subject_activity_id &&
            step.status === "unknown",
        ),
      {
        timeout: 90_000,
        message:
          "actual worker persists unknown after withheld successful physical receipt",
      },
    );
    const step = unknown.steps.find(
      (item) => item.activity_id === approval.subject_activity_id,
    )!;
    expect(step.end_reason).toBe("NON_IDEMPOTENT_OUTCOME_UNKNOWN");
    const first = physicalFault("snapshot", { "fault-id": fault.fault_id });
    expect(first.counts).toEqual({ handler: 1, catalog: 1, replay: 0 });
    const target = `/sessions/${session}?run=${pending.run.run_id}&view=debug&step=${step.step_id}`;
    await page.goto(target);
    await closeConversation(page);
    await page.locator(`[data-step="${step.step_id}"]`).click();
    const detail = page.locator("[data-execution-detail]");
    await expect(detail).toContainText(
      /external.*unknown|confirm.*external|外部.*未知|确认.*外部/i,
    );
    await page.reload();
    await expect(detail).toContainText(
      /external.*unknown|confirm.*external|外部.*未知|确认.*外部/i,
    );
    await page.goto("/analysis");
    await page.goBack();
    await expect(page).toHaveURL(new RegExp(`/sessions/${session}`));
    await expect(detail).toContainText(
      /external.*unknown|confirm.*external|外部.*未知|确认.*外部/i,
    );
    await page.goForward();
    await page.goBack();
    await expect(page).toHaveURL(new RegExp(`/sessions/${session}`));
    const refreshed = await viewFor(page, session);
    expect(
      refreshed.steps.filter(
        (item) => item.activity_id === approval.subject_activity_id,
      ),
    ).toHaveLength(1);
    expect(refreshed.run.status).toBe("failed");
    const later = physicalFault("snapshot", { "fault-id": fault.fault_id });
    unchangedFault(first, later);
    const cleaned = physicalFault("disarm", { "fault-id": fault.fault_id });
    expect(cleaned.observation.marker_deleted).toBe(true);
    lifecycle.release(cleaned);
    fault = undefined;
    await test.info().attach("physical-write-unknown", {
      body: JSON.stringify({ first, later, cleaned }),
      contentType: "application/json",
    });
    fault = undefined;
  } finally {
    try {
      if (fault) physicalFault("cancel", { "fault-id": fault.fault_id });
    } finally {
      lifecycle.retainFailure();
    }
  }
});

test(
  "AC06 different owned actor wins current approval and stale native decision revalidates without write",
  { annotation: { type: "acceptance", description: "AC06" } },
  async ({ operatorPage: page, bootstrapState }) => {
    test.setTimeout(300_000);
    const { createOwnedActor } = await import("./support/owned-actor");
    const operator = (await appApi<{ id: string }>(page, "/auth/me")).data;
    const team = (
      await appApi<{ id: string }>(page, "/teams", {
        method: "POST",
        body: {
          name: acceptanceId("approval-actors"),
          description: "Owned acceptance actors",
        },
      })
    ).data;
    registerCleanupAction({
      action: "delete-resource",
      resource: "team",
      resource_id: team.id,
    });
    const actor = await createOwnedActor(page, team.id);
    try {
      expect(actor.cleanup.resource_id).not.toBe(operator.id);
      await page.evaluate(
        ({ workspaceId, userId }) => {
          localStorage.setItem("opencitadel-active-workspace", workspaceId);
          localStorage.setItem(
            `opencitadel-active-workspace:${encodeURIComponent(userId)}`,
            workspaceId,
          );
        },
        { workspaceId: team.id, userId: operator.id },
      );
      const session = await createSession(
        page,
        acceptanceId("other-actor-winner"),
        "agent",
        bootstrapState.model_ids.chat!,
      );
      // createSession journals the current real workspace, retaining team authority for cleanup.
      await readChatStream(page, session, {
        message:
          "[acceptance:tool:artifact_write] [acceptance:workbench:artifact]",
        mode: "agent",
        model_id: bootstrapState.model_ids.chat!,
        request_id: randomUUID(),
      });
      const pending = await pollProjection(
        () => viewFor(page, session),
        (v) => v.run.status === "waiting",
        { timeout: 60_000, message: "owned team pending approval" },
      );
      async function inbox() {
        for (let offset = 0; ; offset += 200) {
          const items = (
            await appApi<any>(page, `/approvals?limit=200&offset=${offset}`)
          ).data.items;
          const selected = items.find(
            (item: any) =>
              item.run_id === pending.run.run_id &&
              item.source_entity_id === session,
          );
          if (selected) return selected;
          if (items.length < 200)
            throw new Error("owned actor approval missing");
        }
      }
      const approval = await inbox();
      const stream = `**/api/execution-runs/${pending.run.run_id}/events/stream*`;
      await page.route(stream, (route) => route.abort("connectionclosed"));
      let stalePosts = 0;
      const decisionPath = `/api/approval-batches/${approval.approval_id}/commands/decide`;
      page.on("request", (request) => {
        if (
          new URL(request.url()).pathname === decisionPath &&
          request.method() === "POST"
        )
          stalePosts++;
      });
      try {
        await page.goto(
          `/sessions/${session}?run=${pending.run.run_id}&panel=approval`,
        );
        await closeConversation(page);
        const reject = page
          .locator("[data-execution-detail]")
          .getByRole("button", { name: /^Reject$|^拒绝$/ });
        await expect(reject).toBeEnabled();
        await appApi(actor.page, decisionPath.slice(4), {
          method: "POST",
          body: {
            decision: "approved",
            feedback: "Different owned actor wins",
          },
          headers: { "X-Workspace-Id": team.id },
        });
        const won = await pollProjection(
          inbox,
          (item) => item.decision === "approved",
          { timeout: 60_000, message: "durable other actor winner" },
        );
        expect(won.decided_by_user_id).toBe(actor.cleanup.resource_id);
        // Keyboard activation exercises the stale control's current inbox revalidation.
        await reject.focus();
        await page.keyboard.press("Enter");
        const detail = page.locator("[data-execution-detail]");
        await detail.getByRole("textbox").fill("Stale decision revalidation");
        await detail
          .getByRole("button", { name: /^Confirm reject$|^确认拒绝$/ })
          .focus();
        await page.keyboard.press("Enter");
        await expect(detail).toContainText("approved");
        expect(stalePosts).toBe(0);
        const stable = await inbox();
        expect(stable.decided_by_user_id).toBe(actor.cleanup.resource_id);
        expect(stable.subject_activity_id).toBe(approval.subject_activity_id);
        await test.info().attach("different-actor-approval", {
          body: JSON.stringify({
            run_id: pending.run.run_id,
            approval_id: approval.approval_id,
            first_actor: operator.id,
            winning_actor: stable.decided_by_user_id,
            stale_posts: stalePosts,
          }),
          contentType: "application/json",
        });
      } finally {
        await page.unroute(stream);
      }
    } finally {
      await actor.context.close();
      await page.evaluate((userId) => {
        localStorage.removeItem("opencitadel-active-workspace");
        localStorage.removeItem(
          `opencitadel-active-workspace:${encodeURIComponent(userId)}`,
        );
      }, operator.id);
    }
  },
);
