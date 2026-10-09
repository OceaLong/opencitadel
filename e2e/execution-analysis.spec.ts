import { zoomTest } from "./fixtures/native-zoom.fixture";
import { selectHistoryCuts } from "./support/execution-history";
import { strictScenario } from "./support/strict-driver";
import { randomUUID } from "node:crypto";
import type { Locator, Page } from "@playwright/test";
import { appApi, expect, test } from "./fixtures/acceptance.fixture";
import {
  bindExportDownload,
  registerCleanupAction,
} from "./support/cleanup-journal";
import { acceptanceId } from "./support/ids";
import {
  createSession,
  readChatStream,
  waitForTerminalProfile,
} from "./support/execution";
import { pollProjection } from "./support/poll";
import { settleRecordedBatch } from "./support/batch-approvals";

type Version = { id: string; revision: number };
async function publish(
  page: Page,
  kind: "config" | "rubric" | "suite",
  definition: unknown,
  name?: string,
  ownedWorkspaceId?: string | null,
) {
  const workspaceId =
    ownedWorkspaceId ??
    (await page.evaluate(() =>
      localStorage.getItem("opencitadel-active-workspace"),
    ));
  const headers = workspaceId ? { "X-Workspace-Id": workspaceId } : {};
  const draft = (
    await appApi<Version>(page, `/evaluation/${kind}s`, {
      method: "POST",
      headers,
      body: {
        request_id: randomUUID(),
        name: name ?? acceptanceId(kind),
        definition,
      },
    })
  ).data;
  registerCleanupAction({
    ...(workspaceId ? { workspace_id: workspaceId } : {}),
    action: "delete-resource",
    resource: `evaluation-${kind}`,
    resource_id: draft.id,
  });
  return (
    await appApi<Version>(page, `/evaluation/${kind}s/${draft.id}/publish`, {
      method: "POST",
      headers,
      body: { request_id: randomUUID(), expected_revision: draft.revision },
    })
  ).data;
}

