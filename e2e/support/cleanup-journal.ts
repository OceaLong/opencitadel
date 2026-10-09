import {
  mkdirSync,
  openSync,
  closeSync,
  fsyncSync,
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

export type OwnedActorCleanup = {
  action: "disable-owned-actor";
  resource_id: string;
  workspace_id: string;
  email: string;
  recovery_id: string;
  registration: {
    user_id: string;
    team_id: string;
    role: string;
    joined_at: string;
  };
};

type ResourceCleanup = {
  action: "delete-resource";
  resource:
    | "artifact-share"
    | "file"
    | "knowledge-base"
    | "session"
    | "team"
    | "patrol-pack"
    | "mcp-server"
    | "a2a-server"
    | "inference-model"
    | "inference-binding"
    | "memory"
    | "evaluation-dataset"
    | "evaluation-config"
    | "evaluation-rubric"
    | "evaluation-suite"
    | "evaluation-recording"
    | "evaluation-environment"
    | "evaluation-batch"
    | "execution-comparison"
    | "execution-export";
  resource_id: string;
  workspace_id?: string;
  creator_id?: string;
  export_binding?: import("./creator-export").ExportBinding;
  export_download?: import("./creator-export").ExportDownloadProof;
  retained_revision?: number;
  created_at?: string;
  retained_accounting?: boolean;
  expected_unknown_retention?: true;
  expected_retention?: {
    resource_version: string;
    owners: { owner_kind: string; owner_id: string }[];
  };
};

export type CleanupAction =
  | RuntimePolicyCleanup
  | IntegrationCleanup
  | ResourceCleanup
  | OwnedActorCleanup;

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

function durableFile(path: string, content: string): void {
  const fd = openSync(path, "wx", 0o600);
  try {
    writeFileSync(fd, content, "utf8");
    fsyncSync(fd);
  } finally {
    closeSync(fd);
  }
  syncDirectory(dirname(path));
}
function syncDirectory(path: string): void {
  const fd = openSync(path, "r");
  try {
    fsyncSync(fd);
  } finally {
    closeSync(fd);
  }
}

function journalRoot(environment: NodeJS.ProcessEnv): string {
  const evidenceDir = environment.ACCEPTANCE_EVIDENCE_DIR;
  const runId = environment.ACCEPTANCE_RUN_ID;
  if (!evidenceDir || !runId) {
    throw new Error("acceptance cleanup journal requires evidence and run IDs");
  }
  return resolve(evidenceDir, "cleanup-journal");
}

function validateAction(value: CleanupAction): void {
  if (
    "expected_unknown_retention" in value &&
    (value.action !== "delete-resource" ||
      value.resource !== "evaluation-batch" ||
      value.expected_unknown_retention !== true)
  )
    throw new Error(
      "expected unknown retention requires an exact evaluation batch",
    );
  if (
    value.action === "disable-owned-actor" &&
    (value.registration.user_id !== value.resource_id ||
      value.registration.team_id !== value.workspace_id ||
      value.registration.role !== "member" ||
      !Number.isFinite(Date.parse(value.registration.joined_at)) ||
      !value.email ||
      !/^[a-f0-9-]{36}$/.test(value.recovery_id))
  )
    throw new Error("owned actor registration binding mismatch");
  if (
    value.action === "delete-resource" &&
    value.resource === "execution-comparison" &&
    (!Number.isInteger(value.retained_revision) || value.retained_revision! < 1)
  )
    throw new Error("comparison cleanup requires its returned revision");
  if (
    value.action === "delete-resource" &&
    value.resource === "execution-export" &&
    (!value.created_at || !Number.isFinite(Date.parse(value.created_at)))
  )
    throw new Error("export cleanup requires its returned created_at");
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
  if (
    value.action === "delete-resource" &&
    value.retained_accounting !== undefined &&
    (value.retained_accounting !== true ||
      value.resource !== "evaluation-batch")
  ) {
    throw new Error("accounting retention requires an exact evaluation batch");
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
  durableFile(path, `${JSON.stringify(document, null, 2)}\n`);
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
      (entry) =>
        entry.value.action === "delete-resource" ||
        entry.value.action === "disable-owned-actor",
    ),
    state: entries.filter(
      (entry) =>
        entry.value.action !== "delete-resource" &&
        entry.value.action !== "disable-owned-actor",
    ),
  };
}

