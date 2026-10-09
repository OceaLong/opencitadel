import { randomUUID } from "node:crypto";
import type { Locator, Page } from "@playwright/test";
import { expect, appApi, test } from "../fixtures/acceptance.fixture";
import { registerCleanupAction } from "./cleanup-journal";
import { acceptanceId } from "./ids";
import { pollProjection } from "./poll";
import { settleRecordedBatch, type OwnedApproval } from "./batch-approvals";

export async function keyboardReach(
  page: Page,
  target: Locator,
): Promise<void> {
  await expect(target).toBeVisible();
  await expect(target).toBeEnabled();
  for (let count = 0; count < 240; count++) {
    if (await target.evaluate((node) => node === document.activeElement))
      return;
    await page.keyboard.press("Tab");
  }
  throw new Error("core control unreachable with Tab");
}
export async function keyboardActivate(
  page: Page,
  target: Locator,
): Promise<void> {
  await keyboardReach(page, target);
  await page.keyboard.press("Enter");
}
export async function keyboardApproveTarget(
  page: Page,
  target?: Locator,
): Promise<void> {
  // Batch callers supply the exact owned inbox button; do not rediscover it.
  if (target) {
    await keyboardActivate(page, target);
    return;
  }
  const approve = page.getByRole("button", { name: /^Approve$|^批准$/ });
  const open = page.getByRole("button", {
    name: /^Open approval actions$|^打开审批操作$/,
  });
  const entry = await pollProjection(
    async () =>
      (await approve.isVisible())
        ? "approve"
        : (await open.isVisible())
          ? "open"
          : "loading",
    (value) => value !== "loading",
    { message: "owned session approval has a visible keyboard entry" },
  );
  // On compact layouts the conversation can already cover the task pane.
  if (entry === "open") await keyboardActivate(page, open);
  await keyboardActivate(page, approve);
}

async function text(page: Page, target: Locator, value: string): Promise<void> {
  await keyboardReach(page, target);
  await page.keyboard.press("ControlOrMeta+A");
  await page.keyboard.type(value);
  await expect(target).toHaveValue(value);
}
export async function keyboardChoose(
  page: Page,
  target: Locator,
  value: string,
): Promise<void> {
  const label = await pollProjection(
    () =>
      target
        .locator("option")
        .evaluateAll(
          (options, wanted) =>
            options
              .find((option) => (option as HTMLOptionElement).value === wanted)
              ?.textContent?.trim(),
          value,
        ),
    (label) => Boolean(label),
    { message: "actual option has loaded before keyboard selection" },
  );
  await keyboardReach(page, target);
  // Native select typeahead commits on macOS Chromium, where closed-select
  // Home/ArrowDown does not change the value in headless browser automation.
  await page.keyboard.type(label!);
  await page.keyboard.press("Tab");
  // Identical labels (or shared prefixes) can select a different option. Native
  // first-character typeahead cycles matching options across fresh focus visits.
  const optionCount = await target.locator("option").count();
  for (
    let attempt = 0;
    (await target.inputValue()) !== value && attempt < optionCount;
    attempt++
  ) {
    await keyboardReach(page, target);
    await page.keyboard.type(label![0]);
    await page.keyboard.press("Tab");
  }
  await expect(target).toHaveValue(value);
}

export async function keyboardReconcileReview(page: Page): Promise<void> {
  const dimension = page.getByRole("combobox", { name: /^Dimension$|^维度$/ });
  const reconcile = page.getByRole("button", {
    name: /^Reconcile draft with current review$|^核对并采用当前复核版本$/,
  });
  const readiness = await pollProjection(
    async () =>
      (await reconcile.isVisible())
        ? "reconcile"
        : (await dimension.inputValue()) === "correctness"
          ? "ready"
          : "loading",
    (value) => value !== "loading",
    {
      message: "current review target is available for keyboard reconciliation",
    },
  );
  if (readiness === "reconcile") await keyboardActivate(page, reconcile);
  await expect(dimension).toHaveValue("correctness");
}