/** Every locator is returned by the current test's product writes. No seeded production IDs. */
async function closedLoop(
  page: Page,
  modelId: string,
  approve?: (page: Page, target?: Locator) => Promise<void>,
) {
  const workspaceId = await page.evaluate(() =>
    localStorage.getItem("opencitadel-active-workspace"),
  );
  const scopedApi = <T>(
    path: string,
    init: Parameters<typeof appApi>[2] = {},
  ) =>
    appApi<T>(page, path, {
      ...init,
      headers: {
        ...(workspaceId ? { "X-Workspace-Id": workspaceId } : {}),
        ...init.headers,
      },
    });
  const session = await createSession(
    page,
    acceptanceId("analysis-loop"),
    "agent",
    modelId,
  );
  await readChatStream(page, session, {
    message:
      "[acceptance:evaluation:artifact] [acceptance:tool:artifact_write] [acceptance:workbench:artifact]",
    mode: "agent",
    model_id: modelId,
    request_id: randomUUID(),
  });
  const runs = (
    await scopedApi<any>(
      `/execution-runs?source_entity_type=session&source_entity_id=${session}`,
    )
  ).data;
  expect(runs.items).toHaveLength(1);
  const run = runs.items[0].run_id;
  const before = await pollProjection(
    () => scopedApi<any>(`/execution-runs/${run}/view`).then((r) => r.data),
    (v) => v.run.status === "waiting",
    { timeout: 60_000, message: "persisted approval boundary before artifact" },
  );
  expect(before.at).toBeTruthy();
  expect(before.artifacts).toEqual([]);
  await page.goto(`/sessions/${session}`);
  if (approve) await approve(page);
  else {
    const pending = (
      await scopedApi<any>("/approvals?status=pending&limit=200")
    ).data.items.filter((item: any) => item.run_id === run);
    expect(pending).toHaveLength(1);
    expect(pending[0].subject_activity_id).toBeTruthy();
    await scopedApi(
      `/approval-batches/${pending[0].approval_id}/commands/decide`,
      {
        method: "POST",
        ...(workspaceId ? { headers: { "X-Workspace-Id": workspaceId } } : {}),
        body: {
          decision: "approved",
          feedback: "Exact owned analysis loop",
        },
      },
    );
  }
  await waitForTerminalProfile(page, session, "completed");
  const view = await pollProjection(
    () => scopedApi<any>(`/execution-runs/${run}/view`).then((r) => r.data),
    (v) => v.run.status === "completed" && v.artifacts?.length > 0,
    { timeout: 60_000, message: "owned artifact persisted" },
  );
  const artifact = view.artifacts[0];
  const modelStep = view.steps.find(
    (step: any) => step.kind === "model" && step.status === "completed",
  );
  expect(modelStep).toBeTruthy();
  const sources = (
    await scopedApi<any>(`/evaluation/recordings/sources/${run}`)
  ).data.items;
  expect(sources.length).toBeGreaterThan(0);
  expect(sources.map((source: any) => source.tool).sort()).toEqual([
    "__retrieval__",
    "artifact_write",
  ]);
  const recording = (
    await scopedApi<any>("/evaluation/recordings", {
      method: "POST",
      expectStatus: 202,
      body: {
        request_id: randomUUID(),
        run_id: run,
        selections: sources.map((source: any) => ({
          tool: source.tool,
          // artifact_write.data contains a private storage locator. The
          // recording worker must reject it rather than publish a redaction.
          allowed_fields: source.result_fields
            .map((field: any) => field.name)
            .filter(
              (name: string) =>
                source.tool !== "artifact_write" || name !== "data",
            ),
        })),
      },
    })
  ).data;
  registerCleanupAction({
    ...(workspaceId ? { workspace_id: workspaceId } : {}),
    action: "delete-resource",
    resource: "evaluation-recording",
    resource_id: recording.id,
  });
  await pollProjection(
    () =>
      scopedApi<any>(`/evaluation/recordings/${recording.id}`).then(
        (r) => r.data,
      ),
    (v) => v.status === "ready",
    { timeout: 120_000, message: "recording publication" },
  );
  const recordingVersion = (
    await scopedApi<any>(`/evaluation/recordings/${recording.id}/result`)
  ).data.version_id;
  let dataset = (
    await scopedApi<any>("/evaluation/datasets", {
      method: "POST",
      body: {
        request_id: randomUUID(),
        expected_revision: 0,
        name: acceptanceId("closed-loop"),
      },
    })
  ).data;
  registerCleanupAction({
    ...(workspaceId ? { workspace_id: workspaceId } : {}),
    action: "delete-resource",
    resource: "evaluation-dataset",
    resource_id: dataset.id,
  });
  dataset = (
    await scopedApi<any>(`/evaluation/datasets/${dataset.id}/from-run`, {
      method: "POST",
      body: {
        request_id: randomUUID(),
        expected_revision: dataset.revision,
        run_id: run,
        step_id: modelStep.step_id,
        at: view.at,
        case_key: "artifact",
      },
    })
  ).data;
  const captured = dataset.cases.find(
    (value: any) => value.case_key === "artifact",
  );
  expect(captured.source_run_id).toBe(run);
  dataset = (
    await scopedApi<any>(`/evaluation/datasets/${dataset.id}/cases/artifact`, {
      method: "PATCH",
      body: {
        request_id: randomUUID(),
        expected_revision: dataset.revision,
        case: {
          input: captured.input,
          history: [
            ...captured.history,
            { role: "user", content: "Please use the next task verbatim." },
            { role: "assistant", content: "Ready for the task." },
          ],
          input_confirmed: true,
          reference_answer: "Workbench evidence",
          reference_confirmed: true,
          applicable_dimensions: ["correctness"],
        },
      },
    })
  ).data;
  expect(
    dataset.cases.find((value: any) => value.case_key === "artifact")
      ?.input_status,
  ).toBe("edited");
  const version = (
    await scopedApi<Version>(`/evaluation/datasets/${dataset.id}/publish`, {
      method: "POST",
      body: { request_id: randomUUID(), expected_revision: dataset.revision },
    })
  ).data;
  const config = await publish(
    page,
    "config",
    {
      model_id: modelId,
      mode: "agent",
      max_output_tokens: 4096,
      tool_names: ["artifact_write"],
      external_contract_ref: {
        kind: "recording",
        version_id: recordingVersion,
      },
    },
    approve
      ? "Keyboard configuration " + "observable long label ".repeat(6)
      : undefined,
    workspaceId,
  );
  const judge = await publish(
    page,
    "config",
    {
      model_id: modelId,
      purpose: "evaluation_judge",
      mode: "ask",
      max_output_tokens: 4096,
    },
    undefined,
    workspaceId,
  );
  const rubric = await publish(
    page,
    "rubric",
    {
      judge_config_version: judge.id,
      dimensions: [
        {
          id: "correctness",
          name: "Correctness",
          anchors: [
            "Wrong",
            "Major errors",
            "Partial",
            "Mostly correct",
            "Correct",
          ],
          evidence_required: false,
        },
      ],
      required_conditions: [
        { dimension_id: "correctness", source: "human", minimum: 3 },
      ],
    },
    undefined,
    workspaceId,
  );
  const suite = await publish(
    page,
    "suite",
    {
      dataset_version: version.id,
      config_versions: [config.id],
      rubric_version: rubric.id,
      mode: "recorded",
      recording_versions: [recordingVersion],
      settings: {
        token_budget: 1_000_000,
        money_budget: 1,
        repeat: 1,
        seed: 5,
      },
    },
    undefined,
    workspaceId,
  );
  const preflight = (
    await scopedApi<any>("/evaluation/batches/preflight", {
      method: "POST",
      body: { suite_version: suite.id },
    })
  ).data;
  expect(preflight.allowed, JSON.stringify(preflight.errors)).toBe(true);
  const batch = (
    await scopedApi<any>("/evaluation/batches", {
      method: "POST",
      expectStatus: 202,
      body: {
        request_id: randomUUID(),
        suite_version: suite.id,
        preflight_revision: preflight.revision,
      },
    })
  ).data;
  registerCleanupAction({
    ...(workspaceId ? { workspace_id: workspaceId } : {}),
    action: "delete-resource",
    resource: "evaluation-batch",
    resource_id: batch.id,
  });
  await settleRecordedBatch(
    (path) => scopedApi<any>(path).then((result) => result.data),
    batch.id,
    async (approval) => {
      if (approve) {
        const { approveOwnedInInbox } =
          await import("./support/keyboard-evaluation");
        await approveOwnedInInbox(page, approval, (target, button) =>
          approve(target, button),
        );
      } else {
        await scopedApi(
          `/approval-batches/${approval.approval_id}/commands/decide`,
          {
            method: "POST",
            body: {
              decision: "approved",
              feedback: "Current owned recorded artifact evaluation",
            },
          },
        );
      }
    },
  );
  const results = (
    await scopedApi<any>(`/evaluation/batches/${batch.id}/results`)
  ).data.items;
  expect(results).toHaveLength(1);
  const resultId = results[0].id;
  const review = (
    await scopedApi<any>(`/evaluation/results/${resultId}/review-context`)
  ).data;
  await scopedApi(`/evaluation/results/${resultId}/scores`, {
    method: "POST",
    body: {
      request_id: randomUUID(),
      expected_revision: review.evaluation_revision,
      expected_result_revision: review.result_revision,
      rubric_version: rubric.id,
      scores: [
        {
          dimension: "correctness",
          value: 3,
          status: "valid",
          reason: "Owned closed-loop review",
        },
      ],
    },
  });
  const history = (
    await scopedApi<any>(`/evaluation/results/${resultId}/scores`)
  ).data;
  expect(
    history.items.some(
      (entry: any) => entry.score.source === "human" && entry.score.value === 3,
    ),
  ).toBe(true);
  const comparison = (
    await scopedApi<any>("/execution-comparisons", {
      method: "POST",
      expectStatus: 201,
      body: {
        request_id: randomUUID(),
        mode: "explicit",
        run_ids: [run, results[0].run_id],
        detail_run_ids: [run],
      },
    })
  ).data;
  registerCleanupAction({
    ...(workspaceId ? { workspace_id: workspaceId } : {}),
    action: "delete-resource",
    resource: "execution-comparison",
    resource_id: comparison.comparison_id,
    retained_revision: comparison.revision,
  });
  expect(comparison.members.map((member: any) => member.run_id).sort()).toEqual(
    [run, results[0].run_id].sort(),
  );
  return {
    batchId: batch.id,
    configVersion: config.id,
    recordingVersion,
    suiteVersion: suite.id,
    preflightRevision: preflight.revision,
    rubricVersion: rubric.id,
    comparisonId: comparison.comparison_id,
    revision: comparison.revision,
    resultId,
    artifactTitle: "Workbench evidence",
    artifact,
    run,
    before,
    view,
    session,
  };
}

