import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { Page } from "@playwright/test";
import { appApi } from "./api";
import { expect } from "@playwright/test";

/** Public terminal identities plus transparent actual kernel boundary observations. */
export async function assertReplayDispatch(
  page: Page,
  replay: string,
  source: string,
  isolated: string,
) {
  const root = process.env.ACCEPTANCE_EVIDENCE_DIR;
  const python = process.env.ACCEPTANCE_HOST_PYTHON;
  if (!root || !python)
    throw new Error("dispatch proof requires owning runner");
  for (const id of [replay, source, isolated]) {
    const view = (await appApi<any>(page, `/execution-runs/${id}/view`)).data;
    expect(view.run.run_id).toBe(id);
    expect(["completed", "failed", "cancelled"]).toContain(view.run.status);
    expect(
      view.steps.some(
        (step: any) => step.kind === "model" && step.status === "completed",
      ),
    ).toBe(true);
  }
  const result = spawnSync(
    python,
    [
      "-m",
      "scripts.acceptance.collect_dispatch_audit",
      "--replay-run",
      replay,
      "--source-run",
      source,
      "--isolated-run",
      isolated,
    ],
    {
      cwd: resolve(__dirname, "../.."),
      env: process.env,
      timeout: 60_000,
      encoding: "utf8",
      maxBuffer: 1024 * 1024,
    },
  );
  if (result.error || result.status !== 0)
    throw new Error(
      "actual dispatch audit missing/incomplete/foreign; cannot claim zero real calls",
    );
  const receipt = JSON.parse(
    readFileSync(resolve(root, `dispatch-${replay}.json`), "utf8"),
  );
  expect(receipt.binding).toEqual(
    JSON.parse(readFileSync(resolve(root, "strict-binding.json"), "utf8")),
  );
  expect(receipt.replay_run_id).toBe(replay);
  expect(receipt.source_run_id).toBe(source);
  expect(receipt.isolated_run_id).toBe(isolated);
  expect(
    createHash("sha256")
      .update(readFileSync(resolve(root, `dispatch-${replay}.ndjson`)))
      .digest("hex"),
  ).toBe(receipt.raw_sha256);
  expect(receipt.observation.catalog_entries).toBe(0);
  expect(receipt.observation.replay_entries).toBeGreaterThan(0);
  return receipt;
}
