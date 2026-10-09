import { spawnSync } from "node:child_process";
import { writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect } from "@playwright/test";

/** Host only: browser input carries public IDs; no object keys or private bodies. */
export function physicalFault(
  action: "arm" | "snapshot" | "disarm" | "cancel",
  values: Record<string, string>,
) {
  const python = process.env.ACCEPTANCE_HOST_PYTHON;
  if (!python || !process.env.ACCEPTANCE_EVIDENCE_DIR)
    throw new Error("physical fault requires owning runner");
  const args = ["-m", "scripts.acceptance.physical_fault_host", action];
  for (const [key, value] of Object.entries(values))
    args.push(`--${key}`, value);
  const result = spawnSync(python, args, {
    cwd: resolve(__dirname, "../.."),
    env: process.env,
    encoding: "utf8",
    timeout: action === "arm" ? 280_000 : 120_000,
    maxBuffer: 262144,
  });
  if (result.error || result.status !== 0) {
    writeFileSync(
      resolve(
        process.env.ACCEPTANCE_EVIDENCE_DIR,
        `physical-fault-${action}-failure.log`,
      ),
      result.stderr || result.error?.message || `host exited ${result.status}`,
      { mode: 0o600 },
    );
    throw new Error(
      "Physical fault evidence or cleanup failed; private host evidence retained",
    );
  }
  return JSON.parse(result.stdout);
}

export function unchangedFault(before: any, after: any) {
  for (const key of [
    "fault_id",
    "boot_id",
    "source_sha256",
    "execution_run_id",
    "activity_id",
  ])
    expect(after[key]).toBe(before[key]);
  expect(after.counts).toEqual(before.counts);
  expect(after.observation.generation).toBe(before.observation.generation);
  expect(after.observation.claim_generation).toBe(
    before.observation.claim_generation,
  );
  expect(after.observation.task_status).toBe(before.observation.task_status);
  expect(
    after.journal.filter((row: any) => row.event === "physical_send"),
  ).toHaveLength(1);
  expect(after.observation.marker_lines).toBe(1);
}

/** Only opt-in fault lifecycles serialize; ordinary project tests stay parallel. */
export async function acquirePhysicalFaultLifecycle() {
  const { randomUUID } = await import("node:crypto");
  const token = randomUUID();
  function coordinate(
    action: "acquire" | "bind" | "release" | "fail",
    fault?: string,
  ) {
    const python = process.env.ACCEPTANCE_HOST_PYTHON;
    if (!python) throw new Error("fault lifecycle requires owning runner");
    const result = spawnSync(
      python,
      [
        "-m",
        "scripts.acceptance.fault_lifecycle",
        action,
        "--token",
        token,
        "--pid",
        String(process.pid),
        ...(fault ? ["--fault-id", fault] : []),
      ],
      {
        cwd: resolve(__dirname, "../.."),
        env: process.env,
        encoding: "utf8",
        timeout: 10_000,
        maxBuffer: 16384,
      },
    );
    if (result.error || result.status !== 0)
      throw new Error(
        "fault lifecycle unresolved; private coordinator retained",
      );
    return JSON.parse(result.stdout);
  }
  const deadline = Date.now() + 180_000;
  while (true) {
    const result = coordinate("acquire");
    if (result.acquired === true) break;
    if (result.busy !== true || Date.now() >= deadline)
      throw new Error("fault lifecycle acquisition bounded wait failed");
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  let held = true;
  return {
    arm(values: Record<string, string>) {
      if (!held) throw new Error("fault lifecycle already released");
      const result = physicalFault("arm", values);
      coordinate("bind", result.fault_id);
      return result;
    },
    release(receipt: any) {
      if (!held || !receipt?.fault_id)
        throw new Error("fault lifecycle release lacks disarm receipt");
      coordinate("release", receipt.fault_id);
      held = false;
    },
    retainFailure() {
      if (held) coordinate("fail");
    },
  };
}