test(
  "AC04 owned artifact, case, suite, batch, human review and comparison retain exact historical boundaries",
  { annotation: { type: "acceptance", description: "AC04" } },
  async ({ operatorPage: page, bootstrapState }) => {
    test.setTimeout(600_000);
    const loop = await closedLoop(page, bootstrapState.model_ids.chat!);
    const timeline = (
      await appApi<any>(
        page,
        `/execution-runs/${loop.run}/timeline?${new URLSearchParams({
          start: loop.view.run.admitted_at,
          end: loop.view.run.latest_available,
          bucket_count: "200",
        })}`,
      )
    ).data;
    expect(timeline.key_events.length).toBeLessThan(200); // Never claim complete boundary search after truncation.
    const historicalViews: any[] = [];
    for (const event of timeline.key_events) {
      const value = (
        await appApi<any>(
          page,
          `/execution-runs/${loop.run}/view?at=${encodeURIComponent(event.at)}`,
        )
      ).data;
      expect(value.next_cursor).toBeNull();
      historicalViews.push(value);
    }
    // The final live cut is itself issued by the server; no timestamp arithmetic.
    historicalViews.push(loop.view);
    const cuts = selectHistoryCuts(historicalViews);
    expect(cuts.before.approvals).toEqual([]);
    expect(
      cuts.pending.approvals.every((row: any) => row.decision === null),
    ).toBe(true);
    expect(
      cuts.decided.approvals.some((row: any) => row.decision === "approved"),
    ).toBe(true);
    // View steps are ordered by their last observation, newest first.
    // Reversing them selects the earlier tool-request model, before approval.
    const finalModel = loop.view.steps.find(
      (step: any) =>
        step.kind === "model" &&
        step.status === "completed" &&
        step.output_ref?.availability === "available",
    );
    expect(
      finalModel,
      "actual persisted final model output required",
    ).toBeTruthy();
    const artifactProducer = loop.view.steps.find((step: any) =>
      step.artifact_refs?.some(
        (ref: any) =>
          ref.artifact_id === loop.artifact.artifact_id &&
          ref.version === loop.artifact.version,
      ),
    );
    expect(
      artifactProducer,
      "actual artifact producer step required",
    ).toBeTruthy();
    const outputPath = `/execution-runs/${loop.run}/steps/${encodeURIComponent(finalModel.step_id)}/content`;
    const finalOutput = (
      await appApi<any>(
        page,
        `${outputPath}?at=${encodeURIComponent(loop.view.at)}&content_kind=output`,
      )
    ).data;
    expect(finalOutput.availability).toBe("available");
    expect(finalOutput.content.length).toBeGreaterThan(0);
    const finalMessage = loop.view.messages.find(
      (message: any) => message.role === "assistant",
    );
    expect(
      finalMessage,
      "actual final assistant message required",
    ).toBeTruthy();
    let historicalWrites = 0;
    const onRequest = (request: import("@playwright/test").Request) => {
      if (
        request.method() === "POST" &&
        request.url().includes("/commands/decide")
      )
        historicalWrites++;
    };
    page.on("request", onRequest);
    try {
      for (const cut of [cuts.before, cuts.pending, cuts.decided]) {
        expect(cut.artifacts).toEqual([]);
        expect(cut.run.public_summary).toBeNull();
        expect(
          cut.messages.some(
            (message: any) => message.message_id === finalMessage.message_id,
          ),
        ).toBe(false);
        expect(
          cut.steps.some(
            (step: any) =>
              step.step_id === finalModel.step_id &&
              step.output_ref?.availability === "available",
          ),
        ).toBe(false);
        const futureOutput = await appApi<any>(
          page,
          `${outputPath}?at=${encodeURIComponent(cut.at)}&content_kind=output`,
          { expectStatus: [200, 404] },
        );
        if (futureOutput.status === 200) {
          expect(futureOutput.data.availability).toBe("unavailable");
          expect(futureOutput.data.content).toBeNull();
        }
        const futureArtifact = await appApi<any>(
          page,
          `/execution-artifacts/${loop.artifact.artifact_id}/content?${new URLSearchParams({ version: String(loop.artifact.version), run_id: loop.run, step_id: artifactProducer.step_id, at: cut.at })}`,
          { expectStatus: [200, 404] },
        );
        if (futureArtifact.status === 200) {
          expect(futureArtifact.data.availability).toBe("unavailable");
          expect(futureArtifact.data.content).toBeNull();
        }
        await page.goto(
          `/runs/${loop.run}?at=${encodeURIComponent(cut.at)}&panel=approval`,
        );
        await expect(page.getByTestId("workbench")).toBeVisible();
        await expect(
          page.getByRole("button", {
            name: /^Approve$|^批准$|^Reject$|^拒绝$/,
          }),
        ).toHaveCount(0);
        await expect(page.getByTestId("workbench")).not.toContainText(
          loop.artifactTitle,
        );
        const reread = (
          await appApi<any>(
            page,
            `/execution-runs/${loop.run}/view?at=${encodeURIComponent(cut.at)}`,
          )
        ).data;
        expect(reread.approvals).toEqual(cut.approvals);
        expect(reread.artifacts).toEqual(cut.artifacts);
        expect(reread.messages).toEqual(cut.messages);
        expect(reread.run.public_summary).toBe(cut.run.public_summary);
      }
      expect(historicalWrites).toBe(0);
    } finally {
      page.off("request", onRequest);
    }
    await page.goto(
      `/analysis/comparisons/${loop.comparisonId}?revision=${loop.revision}`,
    );
    await expect(
      page.getByRole("heading", { name: /^Fixed comparison$|^固定比较$/ }),
    ).toBeVisible();
    await page.getByRole("button", { name: loop.run, exact: true }).click();
    await expect(page.getByTestId("workbench")).toBeVisible();
    await page.goto(
      `/runs/${loop.run}?at=${encodeURIComponent(loop.view.at)}&artifact=${loop.artifact.artifact_id}&version=${loop.artifact.version}&panel=artifact`,
    );
    await page
      .getByRole("button", { name: /^Load artifact$|^加载成果$/ })
      .click();
    await expect(page.getByTestId("detail-panel")).toContainText(
      loop.artifactTitle,
    );
    const historical = (
      await appApi<any>(
        page,
        `/execution-runs/${loop.run}/view?at=${encodeURIComponent(loop.before.at)}`,
      )
    ).data;
    expect(historical.artifacts).toEqual([]);
    expect(historical.run.status).toBe("waiting");
    await page.goto(
      `/runs/${loop.run}?at=${encodeURIComponent(loop.before.at)}`,
    );
    await expect(page.getByTestId("workbench")).toBeVisible();
    await expect(
      page.getByRole("button", { name: /^Approve$|^批准$/, exact: true }),
    ).toHaveCount(0);
    await expect(page.getByTestId("workbench")).not.toContainText(
      loop.artifactTitle,
    );
  },
);

test(
  "AC21 consumes separately measured matching-build capacity evidence",
  { annotation: { type: "acceptance", description: "AC21" } },
  async () => {
    const { readFileSync } = await import("node:fs");
    const { resolve } = await import("node:path");
    const { createHash } = await import("node:crypto");
    expect(
      process.env.ACCEPTANCE_EVIDENCE_DIR,
      "capacity is only accepted through the owning runner",
    ).toBeTruthy();
    const root = process.env.ACCEPTANCE_EVIDENCE_DIR!;
    const receipt = JSON.parse(
      readFileSync(resolve(root, "capacity-validation.json"), "utf8"),
    );
    expect(receipt.schema_version).toBe(2);
    expect(receipt.run_id).toBe(process.env.ACCEPTANCE_RUN_ID);
    expect(receipt.project).toBe(process.env.ACCEPTANCE_PROJECT_ID);
    expect(
      receipt.errors,
      "A06 capacity is missing, stale, incomplete, non-reference or over budget",
    ).toEqual([]);
    const bytes = readFileSync(resolve(root, "capacity/report.json"));
    expect(createHash("sha256").update(bytes).digest("hex")).toBe(
      receipt.report_sha256,
    );
    expect(JSON.parse(bytes.toString()).binding).toEqual(receipt.binding);
  },
);