export async function keyboardEvaluationFlow(
  page: Page,
  source: {
    run: string;
    session: string;
    view: any;
    configVersion: string;
    rubricVersion: string;
    recordingVersion: string;
  },
  reflow: (page: Page) => Promise<void>,
): Promise<void> {
  const dataset = (
    await appApi<any>(page, "/evaluation/datasets", {
      method: "POST",
      body: {
        request_id: randomUUID(),
        expected_revision: 0,
        name: `${acceptanceId("keyboard-dataset")}-${randomUUID().slice(0, 8)}`,
      },
    })
  ).data;
  registerCleanupAction({
    action: "delete-resource",
    resource: "evaluation-dataset",
    resource_id: dataset.id,
  });
  await page.goto(
    `/sessions/${source.session}?run=${source.run}&at=${encodeURIComponent(source.view.at)}`,
  );
  await reflow(page);
  await keyboardActivate(
    page,
    page.getByRole("button", { name: /^Capture as case$|^沉淀为案例$/ }),
  );
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await reflow(page);
  await keyboardChoose(
    page,
    dialog.getByLabel(/^Dataset$|^测试集$/),
    dataset.id,
  );
  const modelStep = source.view.steps.find(
    (step: any) =>
      step.kind === "model" &&
      step.status === "completed" &&
      step.activity_id &&
      step.output_ref?.availability === "available",
  );
  expect(modelStep, "completed model has real readable output").toBeTruthy();
  await keyboardChoose(
    page,
    dialog.getByLabel(/^Source step$|^来源步骤$/),
    modelStep.step_id,
  );
  await text(
    page,
    dialog.getByLabel(/^Case key$|^案例标识$/),
    "keyboard-captured",
  );
  await keyboardActivate(
    page,
    dialog.getByRole("button", { name: /^Inspect$|^检查$/ }),
  );
  await keyboardActivate(
    page,
    dialog.getByRole("button", { name: /^Save case$|^保存案例$/ }),
  );
  const captured = await pollProjection(
    () =>
      appApi<any>(page, `/evaluation/datasets/${dataset.id}`).then(
        (r) => r.data,
      ),
    (draft) =>
      draft.cases.some((item: any) => item.case_key === "keyboard-captured"),
    { timeout: 30_000, message: "keyboard capture actually persisted" },
  );
  expect(
    captured.cases.find((item: any) => item.case_key === "keyboard-captured")
      .source_run_id,
  ).toBe(source.run);
  await keyboardActivate(
    page,
    dialog.getByRole("link", {
      name: /^Open dataset to edit and confirm$|^打开测试集.*确认$/,
    }),
  );
  await expect(page).toHaveURL(
    new RegExp(`/evaluations/datasets/${dataset.id}`),
  );
  await reflow(page);
  const caseKey =
    "keyboard-long-case-" + "readable-operational-evidence-".repeat(5);
  const original = captured.cases.find(
    (item: any) => item.case_key === "keyboard-captured",
  );
  const payload = JSON.stringify({
    schema_version: 1,
    cases: [
      {
        case_key: caseKey,
        input: original.input,
        reference_answer: "Workbench evidence",
        reference_confirmed: true,
        applicable_dimensions: ["correctness"],
      },
    ],
  });
  // Enter activates the real browser file chooser. setFiles supplies the owned
  // local payload at the OS picker boundary, not a mocked import API response.
  const chooser = page.waitForEvent("filechooser");
  const validated = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname ===
        `/api/evaluation/datasets/${dataset.id}/imports/validate` &&
      response.request().method() === "POST",
  );
  await keyboardActivate(
    page,
    page.getByLabel(/^Import JSON or CSV$|^导入 JSON 或 CSV$/),
  );
  await (
    await chooser
  ).setFiles({
    name: "owned-keyboard-cases.json",
    mimeType: "application/json",
    buffer: Buffer.from(payload),
  });
  const validationResponse = await validated;
  expect(validationResponse.status()).toBe(200);
  expect((await validationResponse.json()).data.errors).toEqual([]);
  const consent = page.getByRole("checkbox", {
    name: /^I reviewed the additions|我已.*新增/,
  });
  await keyboardReach(page, consent);
  await page.keyboard.press("Space");
  await expect(consent).toBeChecked();
  await keyboardActivate(
    page,
    page.getByRole("button", { name: /^Apply import$|^应用导入$/ }),
  );
  await pollProjection(
    () =>
      appApi<any>(page, `/evaluation/datasets/${dataset.id}`).then(
        (r) => r.data,
      ),
    (draft) => draft.cases.length === 1 && draft.cases[0].case_key === caseKey,
    { timeout: 30_000, message: "keyboard import applied" },
  );
  // The API can expose the applied revision before this editor adopts its
  // response. Publishing must use the revision displayed by the real editor.
  await expect(
    page.getByRole("cell", { name: caseKey, exact: true }),
  ).toBeVisible();
  const published = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/evaluation/datasets/${dataset.id}/publish`) &&
      response.request().method() === "POST",
  );
  await keyboardActivate(
    page,
    page.getByRole("button", {
      name: /^Publish fixed version$|^发布固定版本$/,
    }),
  );
  const publishResponse = await published;
  expect(publishResponse.status()).toBe(200);
  const version = (await publishResponse.json()).data;
  const suite = (
    await appApi<any>(page, "/evaluation/suites", {
      method: "POST",
      body: {
        request_id: randomUUID(),
        name: acceptanceId("keyboard-suite"),
        definition: {
          dataset_version: version.id,
          config_versions: [source.configVersion],
          rubric_version: source.rubricVersion,
          mode: "recorded",
          recording_versions: [source.recordingVersion],
          settings: {
            token_budget: 1_000_000,
            money_budget: 1,
            repeat: 1,
            seed: 5,
          },
        },
      },
    })
  ).data;
  registerCleanupAction({
    action: "delete-resource",
    resource: "evaluation-suite",
    resource_id: suite.id,
  });
  await page.goto(`/evaluations/suites/${suite.id}`);
  await reflow(page);
  const suitePublished = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/evaluation/suites/${suite.id}/publish`) &&
      response.request().method() === "POST",
  );
  await keyboardActivate(
    page,
    page.getByRole("button", {
      name: /^Publish fixed version$|^发布固定版本$/,
    }),
  );
  expect((await suitePublished).status()).toBe(200);
  await reflow(page);
  await keyboardActivate(
    page,
    page.getByRole("button", { name: /^Run preflight$|^执行预检$/ }),
  );
  const start = page.getByRole("button", { name: /^Start batch$|^开始批次$/ });
  await expect(start).toBeDisabled();
  const authorizes = page.getByRole("checkbox", {
    name: /^I reviewed preflight and authorize this batch|我已.*预检.*授权/,
  });
  await keyboardReach(page, authorizes);
  await page.keyboard.press("Space");
  await expect(authorizes).toBeChecked();
  const started = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/evaluation/batches" &&
      response.request().method() === "POST",
  );
  await keyboardActivate(page, start);
  const startResponse = await started;
  expect(startResponse.status()).toBe(202);
  const batch = (await startResponse.json()).data;
  registerCleanupAction({
    action: "delete-resource",
    resource: "evaluation-batch",
    resource_id: batch.id,
  });
  await settleRecordedBatch(
    (path) => appApi<any>(page, path).then((result) => result.data),
    batch.id,
    (approval) =>
      approveOwnedInInbox(page, approval, async (target, button) => {
        await reflow(target);
        await keyboardActivate(target, button);
      }),
  );
  await page.goto(`/evaluations/batches/${batch.id}`);
  await reflow(page);
  const matrix = page.getByRole("region", {
    name: /^Result matrix$|^结果矩阵$/,
  });
  await keyboardActivate(
    page,
    matrix.getByRole("button", { name: caseKey, exact: true }),
  );
  await expect(matrix.locator("p").filter({ hasText: caseKey })).toBeVisible();
  await keyboardActivate(page, matrix.locator("tbody td button").first());
  await reflow(page);
  await expect(
    page.getByRole("heading", { name: /^Score details|^评分详情/ }),
  ).toBeVisible();
  await keyboardReconcileReview(page);
  await text(page, page.getByLabel(/^New value$|^新值$/), "3");
  await text(
    page,
    page.getByLabel(/^Reason$|^理由$/),
    "Keyboard-only reviewed evidence",
  );
  await keyboardActivate(
    page,
    page.getByRole("button", { name: /^Submit review$|^提交复核$/ }),
  );
  await expect(page.locator('section[data-source="human"]')).toContainText(
    "Keyboard-only reviewed evidence",
  );
  const summary = (
    await appApi<any>(page, `/evaluation/batches/${batch.id}/summary`)
  ).data;
  expect(summary.items).toHaveLength(1);
  const configLabel = summary.items[0].config_label;
  expect(configLabel.length).toBeGreaterThan(100);
  for (const locale of ["en", "zh"])
    for (const theme of ["light", "dark"]) {
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
      await page.goto(
        `/evaluations/batches/${batch.id}?result=${summary.items[0].id}`,
      );
      await reflow(page);
      const region = page.getByRole("region", {
        name: /^Result matrix$|^结果矩阵$/,
      });
      await keyboardActivate(
        page,
        region.getByRole("button", { name: caseKey, exact: true }),
      );
      const full = region.locator("p").filter({ hasText: caseKey });
      await expect(full).toBeVisible();
      expect(
        await full.evaluate(
          (node) =>
            node.scrollWidth <= node.clientWidth + 1 &&
            node.scrollHeight <= node.clientHeight + 1,
        ),
      ).toBe(true);
      const distribution = page
        .getByRole("heading", {
          name: /^Score distribution by configuration$|^各配置评分分布$/,
        })
        .locator("..");
      const legend = distribution.getByText("1: " + configLabel, {
        exact: true,
      });
      await expect(legend).toBeVisible();
      expect(
        await legend.evaluate(
          (node) =>
            node.getBoundingClientRect().left >= 0 &&
            node.getBoundingClientRect().right <= innerWidth + 1,
        ),
      ).toBe(true);
      await expect
        .poll(() =>
          page.evaluate(
            () => document.documentElement.scrollWidth <= innerWidth + 1,
          ),
        )
        .toBe(true);
    }

  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    )
    .toBe(true);
  await test.info().attach("keyboard-core-completion", {
    body: JSON.stringify({
      dataset: dataset.id,
      version: version.id,
      suite: suite.id,
      batch: batch.id,
      actions: [
        "Tab",
        "Enter",
        "Space",
        "native select typeahead",
        "typed input",
      ],
      file_chooser_handoff: "owned JSON payload through real chooser",
      reduced_motion: await page.evaluate(
        () => matchMedia("(prefers-reduced-motion: reduce)").matches,
      ),
    }),
    contentType: "application/json",
  });
}

