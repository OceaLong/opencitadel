import { execFile } from "node:child_process";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { promisify } from "node:util";

const execute = promisify(execFile);
const identifier = /^[a-z0-9][a-z0-9-]{2,47}$/;

export function requireOwnedKernel(
  document: any,
  project: string,
  run: string,
) {
  const labels = document?.Config?.Labels ?? {};
  if (
    !identifier.test(project) ||
    !identifier.test(run) ||
    !/^[0-9a-f]{64}$/.test(document?.Id ?? "") ||
    labels["com.docker.compose.project"] !== project ||
    labels["com.docker.compose.service"] !== "opencitadel-execution-kernel" ||
    labels["com.opencitadel.acceptance.project"] !== project ||
    labels["com.opencitadel.acceptance.run"] !== run
  ) {
    throw new Error("Refusing a foreign or unidentified acceptance kernel");
  }
  return document.Id as string;
}

/** Host-only bounded fault, with ownership checked before stop AND restore. */
export async function withStoppedContextWorker(action: () => Promise<void>) {
  if (process.env.ACCEPTANCE_PHASE2_CONTEXT !== "1")
    throw new Error("Worker fault requires the focused owned context runner");
  const project = process.env.ACCEPTANCE_PROJECT_ID ?? "";
  const run = process.env.ACCEPTANCE_RUN_ID ?? "";
  const root = process.env.ACCEPTANCE_EVIDENCE_DIR;
  if (!root || !identifier.test(project) || !identifier.test(run))
    throw new Error("Missing owned invocation identity");
  const lifecycle = JSON.parse(
    await readFile(resolve(root, "lifecycle.json"), "utf8"),
  );
  if (
    lifecycle.project_name !== project ||
    lifecycle.run_id !== run ||
    lifecycle.state !== "stack_ready"
  )
    throw new Error("Acceptance invocation is not ready");
  const docker = async (...args: string[]) =>
    (
      await execute("docker", args, { timeout: 55_000, maxBuffer: 512 * 1024 })
    ).stdout.trim();
  const ids = (
    await docker(
      "ps",
      "-q",
      "--no-trunc",
      "--filter",
      `label=com.opencitadel.acceptance.project=${project}`,
      "--filter",
      `label=com.opencitadel.acceptance.run=${run}`,
      "--filter",
      "label=com.docker.compose.service=opencitadel-execution-kernel",
    )
  )
    .split(/\s+/)
    .filter(Boolean);
  if (ids.length !== 1)
    throw new Error("Expected exactly one owned live kernel");
  const inspect = async () =>
    JSON.parse(await docker("inspect", "--format", "{{json .}}", ids[0]));
  const before = await inspect();
  requireOwnedKernel(before, project, run);
  try {
    await docker("stop", "--time", "20", ids[0]);
    await action();
  } finally {
    const after = await inspect();
    requireOwnedKernel(after, project, run);
    if (after.Id !== before.Id || after.Image !== before.Image)
      throw new Error("Kernel identity changed before restore");
    await docker("start", ids[0]);
  }
}