test(
  "AC02 retains legal parallel ties, unknown parents and business outcome independently of run status",
  { annotation: { type: "acceptance", description: "AC02" } },
  async ({ operatorPage: page }) => {
    const parallel = strictScenario("AC02", "parallel_order");
    const missing = strictScenario("AC02", "missing_parent");
    const business = strictScenario("AC02", "business_failure_successful_run");
    expect(missing.resource_ids).toEqual(parallel.resource_ids);
    expect(business.resource_ids).toEqual(parallel.resource_ids);
    const id = parallel.resource_ids.run_id;
    const view = (await appApi<any>(page, `/execution-runs/${id}/view`)).data;
    expect(view.steps).toEqual(parallel.after.steps);
    expect(new Set(view.steps.map((step: any) => step.activity_id)).size).toBe(
      3,
    );
    expect(view.run.duration_ms).toBe(3000);
    expect(view.run.status).toBe("completed");
    const failure = view.steps.find(
      (step: any) => step.business_outcome === "failure",
    );
    expect(failure.status).toBe("completed");
    expect(failure.parent_step_id).toBeNull();
    expect(failure.relationship).toBe("unknown");
    expect(failure.completeness.missing_fields).toContain("parent_step_id");
    await page.goto(
      `/runs/${id}?view=debug&step=${encodeURIComponent(failure.step_id)}&attempt=${failure.attempt_id}&panel=overview`,
    );
    await expect(page.getByTestId("workbench")).toBeVisible();
    await expect(page.getByTestId("detail-panel")).toContainText(
      /unknown|未知|missing|缺失/i,
    );
    await expect(page.getByTestId("trace-view")).toBeVisible();
  },
);

test(
  "AC05 binds real shadow activation and separate legacy history gap to current usable view",
  { annotation: { type: "acceptance", description: "AC05" } },
  async ({ operatorPage: page }) => {
    const shadow = strictScenario("AC05", "shadow_activation");
    const legacy = strictScenario("AC05", "missing_history_available_segment");
    expect(legacy.database.main_database_distinct).toBe(true);
    expect(legacy.after.view.run.completeness.state).toBe("partial");
    expect(
      legacy.after.view.run.completeness.missing_intervals.some(
        (interval: any) =>
          interval.reason === "pre_journal_progress_unavailable",
      ),
    ).toBe(true);
    expect(legacy.after.view.steps.length).toBeGreaterThan(0);
    const view = (
      await appApi<any>(
        page,
        `/execution-runs/${shadow.resource_ids.run_id}/view`,
      )
    ).data;
    expect(view.steps).toEqual(shadow.after.steps);
    expect(view.revision).toBe(shadow.after.revision);
    expect(view.at).toBeTruthy();
    const historical = (
      await appApi<any>(
        page,
        `/execution-runs/${view.run.run_id}/view?at=${encodeURIComponent(view.at)}`,
      )
    ).data;
    expect(historical.steps).toEqual(view.steps);
    await page.goto(
      `/runs/${view.run.run_id}?at=${encodeURIComponent(view.at)}`,
    );
    await expect(page.getByTestId("playback-controls")).toBeVisible();
    await expect(
      page.getByRole("button", { name: /^Return (to )?live$|^返回实时$/ }),
    ).toBeVisible();
  },
);

test(
  "AC19 actual controlled historical sources render zero, one and seven discrete UTC buckets",
  { annotation: { type: "acceptance", description: "AC19" } },
  async ({ operatorPage: page }) => {
    test.setTimeout(240_000);
    const dated = strictScenario("AC19", "seven_daily_buckets");
    const start = new Date(dated.before.filters.start);
    for (const count of [0, 1, 7]) {
      const filters = {
        ...dated.before.filters,
        start:
          count === 0
            ? new Date(start.getTime() - 86400000).toISOString()
            : start.toISOString(),
        end: new Date(
          start.getTime() + (count === 0 ? 0 : count * 86400000),
        ).toISOString(),
      };
      const query = new URLSearchParams({
        filters: JSON.stringify(filters),
        grain: "day",
        timezone: "UTC",
      });
      const captured = await pollProjection(
        async () => {
          const summary = await appApi<any>(
            page,
            `/execution-analysis/summary?${query}`,
            { expectStatus: [200, 409] },
          );
          if (summary.status === 409) {
            expect(summary.data.code).toBe("resource_unavailable");
            return null;
          }
          const runs = await appApi<any>(
            page,
            `/execution-analysis/runs?${query}&watermark=${encodeURIComponent(summary.data.watermark)}`,
            { expectStatus: [200, 409] },
          );
          if (runs.status === 409) {
            expect(runs.data.code).toBe("resource_unavailable");
            return null;
          }
          return { summary: summary.data, runs: runs.data };
        },
        (value) => value !== null,
        { timeout: 150_000, message: "fresh historical analysis capture" },
      );
      const { summary, runs } = captured!;
      expect(summary.timezone).toBe("UTC");
      expect(
        new Set(summary.metrics.series.map((row: any) => row.group.bucket))
          .size,
      ).toBe(count);
      expect(
        summary.metrics.series.reduce(
          (total: number, row: any) => total + row.metrics.run_count.value,
          0,
        ),
      ).toBe(count);
      expect(runs.items.map((row: any) => row.run_id).sort()).toEqual(
        dated.resource_ids.run_ids.slice(0, count).sort(),
      );
      for (const locale of ["en", "zh"])
        for (const theme of ["light", "dark"]) {
          await page.context().addCookies([
            {
              name: "NEXT_LOCALE",
              value: locale,
              url: process.env.PLAYWRIGHT_BASE_URL!,
            },
          ]);
          await page.evaluate(
            (value) => localStorage.setItem("opencitadel-theme", value),
            theme,
          );
          await page.goto(
            `/analysis?${query}&watermark=${encodeURIComponent(summary.watermark)}`,
          );
          await expect(
            page.getByRole("heading", {
              name: /^Execution analysis$|^运行分析$/,
            }),
          ).toBeVisible();
          // The empty chart also has no curves or Run buttons while loading.
          // Wait for the real capture before the next locale/theme navigation.
          await expect(
            page.locator(
              '[data-native-view="analysis"][data-native-ready="true"]',
            ),
          ).toBeVisible({ timeout: 150_000 });
          expect(
            await page.evaluate(
              () => document.documentElement.scrollWidth <= innerWidth + 1,
            ),
          ).toBe(true);
          // The real chart contract uses discrete marks below eight observed buckets.
          await expect(page.locator(".recharts-line-curve")).toHaveCount(0);
          for (const id of dated.resource_ids.run_ids.slice(0, count))
            await expect(
              page.getByRole("button", { name: id, exact: true }),
            ).toBeVisible();
        }
    }
  },
);