/** Locate the actual new evaluation approval; standalone run pages are read-only. */
export async function approveOwnedInInbox(
  page: Page,
  approval: OwnedApproval,
  activate: (page: Page, button: Locator) => Promise<void>,
) {
  const loaded = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/approvals" &&
      response.request().method() === "GET",
  );
  await page.goto("/approvals");
  async function rendered(response: import("@playwright/test").Response) {
    expect(response.status()).toBe(200);
    const items = (await response.json()).data.items;
    if (items.length)
      await expect(
        page.locator(`[data-approval-id="${items[0].approval_id}"]`),
      ).toBeVisible();
  }
  await rendered(await loaded);
  const row = page.locator(
    `[data-approval-id="${approval.approval_id}"][data-run-id="${approval.run_id}"][data-subject-activity-id="${approval.subject_activity_id}"]`,
  );
  for (let index = 0; index < 100 && (await row.count()) === 0; index++) {
    const next = page.getByRole("button", { name: /^Next$|^下一页$/ });
    await expect(next).toBeEnabled();
    const loaded = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === "/api/approvals" &&
        response.request().method() === "GET",
    );
    await activate(page, next);
    await rendered(await loaded);
  }
  await expect(row).toHaveCount(1);
  const decisionPath = `/api/approval-batches/${approval.approval_id}/commands/decide`;
  const decided = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === decisionPath &&
      response.request().method() === "POST",
  );
  await activate(page, row.getByRole("button", { name: /^Approve$|^批准$/ }));
  const response = await decided;
  expect(response.request().postDataJSON().decision).toBe("approved");
  expect(response.status()).toBe(200);
  await pollProjection(
    async () => {
      for (let offset = 0; ; offset += 200) {
        const inbox = (
          await appApi<any>(page, `/approvals?limit=200&offset=${offset}`)
        ).data;
        const found = inbox.items.find(
          (item: any) => item.approval_id === approval.approval_id,
        );
        if (found || inbox.items.length < 200) return found;
      }
    },
    (item) =>
      item?.status === "approved" &&
      item.run_id === approval.run_id &&
      item.subject_activity_id === approval.subject_activity_id,
    { timeout: 30_000, message: "exact new evaluation approval accepted" },
  );
}
