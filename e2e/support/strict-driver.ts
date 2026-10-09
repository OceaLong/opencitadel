/** Fixed runner-owned host bridge. This module never exposes private driver data. */
import { spawnSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { readFileSync, writeFileSync, renameSync } from "node:fs";
import { resolve } from "node:path";
import type { Page } from "@playwright/test";
import { appApi } from "./api";
import { registerCleanupAction } from "./cleanup-journal";
import { acceptanceId } from "./ids";
import { createSession } from "./execution";
import type { BootstrapState } from "./bootstrap-state";

type Ref = { id: string; revision: number };
type Draft = Ref & { cases?: Array<{ id: string }> };
export type StrictBootstrapInput = {
  schema_version: 1;
  run_id: string;
  project: string;
  operator_id: string;
  scope: { type: "personal"; user_id: string; team_id: null };
  session_id: string;
  analysis_session_id: string;
  endpoint_id: string;
  model_id: string;
  dataset: Ref;
  dataset_version: Ref;
  case_id: string;
  configuration: Ref;
  configuration_version: Ref;
  suite: Ref;
  suite_version: Ref;
  environment: Ref;
  target: Ref;
  credentials: Ref[];
};

export function assertStrictReceipt(
  receipt: unknown,
  binding: Record<string, unknown>,
): void {
  const value = receipt as {
    status?: string;
    kernel_restored?: boolean;
    binding?: unknown;
    requirements?: string[];
  };
  if (
    !value ||
    value.status !== "passed" ||
    value.kernel_restored !== true ||
    JSON.stringify(value.binding) !== JSON.stringify(binding) ||
    JSON.stringify(value.requirements) !==
      JSON.stringify(["AC02", "AC05", "AC12", "AC13", "AC19", "AC22"])
  )
    throw new Error(
      "strict driver receipt is incomplete or belongs to another invocation",
    );
}

function writeInput(input: StrictBootstrapInput): void {
  const path = resolve(
    process.env.ACCEPTANCE_EVIDENCE_DIR!,
    "strict-bootstrap.json",
  );
  writeFileSync(`${path}.tmp`, JSON.stringify(input), { mode: 0o600 });
  renameSync(`${path}.tmp`, path);
}

export async function prepareStrictDriver(
  page: Page,
  state: BootstrapState,
): Promise<void> {
  if (
    !process.env.ACCEPTANCE_PLAYWRIGHT_PROJECTS?.split(",").includes(
      "execution",
    )
  )
    return;
  const root = process.env.ACCEPTANCE_EVIDENCE_DIR;
  if (!root || !process.env.ACCEPTANCE_HOST_PYTHON)
    throw new Error(
      "strict driver requires runner binding and host interpreter",
    );
  const binding = JSON.parse(
    readFileSync(resolve(root, "strict-binding.json"), "utf8"),
  );
  if (
    binding.run_id !== state.run_id ||
    binding.project !== process.env.ACCEPTANCE_PROJECT_ID
  )
    throw new Error("strict binding is foreign");
  const operator = (
    await appApi<{ id: string; global_role: string }>(page, "/auth/me")
  ).data;
  const workspace = await page.evaluate(() =>
    localStorage.getItem("opencitadel-active-workspace"),
  );
  if (workspace && workspace !== "personal")
    throw new Error("strict producer needs operator personal workspace");
  if (!state.endpoint_id || !state.model_ids.chat)
    throw new Error("strict producer requires real bootstrap model");
  const sessionId = await createSession(
    page,
    acceptanceId("strict-source"),
    "agent",
    state.model_ids.chat,
  );
  const analysisSessionId = await createSession(
    page,
    acceptanceId("strict-dated"),
    "agent",
    state.model_ids.chat,
  );
  const inventory = (
    await appApi<{
      targets: Array<Ref & { kind: string }>;
      credentials: Array<Ref & { target: Ref }>;
      adapters: Array<{
        name: string;
        revision: string;
        images: unknown[];
        fixtures: string[];
        healthchecks: string[];
      }>;
    }>(page, "/evaluation/environments/inventory")
  ).data;
  const adapter = inventory.adapters.find(
    (item) => item.name === "docker-http-cell-v1",
  );
  const target = inventory.targets.find((item) => item.kind === "http");
  if (
    !adapter ||
    !target ||
    !adapter.images.length ||
    !adapter.fixtures.length ||
    !adapter.healthchecks.length
  )
    throw new Error("dedicated acceptance environment unavailable");
  const targetRef = { id: target.id, revision: target.revision };
  await appApi(
    page,
    `/evaluation/environments/inventory/target/${target.id}/register`,
    {
      method: "POST",
      body: { request_id: randomUUID(), revision: target.revision },
    },
  );
  const credentials = inventory.credentials
    .filter((item) => item.target.id === target.id)
    .map((item) => ({ id: item.id, revision: item.revision }));
  for (const credential of credentials)
    await appApi(
      page,
      `/evaluation/environments/inventory/credential/${credential.id}/register`,
      {
        method: "POST",
        body: { request_id: randomUUID(), revision: credential.revision },
      },
    );
  const environment = { id: randomUUID(), revision: 1 };
  await appApi(page, "/evaluation/environments/registry", {
    method: "POST",
    body: {
      request_id: randomUUID(),
      kind: "environment",
      value: {
        ...environment,
        image_digest: adapter.images[0],
        fixture_revision: adapter.fixtures[0],
        reset_adapter: adapter.name,
        adapter_revision: adapter.revision,
        healthcheck_revision: adapter.healthchecks[0],
        allowed_targets: [targetRef],
        credential_refs: credentials,
      },
    },
  });
  registerCleanupAction({
    action: "delete-resource",
    resource: "evaluation-environment",
    resource_id: environment.id,
  });
  let dataset = (
    await appApi<Draft>(page, "/evaluation/datasets", {
      method: "POST",
      body: {
        request_id: randomUUID(),
        expected_revision: 0,
        name: acceptanceId("strict"),
      },
    })
  ).data;
  registerCleanupAction({
    action: "delete-resource",
    resource: "evaluation-dataset",
    resource_id: dataset.id,
  });
  dataset = (
    await appApi<Draft>(
      page,
      `/evaluation/datasets/${dataset.id}/cases/strict`,
      {
        method: "PATCH",
        body: {
          request_id: randomUUID(),
          expected_revision: dataset.revision,
          case: {
            input: "[acceptance:evaluation:rule-pass]",
            input_confirmed: true,
            reference_answer:
              "Acceptance response: [acceptance:evaluation:rule-pass]",
            reference_confirmed: true,
          },
        },
      },
    )
  ).data;
  const datasetVersion = (
    await appApi<Ref & { cases: Array<{ id: string }> }>(
      page,
      `/evaluation/datasets/${dataset.id}/publish`,
      {
        method: "POST",
        body: { request_id: randomUUID(), expected_revision: dataset.revision },
      },
    )
  ).data;
  async function publish(
    kind: "config" | "rubric" | "suite",
    definition: unknown,
  ) {
    const draft = (
      await appApi<Ref>(page, `/evaluation/${kind}s`, {
        method: "POST",
        body: {
          request_id: randomUUID(),
          name: acceptanceId(`strict-${kind}`),
          definition,
        },
      })
    ).data;
    registerCleanupAction({
      action: "delete-resource",
      resource: `evaluation-${kind}`,
      resource_id: draft.id,
    });
    const version = (
      await appApi<Ref>(page, `/evaluation/${kind}s/${draft.id}/publish`, {
        method: "POST",
        body: { request_id: randomUUID(), expected_revision: draft.revision },
      })
    ).data;
    return { draft, version };
  }
  const configuration = await publish("config", {
    model_id: state.model_ids.chat,
    mode: "agent",
    max_output_tokens: 4096,
    tool_names: [],
    external_contract_ref: { kind: "environment", version_id: environment.id },
  });
  const judge = await publish("config", {
    model_id: state.model_ids.chat,
    purpose: "evaluation_judge",
    mode: "ask",
    max_output_tokens: 4096,
  });
  const rubric = await publish("rubric", {
    judge_config_version: judge.version.id,
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
  });
  const suite = await publish("suite", {
    rubric_version: rubric.version.id,
    dataset_version: datasetVersion.id,
    config_versions: [configuration.version.id],
    mode: "isolated",
    environment_version: environment.id,
    settings: { token_budget: 266240, repeat: 1, seed: 5 },
  });
  // DatasetVersion returns immutable case membership IDs; no synthetic case UUID.
  const caseId = datasetVersion.cases?.[0]?.id;
  if (!caseId)
    throw new Error("published dataset has no immutable case identity");
  const ref = (value: Ref): Ref => ({ id: value.id, revision: value.revision });
  writeInput({
    schema_version: 1,
    run_id: state.run_id,
    project: binding.project,
    operator_id: operator.id,
    scope: { type: "personal", user_id: operator.id, team_id: null },
    session_id: sessionId,
    analysis_session_id: analysisSessionId,
    endpoint_id: state.endpoint_id,
    model_id: state.model_ids.chat,
    dataset: ref(dataset),
    dataset_version: ref(datasetVersion),
    case_id: caseId,
    configuration: ref(configuration.draft),
    configuration_version: ref(configuration.version),
    suite: ref(suite.draft),
    suite_version: ref(suite.version),
    environment,
    target: targetRef,
    credentials,
  });
  const result = spawnSync(
    process.env.ACCEPTANCE_HOST_PYTHON,
    ["-m", "scripts.acceptance.strict_bridge"],
    {
      cwd: resolve(__dirname, "../.."),
      env: process.env,
      encoding: "utf8",
      timeout: 2_400_000,
      maxBuffer: 1024 * 1024,
    },
  );
  if (result.error || result.status !== 0)
    throw new Error(
      "controlled strict driver failed; runner evidence and cleanup journal retained",
    );
  const receipt = JSON.parse(
    readFileSync(resolve(root, "strict-consumer.json"), "utf8"),
  );
  assertStrictReceipt(receipt, binding);
}

/** Re-runs the host's read-only artifact validator; never starts/restores a service. */
export function strictScenario(requirement: string, identity: string): any {
  const root = process.env.ACCEPTANCE_EVIDENCE_DIR;
  const python = process.env.ACCEPTANCE_HOST_PYTHON;
  if (!root || !python)
    throw new Error("strict consumer requires owning runner");
  const result = spawnSync(
    python,
    ["-m", "scripts.acceptance.strict_bridge", "--validate-only"],
    {
      cwd: resolve(__dirname, "../.."),
      env: process.env,
      encoding: "utf8",
      timeout: 30_000,
      maxBuffer: 1024 * 1024,
    },
  );
  if (result.error || result.status !== 0)
    throw new Error(
      "strict evidence validation failed; no absent/stale/failed fallback",
    );
  const report = JSON.parse(
    readFileSync(resolve(root, "strict-report.json"), "utf8"),
  );
  const matches = report.scenarios.filter(
    (item: any) => item.requirement === requirement && item.id === identity,
  );
  if (matches.length !== 1)
    throw new Error("strict scenario must have exactly one producer");
  return matches[0];
}

export function strictBootstrap(): StrictBootstrapInput {
  strictScenario("AC02", "parallel_order");
  return JSON.parse(
    readFileSync(
      resolve(process.env.ACCEPTANCE_EVIDENCE_DIR!, "strict-bootstrap.json"),
      "utf8",
    ),
  );
}
