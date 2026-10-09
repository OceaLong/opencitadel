import { spawnSync } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { isDeepStrictEqual } from "node:util";

type Proof = {
  binding: Record<string, unknown>;
  owner_id: string;
  before_sha256: string;
  after_sha256?: string;
};

const digest = (bytes: Buffer) =>
  createHash("sha256").update(bytes).digest("hex");

const comparisonPolicy =
  "typed-source-equality-with-monotonic-judge-work-checked-at";

function clockMicroseconds(value: unknown): bigint {
  if (typeof value !== "string")
    throw new Error("protected retention scheduler clock is unverified");
  const parts =
    /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(Z|[+-]\d{2}:\d{2})$/.exec(
      value,
    );
  if (!parts)
    throw new Error("protected retention scheduler clock is unverified");
  const [year, month, day, hour, minute, second] = parts
    .slice(1, 7)
    .map(Number);
  const calendar = new Date(0);
  calendar.setUTCFullYear(year, month - 1, day);
  calendar.setUTCHours(hour, minute, second, 0);
  if (
    calendar.getUTCFullYear() !== year ||
    calendar.getUTCMonth() !== month - 1 ||
    calendar.getUTCDate() !== day ||
    calendar.getUTCHours() !== hour ||
    calendar.getUTCMinutes() !== minute ||
    calendar.getUTCSeconds() !== second
  )
    throw new Error("protected retention scheduler clock is unverified");
  const zone = parts[8];
  const zoneHour = zone === "Z" ? 0 : Number(zone.slice(1, 3));
  const zoneMinute = zone === "Z" ? 0 : Number(zone.slice(4, 6));
  if (zoneHour > 23 || zoneMinute > 59)
    throw new Error("protected retention scheduler clock is unverified");
  const offset = (zoneHour * 60 + zoneMinute) * (zone.startsWith("-") ? -1 : 1);
  return (
    BigInt(calendar.getTime() - offset * 60_000) * BigInt(1_000) +
    BigInt((parts[7] ?? "").padEnd(6, "0"))
  );
}

function observedSchedulerClocks(before: any, after: any, scope: string) {
  const first = before.tables?.judge_work;
  const second = after.tables?.judge_work;
  if (
    !Array.isArray(first) ||
    !Array.isArray(second) ||
    first.length !== second.length
  )
    throw new Error("protected retention scheduler clock is unverified");
  const observations = [];
  for (let index = 0; index < first.length; index++) {
    const left = first[index],
      right = second[index];
    if (
      !left ||
      !right ||
      typeof left.intent_id !== "string" ||
      left.intent_id !== right.intent_id ||
      left.scope_key !== scope ||
      right.scope_key !== scope ||
      Object.hasOwn(left, "checked_at") !== Object.hasOwn(right, "checked_at")
    )
      throw new Error("protected retention scheduler clock is unverified");
    if (!Object.hasOwn(left, "checked_at")) continue;
    const oldClock = left.checked_at,
      newClock = right.checked_at;
    if (oldClock === null && newClock === null) continue;
    if (
      typeof oldClock !== "string" ||
      typeof newClock !== "string" ||
      clockMicroseconds(newClock) < clockMicroseconds(oldClock)
    )
      throw new Error("protected retention scheduler clock is unverified");
    observations.push({
      table: "judge_work",
      field: "checked_at",
      intent_id: left.intent_id,
      scope_key: left.scope_key,
      before: oldClock,
      after: newClock,
    });
  }
  return observations;
}

