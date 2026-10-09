import {
  confirmedDataset,
  publishEvaluation,
  standardRubric,
} from "./support/evaluation-fixture";
import { strictBootstrap, strictScenario } from "./support/strict-driver";
import { randomUUID } from "node:crypto";
import type { Page } from "@playwright/test";
import { appApi, expect, test } from "./fixtures/acceptance.fixture";
import { registerCleanupAction } from "./support/cleanup-journal";
import { acceptanceId } from "./support/ids";

async function importPreview(
  page: Page,
  dataset: { id: string; revision: number },
  content: string,
  type: string,
) {
  const result = await page.evaluate(
    async ({ dataset, content, type, requestId }) => {
      const csrf = document.cookie
        .split("; ")
        .find((value) => /^(?:__Host-)?csrf_token=/.test(value))
        ?.split("=")
        .slice(1)
        .join("=");
      const workspace = localStorage.getItem("opencitadel-active-workspace");
      const body = new FormData();
      body.append("request_id", requestId);
      body.append("expected_revision", String(dataset.revision));
      body.append("file", new File([content], "owned-import", { type }));
      const response = await fetch(
        `/api/evaluation/datasets/${dataset.id}/imports/validate`,
        {
          method: "POST",
          credentials: "include",
          headers: {
            ...(csrf ? { "X-CSRF-Token": decodeURIComponent(csrf) } : {}),
            ...(workspace ? { "X-Workspace-Id": workspace } : {}),
          },
          body,
        },
      );
      return { status: response.status, payload: await response.json() };
    },
    { dataset, content, type, requestId: randomUUID() },
  );
  expect(result.status).toBe(200);
  return result.payload.data;
}

test(
  "invalid imports are atomic and a concurrent draft edit prevents stale apply",
  { annotation: { type: "acceptance", description: "AC08" } },
  async ({ operatorPage: page }) => {
    const dataset = (
      await appApi<any>(page, "/evaluation/datasets", {
        method: "POST",
        body: {
          request_id: randomUUID(),
          expected_revision: 0,
          name: acceptanceId("governance"),
        },
      })
    ).data;
    registerCleanupAction({
      action: "delete-resource",
      resource: "evaluation-dataset",
      resource_id: dataset.id,
    });
    const header =
      "case_key,input,reference_answer,tags,rules_json,attachment_ids_json,knowledge_bindings_json\n";
    const invalidCsv = await importPreview(
      page,
      dataset,
      header + "ok,prompt,answer,[],[],[],[]\nbad,prompt\n",
      "text/csv",
    );
    expect(invalidCsv.errors).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ row: 3, code: "csv_columns" }),
      ]),
    );
    const duplicate = await importPreview(
      page,
      dataset,
      header +
        "same,prompt,answer,[],[],[],[]\nsame,prompt,answer,[],[],[],[]\n",
      "text/csv",
    );
    expect(
      duplicate.errors.some(
        (error: any) => error.row === 3 && error.code === "duplicate_case_key",
      ),
    ).toBe(true);
    const foreign = await importPreview(
      page,
      dataset,
      JSON.stringify({
        schema_version: 1,
        cases: [
          {
            case_key: "outside",
            input: "prompt",
            attachments: ["https://example.invalid/foreign"],
          },
        ],
      }),
      "application/json",
    );
    expect(
      foreign.errors.some(
        (error: any) => error.code === "resource_unavailable",
      ),
    ).toBe(true);
    for (const invalid of [invalidCsv, duplicate, foreign]) {
      await appApi(
        page,
        `/evaluation/datasets/${dataset.id}/imports/${invalid.import_id}/apply`,
        {
          method: "POST",
          expectStatus: 400,
          body: {
            request_id: randomUUID(),
            expected_revision: dataset.revision,
            input_digest: invalid.input_digest,
          },
        },
      );
    }
    expect(
      (await appApi<any>(page, `/evaluation/datasets/${dataset.id}`)).data,
    ).toEqual(dataset);
    const valid = await importPreview(
      page,
      dataset,
      header + "imported,prompt,answer,[],[],[],[]\n",
      "text/csv",
    );
    expect(valid.errors).toEqual([]);
    const edited = (
      await appApi<any>(
        page,
        `/evaluation/datasets/${dataset.id}/cases/concurrent`,
        {
          method: "PATCH",
          body: {
            request_id: randomUUID(),
            expected_revision: dataset.revision,
            case: { input: "concurrent retained text", input_confirmed: true },
          },
        },
      )
    ).data;
    const conflict = await appApi(
      page,
      `/evaluation/datasets/${dataset.id}/imports/${valid.import_id}/apply`,
      {
        method: "POST",
        expectStatus: 409,
        body: {
          request_id: randomUUID(),
          expected_revision: dataset.revision,
          input_digest: valid.input_digest,
        },
      },
    );
    expect(conflict.errorKey).toBe("executionErrors.revision_conflict");
    expect(
      (await appApi<any>(page, `/evaluation/datasets/${dataset.id}`)).data,
    ).toEqual(edited);
  },
);

