import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, type Page } from "@playwright/test";
import { appApi } from "./api";
import { pollProjection } from "./poll";

/** Wait for the public usage projection of the exact settled physical calls.
 * Evaluation completion and execution projection advance independently. A
 * comparison must capture after these facts are observable, without reading
 * beyond its fixed watermark or converting missing usage into zero.
 */
export async function waitAccountingUsageProjection(
  page: Page,
  receipt: { evidence: { calls: any[] } },
) {
  const runs = new Map<string, Map<string, any[]>>();
  for (const call of receipt.evidence.calls) {
    const purposes = runs.get(call.run_id) ?? new Map<string, any[]>();
    purposes.set(call.purpose, [...(purposes.get(call.purpose) ?? []), call]);
    runs.set(call.run_id, purposes);
  }
  for (const [run, purposes] of runs) {
    await pollProjection(
      () =>
        appApi<any>(page, `/execution-runs/${run}/view`).then((v) => v.data),
      (view) =>
        [...purposes].every(([purpose, calls]) => {
          const usage = view.run.usage?.[purpose];
          return (
            usage?.calls === calls.length &&
            usage.unknown_cost_calls ===
              calls.filter((call) => call.fact.cost_usd === null).length &&
            usage.unknown_usage_calls ===
              calls.filter(
                (call) =>
                  call.fact.usage.prompt_tokens === null ||
                  call.fact.usage.completion_tokens === null,
              ).length &&
            usage.known_input_count ===
              calls.reduce(
                (sum, call) => sum + (call.fact.usage.prompt_tokens ?? 0),
                0,
              ) &&
            usage.known_output_count ===
              calls.reduce(
                (sum, call) => sum + (call.fact.usage.completion_tokens ?? 0),
                0,
              )
          );
        }),
      {
        timeout: 30_000,
        message: "settled physical accounting reaches public run usage",
      },
    );
  }
}

/** Re-read immutable settlements and actual terminal cleanup; never release a hold. */
export function collectAccountingRetention(batchId: string) {
  const root = process.env.ACCEPTANCE_EVIDENCE_DIR;
  const python = process.env.ACCEPTANCE_HOST_PYTHON;
  if (!root || !python)
    throw new Error("accounting retention requires owning runner");
  const result = spawnSync(
    python,
    ["-m", "scripts.acceptance.collect_accounting", "--batch-id", batchId],
    {
      cwd: resolve(__dirname, "../.."),
      env: process.env,
      timeout: 60_000,
      encoding: "utf8",
      maxBuffer: 1024 * 1024,
    },
  );
  if (result.error || result.status !== 0)
    throw new Error("terminal accounting retention is unverified");
  const receipt = JSON.parse(
    readFileSync(
      resolve(root, `evaluation-lifecycle-accounting-${batchId}.json`),
      "utf8",
    ),
  );
  expect(receipt.binding).toEqual(
    JSON.parse(readFileSync(resolve(root, "strict-binding.json"), "utf8")),
  );
  const raw = readFileSync(
    resolve(root, `evaluation-lifecycle-accounting-${batchId}.raw.json`),
  );
  expect(createHash("sha256").update(raw).digest("hex")).toBe(
    receipt.raw_sha256,
  );
  expect(JSON.parse(raw.toString())).toEqual(receipt.evidence);
  expect(receipt.evidence.batch_id).toBe(batchId);
  expect(receipt.status).toBe("retained-accounting");
  expect(receipt.evidence.retention.physical_slots).toBe(0);
  expect(receipt.evidence.retention.environment_status).toBe("clean");
  expect(
    receipt.evidence.retention.missing_subject_calls.length,
  ).toBeGreaterThan(0);
  expect(receipt.evidence.retention.known_judge_calls.length).toBeGreaterThan(
    0,
  );
  return receipt;
}