test(
  "AC22 actual old aggregate replay and formal rebuild coexist with current native entrypoint",
  { annotation: { type: "acceptance", description: "AC22" } },
  async ({ operatorPage: page }) => {
    const legacy = strictScenario("AC22", "upcast_hash_replay");
    strictScenario("AC22", "legacy_coexistence");
    const old = strictScenario("AC22", "old_entrypoint");
    expect(legacy.assertions.every((check: any) => check.passed === true)).toBe(
      true,
    );
    expect(
      legacy.assertions.find(
        (check: any) => check.id === "formal_rebuild_equal",
      ),
    ).toBeTruthy();
    expect(legacy.after.events).toBeGreaterThan(legacy.before.events);
    expect(legacy.cleanup.state).toBe("disposed");
    expect(old.after.view.run.status).toBe("completed");
    // Legacy database is explicitly distinct: current UI uses the current shared-stack owned run.
    const current = strictScenario("AC02", "parallel_order");
    await page.goto(`/sessions/${current.resource_ids.session_id}`);
    await expect(page.getByTestId("workbench")).toBeVisible();
    const view = (
      await appApi<any>(
        page,
        `/execution-runs/${current.resource_ids.run_id}/view`,
      )
    ).data;
    expect(view.run.status).toBe("completed");
  },
);

test(
  "AC17 repeated tool names retain ambiguity and authored pairing revisions without changing statistics",
  { annotation: { type: "acceptance", description: "AC17" } },
  async ({ operatorPage: page, bootstrapState }) => {
    test.setTimeout(480_000);
    const { completedOwnedRun } = await import("./support/owned-execution");
    const sources = [];
    for (let index = 0; index < 2; index++) {
      const source = await completedOwnedRun(
        page,
        bootstrapState.model_ids.chat!,
        "[acceptance:evaluation:repeated-tool] [acceptance:tool:shell_execute]",
      );
      const tools = source.view.steps.filter(
        (step: any) =>
          step.kind === "tool" && step.tool_name === "shell_execute",
      );
      expect(tools).toHaveLength(2);
      expect(new Set(tools.map((step: any) => step.activity_id)).size).toBe(2);
      sources.push({ ...source, tools });
    }
    sources.sort((a, b) => a.view.run.run_id.localeCompare(b.view.run.run_id));
    const runs = sources.map((source) => source.view.run.run_id);
    const created = await appApi<any>(page, "/execution-comparisons", {
      method: "POST",
      expectStatus: [201, 409],
      body: {
        request_id: randomUUID(),
        mode: "explicit",
        run_ids: runs,
        detail_run_ids: runs,
      },
    });
    if (created.status === 409) {
      await test.info().attach("comparison-source-boundaries", {
        body: JSON.stringify({
          error: created.data?.code,
          sources: sources.map((source) => ({
            run_id: source.view.run.run_id,
            source: source.view.run.source,
            status: source.view.run.status,
            admitted_at: source.view.run.admitted_at,
            at: source.view.at,
            tool_count: source.tools.length,
          })),
        }),
        contentType: "application/json",
      });
    }
    expect(created.status, "owned comparison source availability").toBe(201);
    const comparison = created.data;
    registerCleanupAction({
      action: "delete-resource",
      resource: "execution-comparison",
      resource_id: comparison.comparison_id,
      retained_revision: comparison.revision,
    });
    const read = () =>
      appApi<any>(
        page,
        `/execution-comparisons/${comparison.comparison_id}?revision=${comparison.revision}&${runs.map((id) => `detail_run_ids=${id}`).join("&")}`,
      ).then((value) => value.data);
    const original = await read();
    const repeated = original.suggestions.filter((row: any) =>
      sources[0].tools.some((step: any) => row.left.step_id === step.step_id),
    );
    expect(repeated).toHaveLength(2);
    for (const row of repeated) {
      expect(row.status).toBe("unmatched");
      expect(row.right).toBeNull();
      expect(row.provenance).toBe("insufficient_evidence");
      expect(row.reason).toContain("ambiguous");
      expect(row.algorithm_version).toBeTruthy();
    }
    await page.goto(
      `/analysis/comparisons/${comparison.comparison_id}?revision=${comparison.revision}`,
    );
    await page
      .getByRole("combobox", { name: /^Left trace$|^左侧轨迹$/ })
      .selectOption(runs[0]);
    await page
      .getByRole("combobox", { name: /^Right trace$|^右侧轨迹$/ })
      .selectOption(runs[1]);
    for (let index = 0; index < 2; index++) {
      const trace = page.locator(`[data-full-trace-owner="${runs[index]}"]`);
      const tool = sources[index].tools[0];
      await trace
        .getByRole("button", { name: `Expand ${tool.parent_step_id}` })
        .click();
      await trace.locator(`button[data-step="${tool.step_id}"]`).click();
    }
    const requestPromise = page.waitForRequest(
      (request) =>
        request.method() === "POST" &&
        request
          .url()
          .endsWith(
            `/execution-comparisons/${comparison.comparison_id}/alignments`,
          ),
    );
    await page
      .getByRole("button", {
        name: /^Confirm selected step pair$|^确认所选步骤配对$/,
      })
      .click();
    const nativeRequest = (await requestPromise).postDataJSON();
    const confirmed = await pollProjection(
      read,
      (value) => value.alignment_revision > original.alignment_revision,
      {
        timeout: 30_000,
        message: "native confirmation persists authored revision",
      },
    );
    expect(confirmed.metrics).toEqual(original.metrics);
    expect(confirmed.alignments).toHaveLength(original.alignments.length + 1);
    const record = confirmed.alignments.at(-1);
    expect(record.author).toBeTruthy();
    expect(Number.isFinite(Date.parse(record.created_at))).toBe(true);
    expect(record.supersedes).toBe(original.alignment_revision);
    expect(record.edit).toEqual(nativeRequest.edits[0]);
    expect(
      confirmed.suggestions.some(
        (row: any) =>
          row.status === "confirmed" &&
          row.left.step_id === sources[0].tools[0].step_id &&
          row.right?.step_id === sources[1].tools[0].step_id,
      ),
    ).toBe(true);
    const duplicate = (
      await appApi<any>(
        page,
        `/execution-comparisons/${comparison.comparison_id}/alignments`,
        {
          method: "POST",
          body: nativeRequest,
        },
      )
    ).data;
    expect(duplicate.accepted_alignment_revision).toBe(
      confirmed.alignment_revision,
    );
    expect((await read()).alignments).toEqual(confirmed.alignments);
    await appApi(
      page,
      `/execution-comparisons/${comparison.comparison_id}/alignments`,
      {
        method: "POST",
        expectStatus: 409,
        body: { ...nativeRequest, request_id: randomUUID() },
      },
    );
    expect((await read()).alignments).toEqual(confirmed.alignments);
    await page
      .getByRole("button", {
        name: /^Unpair selected steps$|^解除所选步骤配对$/,
      })
      .click();
    const unpaired = await pollProjection(
      read,
      (value) => value.alignment_revision > confirmed.alignment_revision,
      { timeout: 30_000, message: "native unpair persists a new revision" },
    );
    // The projection retains the latest authored state for each pair.
    const samePair = (item: any) =>
      [
        "left_run_id",
        "right_run_id",
        "left_step_id",
        "right_step_id",
        "left_attempt_id",
        "right_attempt_id",
      ].every((key) => item.edit[key] === record.edit[key]);
    const currentPair = unpaired.alignments.filter(samePair);
    expect(currentPair).toHaveLength(1);
    expect(currentPair[0].edit).toEqual({ ...record.edit, action: "unpair" });
    expect(currentPair[0].revision).toBe(unpaired.alignment_revision);
    expect(currentPair[0].revision).toBe(confirmed.alignment_revision + 1);
    expect(currentPair[0].supersedes).toBe(confirmed.alignment_revision);
    expect(currentPair[0].author).toBe(record.author);
    expect(Number.isFinite(Date.parse(currentPair[0].created_at))).toBe(true);
    expect(unpaired.alignments).toHaveLength(confirmed.alignments.length);
    expect(unpaired.alignments.filter((item: any) => !samePair(item))).toEqual(
      confirmed.alignments.filter((item: any) => !samePair(item)),
    );
    expect(unpaired.metrics).toEqual(original.metrics);
    expect(
      unpaired.suggestions.some(
        (row: any) =>
          row.left.step_id === sources[0].tools[0].step_id &&
          row.status === "unmatched",
      ),
    ).toBe(true);
    await page.reload();
    // Suggestions are projected for the two mounted traces, so restore those
    // selections after reloading before checking the authored unpair row.
    await page
      .getByRole("combobox", { name: /^Left trace$|^左侧轨迹$/ })
      .selectOption(runs[0]);
    await page
      .getByRole("combobox", { name: /^Right trace$|^右侧轨迹$/ })
      .selectOption(runs[1]);
    const unpairRow = page
      .getByRole("row")
      .filter({ hasText: sources[0].tools[0].step_id });
    await expect(unpairRow).toHaveCount(1);
    await expect(unpairRow).toContainText(
      /Unmatched \/ direct reference|未匹配 \/ 直接引用/,
    );
    await expect(unpairRow).toContainText("Unpaired by the recorded author.");
    const refreshed = await read();
    expect(refreshed.alignment_revision).toBe(unpaired.alignment_revision);
    expect(refreshed.alignments).toEqual(unpaired.alignments);
    expect(refreshed.suggestions).toEqual(unpaired.suggestions);
    expect(refreshed.metrics).toEqual(original.metrics);
  },
);