test(
  "AC12 actual reset failure, killed worker and late cancellation preserve cleanup and cancelled batch",
  { annotation: { type: "acceptance", description: "AC12" } },
  async ({ operatorPage: page }) => {
    const reset = strictScenario("AC12", "reset_failure");
    const death = strictScenario("AC12", "worker_death");
    const late = strictScenario("AC12", "late_completion_cancel");
    expect(reset.after.quarantine_state).toBe("quarantine");
    expect(reset.cleanup.state).toBe("verified_clean");
    expect(death.fault.mechanism).toBe("child_process_termination");
    expect(death.after.child_exit_code).toBe(-9);
    expect(death.cleanup.state).toBe("verified_clean");
    expect(late.after.physical_sends).toBe(1);
    expect(late.after.ledger.reserved_tokens).toBe(0);
    const batch = (
      await appApi<any>(
        page,
        `/evaluation/batches/${late.resource_ids.batch_id}`,
      )
    ).data;
    expect(batch.status).toBe("cancelled");
    await page.goto(`/evaluations/batches/${batch.id}`);
    await expect(page.getByRole("heading", { level: 1 })).toContainText(
      batch.id,
    );
    await expect(page.getByText(/^Cancelled$|^已取消$/).first()).toBeVisible();
    const leases = (
      await appApi<any>(
        page,
        `/evaluation/batches/${reset.resource_ids.batch_id}/environments`,
      )
    ).data;
    const repaired = leases.items.find(
      (item: any) => item.id === reset.resource_ids.lease_id,
    );
    expect(repaired.state).toBe("verified_clean");
    expect(repaired.reusable).toBe(true);
    expect(repaired.prior_failed_operations.reset).toBe(1);
    expect(repaired).toEqual(reset.after.current_environment);
    const leaseCard = page.locator(`[data-lease-id="${repaired.id}"]`);
    await expect(leaseCard).toContainText(/Verified clean|已验证清理完成/);
    await expect(leaseCard).toContainText(/Previous quarantine|曾发生隔离/);
    await expect(leaseCard).toContainText(/Reset failed|重置失败/);
    await expect(leaseCard).toContainText(/Reusable|可复用/);
    await expect(leaseCard).not.toContainText(/Not reusable|不可复用/);
    // This cleaned fixture displays retained history; actual nonreuse during
    // quarantine was observed before legal repair by the strict producer.
    // No status-row rewrite: later callbacks were settled by the real budget service.
    const again = (await appApi<any>(page, `/evaluation/batches/${batch.id}`))
      .data;
    expect(again.status).toBe("cancelled");
  },
);