/** Validate runner-owned receipts against actual current raw readbacks. No hold is released. */
export function readProtectedRetentionProof(
  batchId: string,
  phase: "before" | "after",
  before?: Proof,
  environment: NodeJS.ProcessEnv = process.env,
): Proof {
  const root = environment.ACCEPTANCE_EVIDENCE_DIR;
  if (!root || !/^[a-f0-9-]{36}$/i.test(batchId))
    throw new Error("protected retention evidence identity required");
  const read = (name: string) => readFileSync(resolve(root, name));
  const json = (name: string) => JSON.parse(read(name).toString("utf8"));
  const binding = json("strict-binding.json");
  const bootstrap = json("strict-bootstrap.json");
  if (
    !environment.ACCEPTANCE_RUN_ID ||
    !environment.ACCEPTANCE_PROJECT_ID ||
    !environment.ACCEPTANCE_STRICT_INVOCATION_ID ||
    binding.schema_version !== 1 ||
    binding.run_id !== environment.ACCEPTANCE_RUN_ID ||
    binding.project !== environment.ACCEPTANCE_PROJECT_ID ||
    binding.invocation_id !== environment.ACCEPTANCE_STRICT_INVOCATION_ID ||
    bootstrap.run_id !== binding.run_id ||
    bootstrap.project !== binding.project ||
    typeof bootstrap.operator_id !== "string" ||
    !bootstrap.operator_id
  )
    throw new Error("protected retention invocation binding mismatch");
  const prefix = `protected-retention-${batchId}`;
  const initial = json(`${prefix}.before.json`);
  const rawBefore = read(`${prefix}.before.raw.json`);
  const beforeSha = digest(rawBefore);
  const verification = {
    resource_id: batchId,
    batch_scope: `user:${bootstrap.operator_id}`,
    owner_id: bootstrap.operator_id,
    physically_deleted: false,
    archived: false,
    settled: false,
    future_obligation: "open",
    immutable_unknown_obligations: true,
    local_active_sends: 0,
    environment_status: "clean",
  };
  if (
    initial.schema_version !== 1 ||
    initial.status !== "protected-retention-observed" ||
    !isDeepStrictEqual(initial.binding, binding) ||
    initial.raw_sha256 !== beforeSha ||
    !isDeepStrictEqual(initial.verification, verification)
  )
    throw new Error("protected retention before proof is unverified");
  if (phase === "before")
    return {
      binding,
      owner_id: bootstrap.operator_id,
      before_sha256: beforeSha,
    };
  const final = json(`${prefix}.json`);
  const rawAfter = read(`${prefix}.after.raw.json`);
  const afterSha = digest(rawAfter);
  if (
    !before ||
    !isDeepStrictEqual(before.binding, binding) ||
    before.owner_id !== bootstrap.operator_id ||
    before.before_sha256 !== beforeSha ||
    final.schema_version !== 1 ||
    final.status !== "verified-protected-retention" ||
    !isDeepStrictEqual(final.binding, binding) ||
    final.before_sha256 !== beforeSha ||
    final.after_sha256 !== afterSha ||
    final.comparison_policy !== comparisonPolicy ||
    !isDeepStrictEqual(
      final.observed_clock_transitions,
      observedSchedulerClocks(
        JSON.parse(rawBefore.toString("utf8")),
        JSON.parse(rawAfter.toString("utf8")),
        `user:${bootstrap.operator_id}`,
      ),
    ) ||
    !isDeepStrictEqual(final.verification, verification) ||
    !isDeepStrictEqual(
      final.evidence?.before,
      JSON.parse(rawBefore.toString("utf8")),
    ) ||
    !isDeepStrictEqual(
      final.evidence?.after,
      JSON.parse(rawAfter.toString("utf8")),
    )
  )
    throw new Error("protected retention after proof is unverified");
  return { ...before, after_sha256: afterSha };
}

export function collectProtectedRetention(
  batchId: string,
  phase: "before" | "after",
  before?: Proof,
): Proof {
  const python = process.env.ACCEPTANCE_HOST_PYTHON;
  if (!python || !process.env.ACCEPTANCE_EVIDENCE_DIR)
    throw new Error("protected retention requires owning runner");
  const result = spawnSync(
    python,
    [
      "-m",
      "scripts.acceptance.collect_protected_retention",
      "--batch-id",
      batchId,
      "--phase",
      phase,
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
    throw new Error("actual protected retention readback is unverified");
  return readProtectedRetentionProof(batchId, phase, before);
}

/** This finishes local housekeeping only; retained accounting remains an open obligation. */
export function recordProtectedRetention(
  batchId: string,
  proof: Proof,
  publicReadback: { revision: number; status: string; cleanup_status: "clean" },
): void {
  if (!proof.after_sha256)
    throw new Error("protected retention after proof required");
  const directory = resolve(
    process.env.ACCEPTANCE_EVIDENCE_DIR!,
    "retained-resources",
  );
  mkdirSync(directory, { recursive: true });
  const prefix = `protected-retention-${batchId}`;
  const path = resolve(directory, `${prefix}.json`);
  const temporary = `${path}.${randomUUID()}.tmp`;
  const references = Object.fromEntries(
    [".before.json", ".json", ".before.raw.json", ".after.raw.json"].map(
      (suffix) => {
        const file = `${prefix}${suffix}`;
        return [
          suffix,
          {
            path: file,
            sha256: digest(
              readFileSync(resolve(process.env.ACCEPTANCE_EVIDENCE_DIR!, file)),
            ),
          },
        ];
      },
    ),
  );
  writeFileSync(
    temporary,
    JSON.stringify(
      {
        schema_version: 1,
        status: "verified-protected-retention",
        resource: "evaluation-batch",
        resource_id: batchId,
        binding: proof.binding,
        owner_id: proof.owner_id,
        batch_scope: `user:${proof.owner_id}`,
        operation: "archive",
        response_status: 400,
        public_readback: publicReadback,
        physically_deleted: false,
        archived: false,
        settled: false,
        future_obligation: "open",
        cleanup_obligation: "protected-retention-accounting-open",
        disposition_verified: true,
        before_sha256: proof.before_sha256,
        after_sha256: proof.after_sha256,
        proof_references: references,
      },
      null,
      2,
    ),
    { mode: 0o600 },
  );
  renameSync(temporary, path);
}