test(
  "AC07 artifact versions and fixed knowledge references retain producers and current authority",
  { annotation: { type: "acceptance", description: "AC07" } },
  async ({ operatorPage: page, bootstrapState }) => {
    test.setTimeout(600_000);
    const { completedOwnedRun } = await import("./support/owned-execution");
    const { view: terminalView } = await completedOwnedRun(
      page,
      bootstrapState.model_ids.chat!,
      "[acceptance:workbench:artifact-versions] [acceptance:tool:artifact_write]",
    );
    const run = terminalView.run.run_id;
    // Artifact receipts reconcile independently after the run terminates.
    // Freeze a server-issued cut only after both exact producer bindings exist.
    const view = await pollProjection(
      () =>
        appApi<any>(page, `/execution-runs/${run}/view`).then((r) => r.data),
      (value) => {
        const refs = value.artifacts;
        if (
          value.run.status !== "completed" ||
          refs.length !== 2 ||
          new Set(refs.map((ref: any) => ref.artifact_id)).size !== 1 ||
          !refs.every((ref: any) => ref.availability === "available") ||
          refs
            .map((ref: any) => ref.version)
            .sort()
            .join(",") !== "1,2"
        )
          return false;
        const producers = [1, 2].map((version) =>
          value.steps.find(
            (step: any) =>
              step.kind === "tool" &&
              step.tool_name === "artifact_write" &&
              step.status === "completed" &&
              step.artifact_refs?.some(
                (ref: any) =>
                  ref.artifact_id === refs[0].artifact_id &&
                  ref.version === version &&
                  ref.availability === "available",
              ),
          ),
        );
        return (
          producers.every(Boolean) &&
          new Set(producers.map((step: any) => step.step_id)).size === 2
        );
      },
      {
        timeout: 60_000,
        message: "both real artifact versions and producers bound",
      },
    );
    const refs = view.artifacts;
    expect(refs).toHaveLength(2);
    expect(new Set(refs.map((ref: any) => ref.artifact_id)).size).toBe(1);
    expect(refs.map((ref: any) => ref.version).sort()).toEqual([1, 2]);
    const artifact = refs[0].artifact_id;
    const producers: string[] = [];
    for (const version of [1, 2]) {
      const query = new URLSearchParams({
        version: String(version),
        run_id: run,
        at: view.at,
      });
      const provenance = (
        await appApi<any>(page, `/artifacts/${artifact}/provenance?${query}`)
      ).data;
      expect(provenance).toHaveLength(1);
      const owner = provenance[0];
      expect(owner.version).toBe(version);
      expect(owner.producer_run_id).toBe(run);
      expect(owner.binding_status).toBe("bound");
      expect(owner.producer_step_ids).toHaveLength(1);
      const step = view.steps.find(
        (value: any) => value.step_id === owner.producer_step_ids[0],
      );
      expect(step.artifact_refs).toContainEqual(
        expect.objectContaining({ artifact_id: artifact, version }),
      );
      producers.push(step.step_id);
      const content = (
        await appApi<any>(
          page,
          `/execution-artifacts/${artifact}/content?${query}&step_id=${encodeURIComponent(step.step_id)}&presentation=false`,
        )
      ).data;
      expect(content.availability).toBe("available");
      expect(content.version).toBe(version);
      expect(content.content).toBe(
        version === 1
          ? "# Version one\n\nOwned immutable original."
          : "# Version two\n\nOwned immutable successor.",
      );
      await page.goto(
        `/runs/${run}?at=${encodeURIComponent(view.at)}&artifact=${artifact}&version=${version}&panel=artifact`,
      );
      await page
        .getByRole("button", { name: /^Load artifact$|^加载成果$/ })
        .click();
      await expect(page.getByTestId("detail-panel")).toContainText(
        version === 1
          ? "Owned immutable original."
          : "Owned immutable successor.",
      );
      const article = page
        .getByTestId("task-view")
        .locator("[data-execution-artifacts] article")
        .filter({
          has: page.locator(`[data-producer="${run}:${step.step_id}"]`),
        });
      await article.locator(`[data-producer="${run}:${step.step_id}"]`).click();
      await expect(page).toHaveURL(
        new RegExp(`step=${encodeURIComponent(step.step_id)}`),
      );
      // Back returns the exact artifact/version selection, never the latest body.
      await page.goBack();
      await expect(page).toHaveURL(new RegExp(`version=${version}(?:&|$)`));
      await page
        .getByRole("button", { name: /^Load artifact$|^加载成果$/ })
        .click();
      await expect(page.getByTestId("detail-panel")).toContainText(
        version === 1
          ? "Owned immutable original."
          : "Owned immutable successor.",
      );
    }
    expect(new Set(producers).size).toBe(2);
    // The two legal writes produce two versions, each with its own producer step;
    // this does not claim a synthetic same-version/multiple-producer association.
    const { verifyFixedKnowledgeVersion } =
      await import("./support/knowledge-version");
    await verifyFixedKnowledgeVersion(page, bootstrapState.model_ids.chat!);
  },
);