test(
  "AC13 real concurrent submission, claim transfer, admission restart and conservative budget keep one run",
  { annotation: { type: "acceptance", description: "AC13" } },
  async ({ operatorPage: page }) => {
    const submitted = strictScenario("AC13", "concurrent_submit");
    const lease = strictScenario("AC13", "lease_transfer");
    const admission = strictScenario("AC13", "admission_restart");
    const budget = strictScenario("AC13", "budget_exhaustion");
    const stopped = strictScenario("AC13", "scheduler_budget_stop");
    expect(submitted.after.first_id).toBe(submitted.after.second_id);
    expect(lease.after.generation).toBeGreaterThan(lease.before.generation);
    expect(admission.after.same_envelope).toBe(true);
    expect(budget.before.denied).toBe(1);
    expect(budget.after.ledger.reserved_tokens).toBe(0);
    expect(budget.after.ledger.spent_tokens).toBe(
      budget.after.usage.total_tokens,
    );
    const results = (
      await appApi<any>(
        page,
        `/evaluation/batches/${submitted.resource_ids.batch_id}/results`,
      )
    ).data;
    const runIds = results.items.map((row: any) => row.run_id).filter(Boolean);
    expect(new Set(runIds).size).toBe(runIds.length);
    expect(runIds).toContain(admission.resource_ids.run_id);
    await page.goto(`/evaluations/batches/${submitted.resource_ids.batch_id}`);
    await expect(page.getByRole("heading", { level: 1 })).toContainText(
      submitted.resource_ids.batch_id,
    );
    await page
      .locator(
        `[data-public-run="${admission.resource_ids.run_id}"][data-native-content="matrix-result"]`,
      )
      .click();
    await expect(
      page.locator(`a[href^="/runs/${admission.resource_ids.run_id}"]`).first(),
    ).toBeVisible();
    expect(stopped.before.preflight_allowed).toBe(true);
    expect(stopped.before.token_budget).toBe(stopped.before.subject_bound);
    expect(stopped.after.usage.total_tokens).toBeGreaterThan(0);
    expect(stopped.after.admission_receipt).toBe(false);
    expect(stopped.after.envelope).toBe(false);
    expect(stopped.after.prepared_envelope).toBe(false);
    const stoppedResults = (
      await appApi<any>(
        page,
        `/evaluation/batches/${stopped.resource_ids.batch_id}/results`,
      )
    ).data;
    const blocked = stoppedResults.items.find(
      (item: any) => item.id === stopped.resource_ids.blocked_result_id,
    );
    expect(blocked.execution_status).toBe("blocked_budget");
    expect(blocked.scoring_status).toBe("skipped");
    await page.goto(`/evaluations/batches/${stopped.resource_ids.batch_id}`);
    const matrix = page.getByRole("region", {
      name: /^Result matrix$|^结果矩阵$/,
    });
    await matrix.getByRole("combobox").selectOption(blocked.id);
    await expect(matrix).toContainText(/Blocked by budget|预算不足/);
    // UUIDs are preallocated. The strict producer checks actual receipt/envelope
    // absence; a non-null result.run_id does not mean execution was admitted.
  },
);

