import {
  mkdirSync,
  readFileSync,
  readdirSync,
  renameSync,
  writeFileSync,
} from "node:fs";
import { randomUUID } from "node:crypto";
import { basename, dirname, join, resolve } from "node:path";

type RuntimePolicyCleanup = {
  action: "restore-runtime-policy";
  policy: "execution" | "operations";
  revision_id: string;
};

type IntegrationCleanup = {
  action: "set-integration-enabled";
  integration: "mcp-server" | "a2a-server";
  resource_id: string;
  enabled: boolean;
};

type ResourceCleanup = {
  action: "delete-resource";
  resource:
    | "knowledge-base"
    | "session"
    | "team"
    | "patrol-pack"
    | "mcp-server"
    | "a2a-server"
    | "inference-model"
    | "memory";
  resource_id: string;
  workspace_id?: string;
};

export type CleanupAction =
  | RuntimePolicyCleanup
  | IntegrationCleanup
  | ResourceCleanup;

type JournalDocument = {
  schema_version: 1;
  run_id: string;
  order: string;
  value: CleanupAction;
};

export type CleanupEntry = JournalDocument & { path: string };

export type CleanupPhases = {
  resources: CleanupEntry[];
  state: CleanupEntry[];
};

function journalRoot(environment: NodeJS.ProcessEnv): string {
  const evidenceDir = environment.ACCEPTANCE_EVIDENCE_DIR;
  const runId = environment.ACCEPTANCE_RUN_ID;
  if (!evidenceDir || !runId) {
    throw new Error("acceptance cleanup journal requires evidence and run IDs");
  }
  return resolve(evidenceDir, "cleanup-journal");
}

function validateAction(value: CleanupAction): void {
  if ("resource_id" in value && !value.resource_id.trim()) {
    throw new Error("cleanup resource ID must be non-empty");
  }
  if (
    value.action === "delete-resource" &&
    value.workspace_id !== undefined &&
    !value.workspace_id.trim()
  ) {
    throw new Error("cleanup workspace ID must be non-empty when provided");
  }
  if (value.action === "restore-runtime-policy" && !value.revision_id.trim()) {
    throw new Error("cleanup Runtime Policy revision ID must be non-empty");
  }
}

export function registerCleanupAction(
  value: CleanupAction,
  environment: NodeJS.ProcessEnv = process.env,
): CleanupEntry {
  validateAction(value);
  const root = journalRoot(environment);
  const pending = join(root, "pending");
  mkdirSync(pending, { recursive: true });
  const order = process.hrtime.bigint().toString().padStart(20, "0");
  const path = join(pending, `${order}-${randomUUID()}.json`);
  const document: JournalDocument = {
    schema_version: 1,
    run_id: environment.ACCEPTANCE_RUN_ID as string,
    order,
    value,
  };
  writeFileSync(path, `${JSON.stringify(document, null, 2)}\n`, {
    encoding: "utf8",
    mode: 0o600,
    flag: "wx",
  });
  return { ...document, path };
}

export function readCleanupActions(
  environment: NodeJS.ProcessEnv = process.env,
): CleanupEntry[] {
  const pending = join(journalRoot(environment), "pending");
  let names: string[];
  try {
    names = readdirSync(pending).filter((name) => name.endsWith(".json"));
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return [];
    throw error;
  }
  return names
    .map((name) => {
      const path = join(pending, name);
      const document = JSON.parse(
        readFileSync(path, "utf8"),
      ) as JournalDocument;
      if (
        document.schema_version !== 1 ||
        document.run_id !== environment.ACCEPTANCE_RUN_ID
      ) {
        throw new Error(`cleanup journal identity mismatch: ${name}`);
      }
      validateAction(document.value);
      return { ...document, path };
    })
    .sort((left, right) => right.order.localeCompare(left.order));
}

export function partitionCleanupActions(
  entries: readonly CleanupEntry[],
): CleanupPhases {
  return {
    resources: entries.filter(
      (entry) => entry.value.action === "delete-resource",
    ),
    state: entries.filter((entry) => entry.value.action !== "delete-resource"),
  };
}

export function completeCleanupAction(entry: CleanupEntry): void {
  const root = dirname(dirname(entry.path));
  const completed = join(root, "completed");
  mkdirSync(completed, { recursive: true });
  renameSync(entry.path, join(completed, basename(entry.path)));
}
