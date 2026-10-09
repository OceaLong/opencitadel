import { randomUUID } from "node:crypto";
import type { Page } from "@playwright/test";
import { appApi } from "./api";
import { registerCleanupAction } from "./cleanup-journal";
import { acceptanceId } from "./ids";

export async function publishEvaluation(
  page: Page,
  kind: "config" | "rubric" | "suite",
  definition: unknown,
) {
  const draft = (
    await appApi<any>(page, `/evaluation/${kind}s`, {
      method: "POST",
      body: { request_id: randomUUID(), name: acceptanceId(kind), definition },
    })
  ).data;
  registerCleanupAction({
    action: "delete-resource",
    resource: `evaluation-${kind}`,
    resource_id: draft.id,
  });
  return (
    await appApi<any>(page, `/evaluation/${kind}s/${draft.id}/publish`, {
      method: "POST",
      body: { request_id: randomUUID(), expected_revision: draft.revision },
    })
  ).data;
}

export async function confirmedDataset(
  page: Page,
  entries: Array<{ key: string; input: string; reference?: string }>,
) {
  let draft = (
    await appApi<any>(page, "/evaluation/datasets", {
      method: "POST",
      body: {
        request_id: randomUUID(),
        expected_revision: 0,
        name: acceptanceId("fixed-data"),
      },
    })
  ).data;
  registerCleanupAction({
    action: "delete-resource",
    resource: "evaluation-dataset",
    resource_id: draft.id,
  });
  for (const entry of entries)
    draft = (
      await appApi<any>(
        page,
        `/evaluation/datasets/${draft.id}/cases/${entry.key}`,
        {
          method: "PATCH",
          body: {
            request_id: randomUUID(),
            expected_revision: draft.revision,
            case: {
              input: entry.input,
              input_confirmed: true,
              reference_answer: entry.reference ?? null,
              reference_confirmed: true,
              applicable_dimensions: ["correctness"],
            },
          },
        },
      )
    ).data;
  const version = (
    await appApi<any>(page, `/evaluation/datasets/${draft.id}/publish`, {
      method: "POST",
      body: { request_id: randomUUID(), expected_revision: draft.revision },
    })
  ).data;
  return { draft, version };
}

export async function standardRubric(page: Page, modelId: string) {
  const judge = await publishEvaluation(page, "config", {
    model_id: modelId,
    purpose: "evaluation_judge",
    mode: "ask",
    max_output_tokens: 4096,
  });
  return publishEvaluation(page, "rubric", {
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
  });
}