test(
  "AC09 published dataset and configuration survive draft edits, alias drift and owned dependency deletion",
  { annotation: { type: "acceptance", description: "AC09" } },
  async ({ operatorPage: page, bootstrapState }) => {
    const { draft, version } = await confirmedDataset(page, [
      { key: "fixed", input: "[acceptance:evaluation:rule-pass]" },
    ]);
    const fixed = (
      await appApi<any>(page, `/evaluation/dataset-versions/${version.id}`)
    ).data;
    const currentDraft = (
      await appApi<any>(page, `/evaluation/datasets/${draft.id}`)
    ).data;
    const changed = (
      await appApi<any>(page, `/evaluation/datasets/${draft.id}/cases/fixed`, {
        method: "PATCH",
        body: {
          request_id: randomUUID(),
          expected_revision: currentDraft.revision,
          case: {
            input: "[acceptance:evaluation:invalid-json]",
            input_confirmed: true,
            reference_answer: null,
            reference_confirmed: true,
            applicable_dimensions: ["correctness"],
          },
        },
      })
    ).data;
    const second = (
      await appApi<any>(page, `/evaluation/datasets/${draft.id}/publish`, {
        method: "POST",
        body: { request_id: randomUUID(), expected_revision: changed.revision },
      })
    ).data;
    expect(second.id).not.toBe(version.id);
    expect(
      (await appApi<any>(page, `/evaluation/dataset-versions/${version.id}`))
        .data,
    ).toEqual(fixed);
    const modelBody = {
      endpoint_id: bootstrapState.endpoint_id,
      display_name: acceptanceId("drift-model"),
      model_name: "acceptance-chat",
      kind: "chat",
      settings: { kind: "chat", temperature: 0, max_output_tokens: 4096 },
      input_price_per_million: 1,
      output_price_per_million: 1,
      extra_params: {},
      capabilities: {},
      visibility: "private",
    };
    const model = (
      await appApi<any>(page, "/inference/models", {
        method: "POST",
        body: modelBody,
      })
    ).data;
    registerCleanupAction({
      action: "delete-resource",
      resource: "inference-model",
      resource_id: model.id,
    });
    expect(
      (
        await appApi<any>(page, `/inference/models/${model.id}/probe`, {
          method: "POST",
        })
      ).data.status,
    ).toBe("ok");
    const environment = strictBootstrap().environment.id;
    const config = await publishEvaluation(page, "config", {
      model_id: model.id,
      mode: "ask",
      max_output_tokens: 4096,
      external_contract_ref: { kind: "environment", version_id: environment },
    });
    expect(config.version_unpinned).toBe(true);
    expect(config.unpinned_reasons.length).toBeGreaterThan(0);
    const rubric = await standardRubric(page, bootstrapState.model_ids.chat!);
    const suite = await publishEvaluation(page, "suite", {
      dataset_version: version.id,
      config_versions: [config.id],
      rubric_version: rubric.id,
      mode: "isolated",
      environment_version: environment,
      settings: { token_budget: 1_000_000, repeat: 1, seed: 9 },
    });
    const preflight = () =>
      appApi<any>(page, "/evaluation/batches/preflight", {
        method: "POST",
        body: { suite_version: suite.id },
      }).then((value) => value.data);
    expect((await preflight()).allowed).toBe(true);
    await appApi(page, `/inference/models/${model.id}`, {
      method: "PUT",
      body: { ...modelBody, model_name: "acceptance-failure" },
    });
    try {
      const drift = await preflight();
      expect(drift.allowed).toBe(false);
      // The changed alias has no attested budget profile. Admission rejects it
      // before a current configuration proof can be formed.
      expect(drift.errors).toContain("configuration_unavailable");
      expect(
        (await appApi<any>(page, `/evaluation/configs/versions/${config.id}`))
          .data,
      ).toEqual(config);
    } finally {
      // Restore the shared fallback inventory before unrelated evaluation
      // scenarios publish configurations in parallel.
      await appApi(page, `/inference/models/${model.id}`, {
        method: "PUT",
        body: modelBody,
      });
    }
    await appApi(page, `/inference/models/${model.id}`, { method: "DELETE" });
    const absent = await preflight();
    expect(absent.allowed).toBe(false);
    expect(absent.errors.length).toBeGreaterThan(0);
    await appApi(page, "/evaluation/batches", {
      method: "POST",
      expectStatus: 400,
      body: {
        request_id: randomUUID(),
        suite_version: suite.id,
        preflight_revision: absent.revision,
      },
    });
    await page.goto(`/evaluations/datasets/${draft.id}`);
    await expect(page.getByRole("heading", { level: 1 })).toContainText(
      draft.name,
    );
    await page
      .getByRole("heading", { name: /^Published history$|^已发布历史$/ })
      .locator("..")
      .getByRole("combobox", { name: /^Fixed version$|^固定版本$/ })
      .selectOption(version.id);
    await expect(
      page.getByText(
        /This published version is immutable|此已发布版本不可修改/,
      ),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: /^Edit$|^编辑$/ }),
    ).toHaveCount(0);
    await page.getByRole("button", { name: /^Inspect$|^检查$/ }).click();
    await expect(page.getByLabel(/^Input$|^输入$/)).toHaveValue(
      "[acceptance:evaluation:rule-pass]",
    );
    await page.goto(`/evaluations/configs/${config.entity_id}`);
    await page
      .getByLabel(/^Published history$|^已发布历史$/)
      .selectOption(config.id);
    await expect(
      page.getByText(/Provider version is not pinned|提供商版本未固定/),
    ).toBeVisible();
    for (const reason of config.unpinned_reasons)
      await expect(page.getByText(reason)).toBeVisible();
  },
);

