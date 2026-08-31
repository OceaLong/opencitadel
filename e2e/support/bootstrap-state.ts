import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";

export type BootstrapCapabilityName = "chat" | "embeddings" | "rerank";

export type BootstrapCapabilityState = {
  state: "available" | "degraded" | "not_configured" | "disabled" | "denied";
  model_id: string | null;
  reason_key: string | null;
};

export type BootstrapCapabilityProjection = Record<
  BootstrapCapabilityName,
  BootstrapCapabilityState
>;

export type BootstrapState = {
  schema_version: 1;
  run_id: string;
  endpoint_id?: string;
  model_ids: {
    chat?: string;
    embedding?: string;
  };
  binding_purposes: Array<"chat" | "embedding" | "rerank">;
  capability_transition?: {
    before: BootstrapCapabilityProjection;
    after: BootstrapCapabilityProjection;
  };
  cleanup_completed: boolean;
};

export function bootstrapStatePath(
  environment: NodeJS.ProcessEnv = process.env,
): string {
  const evidenceDir = environment.ACCEPTANCE_EVIDENCE_DIR;
  if (!evidenceDir) {
    throw new Error("ACCEPTANCE_EVIDENCE_DIR is required");
  }
  return resolve(evidenceDir, "bootstrap.json");
}

export function writeBootstrapState(state: BootstrapState): void {
  const path = bootstrapStatePath();
  mkdirSync(dirname(path), { recursive: true });
  const temporary = `${path}.tmp-${process.pid}`;
  writeFileSync(temporary, `${JSON.stringify(state, null, 2)}\n`, {
    encoding: "utf8",
    mode: 0o600,
  });
  renameSync(temporary, path);
}

export function readBootstrapState(): BootstrapState | undefined {
  const path = bootstrapStatePath();
  try {
    const value = JSON.parse(readFileSync(path, "utf8")) as BootstrapState;
    if (
      value.schema_version !== 1 ||
      value.run_id !== process.env.ACCEPTANCE_RUN_ID
    ) {
      throw new Error("bootstrap state belongs to a different acceptance run");
    }
    return value;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return undefined;
    throw error;
  }
}