export function completeCleanupAction(entry: CleanupEntry): void {
  const root = dirname(dirname(entry.path));
  const completed = join(root, "completed");
  mkdirSync(completed, { recursive: true });
  const destination = join(completed, basename(entry.path));
  try {
    renameSync(entry.path, destination);
    syncDirectory(completed);
    syncDirectory(dirname(entry.path));
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    const previous = JSON.parse(
      readFileSync(destination, "utf8"),
    ) as JournalDocument;
    if (
      previous.schema_version !== entry.schema_version ||
      previous.run_id !== entry.run_id ||
      previous.order !== entry.order ||
      JSON.stringify(previous.value) !== JSON.stringify(entry.value)
    )
      throw new Error("completed cleanup journal identity mismatch");
  }
}

export function bindExpectedRetention(
  entry: CleanupEntry,
  retention: NonNullable<ResourceCleanup["expected_retention"]>,
): void {
  if (
    entry.value.action !== "delete-resource" ||
    entry.value.resource !== "file" ||
    !retention.owners.length
  )
    throw new Error("retention requires an owned file and published owners");
  entry.value.expected_retention = retention;
  const temporary = entry.path + ".tmp";
  writeFileSync(
    temporary,
    JSON.stringify(
      {
        schema_version: entry.schema_version,
        run_id: entry.run_id,
        order: entry.order,
        value: entry.value,
      },
      null,
      2,
    ),
    { mode: 0o600 },
  );
  renameSync(temporary, entry.path);
}

/** A failed scope retains its access dependencies, including on journal replay. */
export async function runCleanupResources(
  entries: readonly CleanupEntry[],
  execute: (entry: CleanupEntry) => Promise<void>,
  complete: (entry: CleanupEntry) => void,
): Promise<string[]> {
  const rank = (entry: CleanupEntry): number => {
    // Release an owned actor after its children, while the team still exists.
    if (entry.value.action === "disable-owned-actor") return 2;
    if (entry.value.action !== "delete-resource")
      throw new Error("resource cleanup action required");
    return entry.value.resource === "execution-export"
      ? 0
      : entry.value.resource === "team"
        ? 3
        : 1;
  };
  const ordered = [...entries].sort((a, b) => rank(a) - rank(b));
  const failedScopes = new Set<string>();
  const failedCreators = new Set<string>();
  const errors: string[] = [];
  for (const entry of ordered) {
    const value = entry.value;
    if (
      value.action !== "delete-resource" &&
      value.action !== "disable-owned-actor"
    )
      throw new Error("resource cleanup action required");
    const scope =
      value.action === "delete-resource" && value.resource === "team"
        ? value.resource_id
        : value.workspace_id;
    if (
      (scope && failedScopes.has(scope)) ||
      (value.action === "disable-owned-actor" &&
        failedCreators.has(value.resource_id))
    ) {
      errors.push(`cleanup journal ${entry.order}: dependency remains pending`);
      continue;
    }
    try {
      await execute(entry);
      complete(entry);
    } catch {
      // Exception messages may contain credential-bearing URLs or API payloads.
      errors.push(
        `cleanup journal ${entry.order}: resource verification failed`,
      );
      if (scope) failedScopes.add(scope);
      if (value.action === "delete-resource" && value.creator_id)
        failedCreators.add(value.creator_id);
    }
  }
  return errors;
}

export function bindExportDownload(
  entry: CleanupEntry,
  proof: import("./creator-export").ExportDownloadProof,
): void {
  if (
    entry.value.action !== "delete-resource" ||
    entry.value.resource !== "execution-export" ||
    !entry.value.export_binding ||
    Object.entries(entry.value.export_binding).some(
      ([key, value]) => proof[key as keyof typeof proof] !== value,
    )
  )
    throw new Error("export download journal binding mismatch");
  entry.value.export_download = proof;
  const temporary = entry.path + "." + randomUUID() + ".tmp";
  const { path: _path, ...document } = entry;
  durableFile(temporary, JSON.stringify(document));
  renameSync(temporary, entry.path);
  syncDirectory(dirname(entry.path));
}

/** The actual resource/bootstrap/state lifecycle; actor disposal has one owner. */
export async function runCleanupPhases(
  entries: readonly CleanupEntry[],
  execute: (entry: CleanupEntry) => Promise<void>,
  complete: (entry: CleanupEntry) => void,
  between: () => Promise<void>,
): Promise<string[]> {
  const phases = partitionCleanupActions(entries);
  const errors = await runCleanupResources(phases.resources, execute, complete);
  try {
    await between();
  } catch {
    errors.push("bootstrap cleanup failed");
  }
  for (const entry of phases.state) {
    try {
      await execute(entry);
      complete(entry);
    } catch {
      errors.push(`cleanup journal ${entry.order}: state restoration failed`);
    }
  }
  return errors;
}