test(
  "AC15 and AC16 retain strata, missing scores, equal case weights and separate subject and judge accounting",
  {
    annotation: [
      { type: "acceptance", description: "AC15" },
      { type: "acceptance", description: "AC16" },
    ],
  },
  async ({ operatorPage: page, bootstrapState }) => {
    test.setTimeout(900_000);
    const { completedOwnedRun } = await import("./support/owned-execution");
    const { recordOwnedRun, completedEvaluation, humanScore } =
      await import("./support/evaluation-lifecycle");
    const priced = (
      await appApi<any>(page, "/inference/models", {
        method: "POST",
        body: {
          endpoint_id: bootstrapState.endpoint_id,
          display_name: acceptanceId("accounting-model"),
          model_name: "acceptance-chat",
          kind: "chat",
          settings: { kind: "chat", temperature: 0, max_output_tokens: 4096 },
          input_price_per_million: 1,
          output_price_per_million: 2,
          extra_params: {},
          capabilities: {},
          visibility: "private",
        },
      })
    ).data;
    registerCleanupAction({
      action: "delete-resource",
      resource: "inference-model",
      resource_id: priced.id,
    });
    expect(
      (
        await appApi<any>(page, `/inference/models/${priced.id}/probe`, {
          method: "POST",
        })
      ).data.status,
    ).toBe("ok");
    const input = "[acceptance:evaluation:rule-pass]";
    const source = await completedOwnedRun(page, priced.id, input, "ask");
    const recording = await recordOwnedRun(page, source.view.run.run_id);
    const dataset = await confirmedDataset(
      page,
      ["a", "b", "missing"].map((key) => ({
        key,
        input,
        reference: `Acceptance response: ${input}`,
      })),
    );
    const rubric = await standardRubric(page, priced.id);
    const configs = [];
    for (const temperature of [0, 0.1])
      configs.push(
        await publishEvaluation(page, "config", {
          model_id: priced.id,
          mode: "ask",
          temperature,
          max_output_tokens: 4096,
          external_contract_ref: {
            kind: "recording",
            version_id: recording.version_id,
          },
        }),
      );
    const suite = await publishEvaluation(page, "suite", {
      dataset_version: dataset.version.id,
      config_versions: configs.map((config) => config.id),
      rubric_version: rubric.id,
      mode: "recorded",
      recording_versions: [recording.version_id],
      settings: { token_budget: 1_000_000, repeat: 3, seed: 17 },
    });
    const recorded = await completedEvaluation(page, suite.id);
    expect(recorded.results).toHaveLength(18);
    const caseIds = Object.fromEntries(
      dataset.version.cases.map((entry: any) => [entry.case_key, entry.id]),
    );
    for (const config of configs) {
      const rows = recorded.results.filter(
        (row: any) => row.slot.config_version_id === config.id,
      );
      const first = rows
        .filter((row: any) => row.slot.case_revision_id === caseIds.a)
        .sort((a: any, b: any) => a.slot.repetition - b.slot.repetition);
      expect(first).toHaveLength(3);
      for (let index = 0; index < first.length; index++)
        await humanScore(page, first[index].id, index * 2);
      const second = rows
        .filter((row: any) => row.slot.case_revision_id === caseIds.b)
        .sort((a: any, b: any) => a.slot.repetition - b.slot.repetition);
      expect(second).toHaveLength(3);
      await humanScore(page, second[0].id, 4);
    }
    const summary = (
      await appApi<any>(
        page,
        `/evaluation/batches/${recorded.batch.id}/summary?source=human&dimension=correctness&rubric_id=${rubric.id}&limit=200`,
      )
    ).data;
    expect(summary.items.filter((row: any) => row.value !== null)).toHaveLength(
      8,
    );
    expect(summary.items.filter((row: any) => row.value === null)).toHaveLength(
      10,
    );
    expect(summary.subject_usage.calls).toBeGreaterThan(0);
    expect(summary.judge_usage.calls).toBeGreaterThan(0);
    expect(summary.subject_usage.tokens).toBeGreaterThan(0);
    expect(summary.judge_usage.tokens).toBeGreaterThan(0);
    for (const purpose of ["subject_usage", "judge_usage"]) {
      expect(summary[purpose].tokens).toBe(
        summary.items.reduce(
          (total: number, row: any) => total + row[purpose].tokens,
          0,
        ),
      );
      expect(Number(summary[purpose].money)).toBeCloseTo(
        summary.items.reduce(
          (total: number, row: any) => total + Number(row[purpose].money),
          0,
        ),
        10,
      );
      expect(summary[purpose].money_known).toBe(summary[purpose].calls);
    }
    const environment = strictBootstrap().environment.id;
    const otherDataset = await confirmedDataset(page, [
      { key: "other", input: "[acceptance:evaluation:missing-usage]" },
    ]);
    const otherRubric = await standardRubric(page, priced.id);
    const isolatedConfig = await publishEvaluation(page, "config", {
      model_id: priced.id,
      mode: "ask",
      max_output_tokens: 4096,
      external_contract_ref: { kind: "environment", version_id: environment },
    });
    const otherSuite = await publishEvaluation(page, "suite", {
      dataset_version: otherDataset.version.id,
      config_versions: [isolatedConfig.id],
      rubric_version: otherRubric.id,
      mode: "isolated",
      environment_version: environment,
      settings: { token_budget: 1_000_000, repeat: 1, seed: 17 },
    });
    const isolated = await completedEvaluation(page, otherSuite.id, {
      retainedAccounting: true,
    });
    const { collectAccountingRetention, waitAccountingUsageProjection } =
      await import("./support/accounting-retention");
    const accounting = collectAccountingRetention(isolated.batch.id);
    await waitAccountingUsageProjection(page, accounting);
    const missingUsage = (
      await appApi<any>(
        page,
        `/evaluation/batches/${isolated.batch.id}/summary?source=model&dimension=correctness&rubric_id=${otherRubric.id}`,
      )
    ).data;
    expect(missingUsage.items).toHaveLength(1);
    expect(missingUsage.items[0].value).toBe(4);
    expect(missingUsage.subject_usage.calls).toBeGreaterThan(0);
    expect(missingUsage.subject_usage.tokens).toBeNull();
    expect(missingUsage.subject_usage.money).toBeNull();
    expect(missingUsage.subject_usage.money_known).toBe(0);
    expect(missingUsage.subject_usage.unresolved).toBe(0); // terminal receipt, missing accounting amounts.
    expect(missingUsage.judge_usage.tokens).toBeGreaterThan(0);
    expect(missingUsage.judge_usage.money_known).toBe(
      missingUsage.judge_usage.calls,
    );
    expect(missingUsage.points[0].cost_usd).toBeNull();
    expect(missingUsage.quality_cost_metadata.sample_count).toBe(0);
    expect(missingUsage.quality_cost_metadata.missing_count).toBe(1);
    expect(
      accounting.evidence.calls
        .filter((row: any) => row.purpose === "evaluation_subject")
        .map((row: any) => row.run_id),
    ).toContain(isolated.results[0].run_id);
    const runs = [...recorded.results, ...isolated.results].map(
      (row: any) => row.run_id,
    );
    const comparison = (
      await appApi<any>(page, "/execution-comparisons", {
        method: "POST",
        expectStatus: 201,
        body: {
          request_id: randomUUID(),
          mode: "explicit",
          run_ids: runs,
          detail_run_ids: runs.slice(0, 2),
          filters: { accounting: "selected_result" },
        },
      })
    ).data;
    registerCleanupAction({
      action: "delete-resource",
      resource: "execution-comparison",
      resource_id: comparison.comparison_id,
      retained_revision: comparison.revision,
    });
    const usage = comparison.metrics.usage;
    expect(usage.grain).toBe("selected_result");
    const subjectUsage = usage.purposes.evaluation_subject;
    const judgeUsage = usage.purposes.evaluation_judge;
    expect(subjectUsage.cost_coverage.numerator).toBe(
      summary.subject_usage.calls,
    );
    expect(subjectUsage.cost_coverage.denominator).toBe(
      summary.subject_usage.calls + missingUsage.subject_usage.calls,
    );
    expect(subjectUsage.cost_usd.missing_count).toBe(
      missingUsage.subject_usage.calls,
    );
    expect(judgeUsage.cost_coverage.numerator).toBe(
      summary.judge_usage.calls + missingUsage.judge_usage.calls,
    );
    expect(judgeUsage.cost_coverage.numerator).toBe(
      judgeUsage.cost_coverage.denominator,
    );
    expect(Number(subjectUsage.cost_usd.value)).toBeCloseTo(
      Number(summary.subject_usage.money),
      10,
    );
    expect(Number(judgeUsage.cost_usd.value)).toBeCloseTo(
      Number(summary.judge_usage.money) +
        Number(missingUsage.judge_usage.money),
      10,
    );
    // The same single selected result has a distinct whole-batch accounting expansion.
    const selectedRow = summary.items.find(
      (row: any) => row.run_id === recorded.results[0].run_id,
    );
    for (const grain of ["selected_result", "batch_total"]) {
      const accountingCapture = (
        await appApi<any>(page, "/execution-comparisons", {
          method: "POST",
          expectStatus: 201,
          body: {
            request_id: randomUUID(),
            mode: "explicit",
            run_ids: [selectedRow.run_id],
            detail_run_ids: [selectedRow.run_id],
            filters: {
              accounting: grain,
              ...(grain === "batch_total"
                ? { batch_id: recorded.batch.id }
                : {}),
            },
          },
        })
      ).data;
      registerCleanupAction({
        action: "delete-resource",
        resource: "execution-comparison",
        resource_id: accountingCapture.comparison_id,
        retained_revision: accountingCapture.revision,
      });
      expect(accountingCapture.member_count).toBe(1);
      expect(accountingCapture.metrics.usage.grain).toBe(grain);
      for (const [purpose, kind] of [
        ["evaluation_subject", "subject_usage"],
        ["evaluation_judge", "judge_usage"],
      ]) {
        const expectedUsage =
          grain === "batch_total" ? summary[kind] : selectedRow[kind];
        const actualUsage = accountingCapture.metrics.usage.purposes[purpose];
        expect(actualUsage.cost_coverage.denominator).toBe(expectedUsage.calls);
        expect(Number(actualUsage.cost_usd.value)).toBeCloseTo(
          Number(expectedUsage.money),
          10,
        );
      }
    }
    const scores = comparison.metrics.scores;
    const main = scores.series.filter((row: any) =>
      configs.some((config) => config.id === row.configuration),
    );
    expect(main).toHaveLength(2);
    for (const row of main) {
      expect(row.identity[1]).toBe(dataset.version.id);
      expect(row.identity[2]).toBe("recorded");
      expect(row.identity[4]).toBe(rubric.id);
      const mean = row.metrics["human:correctness:mean"];
      expect(mean.value).toBe(3); // (mean(0,2,4) + mean(4)) / 2, not 2.5 per result.
      expect(mean.sample_count).toBe(2);
      expect(mean.missing_count).toBe(2); // partially reviewed B plus fully missing case.
      expect(mean.excluded_count).toBe(0);
      const coverage = row.metrics["human:correctness:coverage"];
      expect(coverage.numerator).toBeCloseTo(4 / 3);
      expect(coverage.denominator).toBe(3);
      expect(coverage.value).toBeCloseTo(4 / 9);
    }
    const other = scores.series.find(
      (row: any) => row.configuration === isolatedConfig.id,
    );
    expect(other).toBeTruthy();
    expect(other.identity).not.toEqual(main[0].identity);
    expect(other.identity[1]).toBe(otherDataset.version.id);
    expect(other.identity[2]).toBe("isolated");
    expect(other.identity[4]).toBe(otherRubric.id);
    expect(other.metrics["human:correctness:mean"].value).toBeNull();
    expect(other.metrics["human:correctness:mean"].missing_count).toBe(1);
    expect(
      scores.comparisons.some((row: any) =>
        [row.left, row.right].includes(isolatedConfig.id),
      ),
    ).toBe(false);
    expect(
      scores.comparisons.some(
        (row: any) =>
          new Set([row.left, row.right]).size === 2 &&
          [row.left, row.right].every((id: string) =>
            configs.some((config) => config.id === id),
          ),
      ),
    ).toBe(true);
    await page.goto(
      `/analysis/comparisons/${comparison.comparison_id}?revision=${comparison.revision}`,
    );
    await expect(
      page.getByText(/cannot be ranked together|不能混合排名/),
    ).toBeVisible();
    const scoreStrata = page
      .getByRole("heading", {
        name: /^Comparable score strata$|^可比分数分层$/,
      })
      .locator("..");
    await scoreStrata.locator("summary").click();
    await expect(scoreStrata.getByText(configs[0].id).first()).toBeVisible();
    await expect(
      scoreStrata.getByText(isolatedConfig.id).first(),
    ).toBeVisible();
  },
);