test(
  "AC18 real member revocation rejects cached comparison, private export and evaluation paths; auditor and public share stay bounded",
  { annotation: { type: "acceptance", description: "AC18" } },
  async ({ operatorPage: page, bootstrapState, browser }) => {
    test.setTimeout(600_000);
    const { createOwnedActor } = await import("./support/owned-actor");
    const team = (
      await appApi<{ id: string }>(page, "/teams", {
        method: "POST",
        body: {
          name: acceptanceId("authority"),
          description: "Owned authority acceptance",
        },
      })
    ).data;
    registerCleanupAction({
      action: "delete-resource",
      resource: "team",
      resource_id: team.id,
    });
    const actor = await createOwnedActor(page, team.id);
    const operatorId = (await appApi<{ id: string }>(page, "/auth/me")).data.id;
    try {
      await page.evaluate(
        ({ workspaceId, userId }) => {
          localStorage.setItem("opencitadel-active-workspace", workspaceId);
          localStorage.setItem(
            `opencitadel-active-workspace:${encodeURIComponent(userId)}`,
            workspaceId,
          );
        },
        { workspaceId: team.id, userId: operatorId },
      );
      const loop = await closedLoop(page, bootstrapState.model_ids.chat!);
      const comparison = (
        await appApi<any>(actor.page, "/execution-comparisons", {
          method: "POST",
          expectStatus: 201,
          body: {
            request_id: randomUUID(),
            mode: "explicit",
            run_ids: [loop.run],
            detail_run_ids: [loop.run],
          },
        })
      ).data;
      registerCleanupAction({
        action: "delete-resource",
        resource: "execution-comparison",
        resource_id: comparison.comparison_id,
        retained_revision: comparison.revision,
        workspace_id: team.id,
        creator_id: actor.cleanup.resource_id,
      });
      const requestId = randomUUID();
      const exported = (
        await appApi<any>(actor.page, "/execution-analysis/exports", {
          method: "POST",
          expectStatus: 202,
          body: {
            source_kind: "comparison",
            request_id: requestId,
            format: "json",
            comparison_id: comparison.comparison_id,
            revision: comparison.revision,
          },
        })
      ).data;
      const binding = {
        run_id: process.env.ACCEPTANCE_RUN_ID!,
        caller_id: actor.cleanup.resource_id,
        workspace_id: team.id,
        export_id: exported.id,
        request_id: requestId,
        comparison_id: comparison.comparison_id,
        revision: comparison.revision,
        created_at: exported.created_at,
      };
      const exportCleanup = registerCleanupAction({
        action: "delete-resource",
        resource: "execution-export",
        resource_id: exported.id,
        workspace_id: team.id,
        created_at: exported.created_at,
        creator_id: actor.cleanup.resource_id,
        export_binding: binding,
      });
      const ready = await pollProjection(
        () =>
          appApi<any>(
            actor.page,
            `/execution-analysis/exports/${exported.id}`,
          ).then((r) => r.data),
        (value) => value.status === "ready",
        { timeout: 90_000, message: "actual creator private export ready" },
      );
      const download = await actor.page.evaluate(
        async ({ id, workspace }) => {
          const response = await fetch(
            `/api/execution-analysis/exports/${id}/content`,
            {
              credentials: "include",
              headers: { "X-Workspace-Id": workspace },
            },
          );
          const bytes = await response.arrayBuffer();
          return {
            status: response.status,
            bytes: bytes.byteLength,
            sha256: Array.from(
              new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
            )
              .map((v) => v.toString(16).padStart(2, "0"))
              .join(""),
            complete: true,
          };
        },
        { id: exported.id, workspace: team.id },
      );
      expect(download.status).toBe(200);
      expect(download.bytes).toBeGreaterThan(0);
      bindExportDownload(exportCleanup, {
        ...binding,
        source_kind: "comparison",
        expires_at: ready.expires_at,
        ready: true,
        download,
      });
      const comparisonPath = `/execution-comparisons/${comparison.comparison_id}?revision=${comparison.revision}`;
      const contentStep = loop.view.steps.find(
        (step: any) => step.kind === "model" && step.status === "completed",
      );
      expect(contentStep).toBeTruthy();
      const reads = [
        `/execution-runs/${loop.run}/steps/${contentStep.step_id}/content?at=${encodeURIComponent(loop.view.at)}&content_kind=output`,
        comparisonPath,
        `/execution-analysis/exports/${exported.id}`,
        `/execution-analysis/exports/${exported.id}/content`,
        `/evaluation/batches/${loop.batchId}`,
        `/evaluation/batches/${loop.batchId}/results`,
        `/evaluation/batches/${loop.batchId}/summary`,
        `/evaluation/results/${loop.resultId}/scores`,
        `/execution-runs/${loop.run}/view`,
      ];
      // Prime actual server statistics/cache and both already-rendered native surfaces.
      await appApi(actor.page, `/evaluation/batches/${loop.batchId}/summary`);
      await actor.page.goto(
        `/analysis/comparisons/${comparison.comparison_id}?revision=${comparison.revision}`,
      );
      await expect(
        actor.page.getByRole("heading", {
          name: /^Fixed comparison$|^固定比较$/,
        }),
      ).toBeVisible();
      await expect(actor.page.locator("body")).toContainText(loop.run);
      const batchPage = await actor.context.newPage();
      await batchPage.goto(
        `/evaluations/batches/${loop.batchId}?result=${loop.resultId}`,
      );
      await expect(
        batchPage.getByRole("heading", { name: /^Score details|^评分详情/ }),
      ).toBeVisible();
      await appApi(
        page,
        `/teams/${team.id}/members/${actor.cleanup.resource_id}`,
        { method: "DELETE" },
      );
      for (const path of reads)
        expect(
          (
            await appApi(actor.page, path, {
              headers: { "X-Workspace-Id": team.id },
              expectStatus: 403,
            })
          ).status,
        ).toBe(403);
      for (const stale of [actor.page, batchPage]) {
        await stale.reload();
        await expect(
          stale.locator('section[data-source="human"] article'),
        ).toHaveCount(0);
        await expect(stale.locator("[data-full-trace-owner]")).toHaveCount(0);
        await expect(
          stale.getByRole("region", { name: /^Result matrix$|^结果矩阵$/ }),
        ).toHaveCount(0);
        await expect(stale.locator("body")).not.toContainText(
          "Owned closed-loop review",
        );
        await stale.goto("/");
        await stale.goBack();
        await expect(stale.locator("body")).not.toContainText(
          "Owned closed-loop review",
        );
      }
      // Personal scope remains authorized, but exact foreign UUIDs remain unavailable.
      const actorId = (await appApi<{ id: string }>(actor.page, "/auth/me"))
        .data.id;
      expect(actorId).toBe(actor.cleanup.resource_id);
      await actor.page.evaluate((userId) => {
        localStorage.removeItem("opencitadel-active-workspace");
        localStorage.removeItem(
          `opencitadel-active-workspace:${encodeURIComponent(userId)}`,
        );
      }, actorId);
      for (const path of reads)
        expect(
          (await appApi(actor.page, path, { expectStatus: 404 })).status,
        ).toBe(404);
      await appApi(page, `/admin/users/${actor.cleanup.resource_id}`, {
        method: "PATCH",
        body: { global_role: "auditor" },
      });
      // Auditor write gate precedes scope/object access; valid existing IDs and shapes.
      const approvalId = loop.before.approvals[0]?.approval_id;
      expect(approvalId).toBeTruthy();
      expect(
        (
          await appApi(
            actor.page,
            `/approval-batches/${approvalId}/commands/decide`,
            {
              method: "POST",
              body: {
                decision: "approved",
                feedback: "Auditor must not decide",
              },
              expectStatus: 403,
            },
          )
        ).status,
      ).toBe(403);
      for (const [path, body] of [
        [
          "/execution-analysis/exports",
          {
            source_kind: "comparison",
            request_id: randomUUID(),
            format: "json",
            comparison_id: comparison.comparison_id,
            revision: comparison.revision,
          },
        ],
        [
          "/evaluation/batches",
          {
            request_id: randomUUID(),
            suite_version: loop.suiteVersion,
            preflight_revision: loop.preflightRevision,
          },
        ],
        [
          `/evaluation/results/${loop.resultId}/scores`,
          {
            request_id: randomUUID(),
            expected_revision: 0,
            expected_result_revision: 0,
            rubric_version: loop.rubricVersion,
            scores: [
              {
                dimension: "correctness",
                value: 3,
                status: "valid",
                reason: "auditor must not write",
              },
            ],
          },
        ],
      ] as const)
        expect(
          (
            await appApi(actor.page, path, {
              method: "POST",
              body,
              expectStatus: 403,
            })
          ).status,
        ).toBe(403);
      await appApi(page, `/admin/users/${actor.cleanup.resource_id}`, {
        method: "PATCH",
        body: { global_role: "user" },
      });
      const share = (
        await appApi<any>(
          page,
          `/artifacts/${loop.artifact.artifact_id}/share`,
          { method: "POST" },
        )
      ).data;
      registerCleanupAction({
        action: "delete-resource",
        resource: "artifact-share",
        resource_id: loop.artifact.artifact_id,
        workspace_id: team.id,
      });
      const publicContext = await browser.newContext({
        baseURL: new URL(page.url()).origin,
      });
      try {
        const anonymous = await publicContext.newPage();
        await anonymous.goto(share.share_url);
        await expect(anonymous.locator("body")).toContainText(
          "Workbench evidence",
        );
        for (const path of reads)
          expect(
            (await appApi(anonymous, path, { expectStatus: 401 })).status,
          ).toBe(401);
      } finally {
        await publicContext.close();
      }
      // Owner is not a substitute creator for a private export.
      expect(
        (
          await appApi(page, `/execution-analysis/exports/${exported.id}`, {
            expectStatus: 404,
          })
        ).status,
      ).toBe(404);
      await test.info().attach("authority-paths", {
        body: JSON.stringify({
          workspace_id: team.id,
          actor_id: actor.cleanup.resource_id,
          comparison_id: comparison.comparison_id,
          export_id: exported.id,
          batch_id: loop.batchId,
          removed_scope_status: 403,
          foreign_scope_status: 404,
          anonymous_status: 401,
          download,
        }),
        contentType: "application/json",
      });
    } finally {
      await actor.context.close();
      await page.evaluate((userId) => {
        localStorage.removeItem("opencitadel-active-workspace");
        localStorage.removeItem(
          `opencitadel-active-workspace:${encodeURIComponent(userId)}`,
        );
      }, operatorId);
    }
  },
);

async function keyboardWorkflow(
  page: Page,
  model: string,
  reflow: (page: Page) => Promise<void>,
) {
  const { keyboardEvaluationFlow, keyboardApproveTarget } =
    await import("./support/keyboard-evaluation");
  const loop = await closedLoop(page, model, async (target, button) => {
    await reflow(target);
    await keyboardApproveTarget(target, button);
  });
  await keyboardEvaluationFlow(page, loop, reflow);
}

test("core evaluation capture import publish consent start browse approve and score by keyboard at three widths", async ({
  operatorPage: page,
  bootstrapState,
}) => {
  test.setTimeout(1_800_000);
  await page.emulateMedia({ reducedMotion: "reduce" });
  for (const width of [1440, 1024, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await keyboardWorkflow(
      page,
      bootstrapState.model_ids.chat!,
      async (target) => {
        expect(
          await target.evaluate(
            () => matchMedia("(prefers-reduced-motion: reduce)").matches,
          ),
        ).toBe(true);
        await expect
          .poll(() =>
            target.evaluate(
              () => document.documentElement.scrollWidth <= innerWidth + 1,
            ),
          )
          .toBe(true);
      },
    );
  }
});

zoomTest(
  "AC20 core keyboard workflow completes with actual native browser 200 percent zoom",
  { annotation: { type: "acceptance", description: "AC20" } },
  async ({ operatorPage: page, bootstrapState, nativeZoom }) => {
    zoomTest.setTimeout(900_000);
    await keyboardWorkflow(page, bootstrapState.model_ids.chat!, nativeZoom);
  },
);
