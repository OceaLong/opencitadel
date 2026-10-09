import { mkdirSync, writeFileSync, renameSync } from "node:fs";
import { resolve } from "node:path";
import { createHash, randomUUID } from "node:crypto";
import type { Page } from "@playwright/test";

import { appApi } from "./api";
import type { CleanupAction } from "./cleanup-journal";

type ProductResource = Extract<
  CleanupAction,
  { action: "delete-resource" }
>["resource"];

type SessionState = {
  status:
    | "pending"
    | "running"
    | "waiting"
    | "completed"
    | "cancelled"
    | "failed";
};

type PatrolPackState = {
  status: "draft" | "validating" | "active" | "paused" | "invalid";
};

const RESOURCE_PATHS = {
  "artifact-share": "/artifacts",
  "execution-comparison": "/execution-comparisons",
  "execution-export": "/execution-analysis/exports",
  file: "/files",
  "knowledge-base": "/knowledge-bases",
  session: "/sessions",
  team: "/teams",
  "patrol-pack": "/patrol-packs",
  "mcp-server": "/integrations/mcp-servers",
  "a2a-server": "/integrations/a2a-servers",
  "inference-model": "/inference/models",
  "inference-binding": "/inference/bindings",
  memory: "/memories",
  "evaluation-dataset": "/evaluation/datasets",
  "evaluation-config": "/evaluation/configs",
  "evaluation-rubric": "/evaluation/rubrics",
  "evaluation-suite": "/evaluation/suites",
  "evaluation-recording": "/evaluation/recordings",
  "evaluation-environment": "/evaluation/environments",
  "evaluation-batch": "/evaluation/batches",
} as const satisfies Record<ProductResource, string>;

function recordBlockedCleanup(
  resource: ProductResource,
  resourceId: string,
  workspaceId: string | undefined,
  evidence: {
    operation: "archive" | "delete";
    response_status: number;
    response_error_key?: string;
    observed_revision?: number;
    observed_status?: string;
    observed_cleanup_status?: string;
  },
): void {
  const root = process.env.ACCEPTANCE_EVIDENCE_DIR;
  if (!root) throw new Error("blocked cleanup evidence directory required");
  const directory = resolve(root, "retained-resources");
  mkdirSync(directory, { recursive: true });
  const identity = createHash("sha256")
    .update(
      JSON.stringify(["cleanup-blocked", resource, resourceId, workspaceId]),
    )
    .digest("hex");
  const path = resolve(directory, `${identity}.json`);
  const temporary = `${path}.${randomUUID()}.tmp`;
  writeFileSync(
    temporary,
    JSON.stringify(
      {
        status: "cleanup-blocked",
        resource,
        resource_id: resourceId,
        workspace_id: workspaceId ?? null,
        ...evidence,
        physically_deleted: false,
        cleanup_obligation: "pending",
        // A failed public command is evidence of retention, not an archive or
        // deletion receipt. The journal must remain pending for review/replay.
        disposition_verified: false,
      },
      null,
      2,
    ),
    { mode: 0o600 },
  );
  renameSync(temporary, path);
}

export async function cleanupProductResource(
  page: Page,
  resource: ProductResource,
  resourceId: string,
  options: {
    exportBinding?: import("./creator-export").ExportBinding;
    exportDownload?: import("./creator-export").ExportDownloadProof;
    retainedRevision?: number;
    retainedAccounting?: boolean;
    expectedUnknownRetention?: true;
    createdAt?: string;
    workspaceId?: string;
    expectedRetention?: {
      resource_version: string;
      owners: { owner_kind: string; owner_id: string }[];
    };
  } = {},
): Promise<void> {
  if (
    options.expectedUnknownRetention !== undefined &&
    (resource !== "evaluation-batch" ||
      options.expectedUnknownRetention !== true ||
      options.workspaceId !== undefined)
  )
    throw new Error(
      "expected unknown retention requires an exact personal evaluation batch",
    );
  const collection = RESOURCE_PATHS[resource];
  const resourcePath = `${collection}/${encodeURIComponent(resourceId)}${resource === "inference-binding" ? "?binding_scope=workspace" : resource === "artifact-share" ? "/share" : ""}`;
  if (resource === "inference-binding" && !options.workspaceId) {
    throw new Error(
      "workspace binding cleanup requires an explicit workspace ID",
    );
  }
  const scopedHeaders = options.workspaceId
    ? { "X-Workspace-Id": options.workspaceId }
    : undefined;
  if (resource === "execution-comparison" || resource === "execution-export") {
    if (!process.env.ACCEPTANCE_EVIDENCE_DIR)
      throw new Error("retention evidence directory required");
    let evidence: Record<string, unknown>;
    if (resource === "execution-comparison") {
      if (
        !Number.isInteger(options.retainedRevision) ||
        options.retainedRevision! < 1
      )
        throw new Error("retained comparison revision required");
      const current = (
        await appApi<{ comparison_id: string; revision: number }>(
          page,
          `${resourcePath}?revision=${options.retainedRevision}`,
          { headers: scopedHeaders },
        )
      ).data;
      if (
        current.comparison_id !== resourceId ||
        current.revision !== options.retainedRevision
      )
        throw new Error("retained comparison identity mismatch");
      evidence = { status: "retained-history", revision: current.revision };
    } else {
      const current = (
        await appApi<{
          id: string;
          status: string;
          created_at: string;
          expires_at: string;
        }>(page, resourcePath, { headers: scopedHeaders })
      ).data;
      if (options.exportBinding) {
        const me = (await appApi<{ id: string }>(page, "/auth/me")).data;
        const { verifyCreatorExportRetention } =
          await import("./creator-export");
        if (
          options.exportBinding.run_id !== process.env.ACCEPTANCE_RUN_ID ||
          options.exportBinding.export_id !== resourceId ||
          options.exportBinding.workspace_id !== options.workspaceId ||
          options.exportBinding.created_at !== options.createdAt
        )
          throw new Error("export cleanup invocation binding mismatch");
        evidence = verifyCreatorExportRetention(
          options.exportBinding,
          me.id,
          current,
          options.exportDownload,
        );
      } else {
        if (
          !options.createdAt ||
          current.id !== resourceId ||
          current.created_at !== options.createdAt
        )
          throw new Error("retained export identity mismatch");
        if (
          !["ready", "failed", "invalidated", "expired"].includes(
            current.status,
          )
        )
          throw new Error("owned export generation remains pending");
        if (!Number.isFinite(Date.parse(current.expires_at)))
          throw new Error("owned export expiry is unavailable");
        evidence = {
          status: "retained-until-expiry",
          generation_status: current.status,
          created_at: current.created_at,
          expires_at: current.expires_at,
          gc_obligation: "pending-kernel-expiry-and-object-GC",
        };
      }
    }
    const directory = resolve(
      process.env.ACCEPTANCE_EVIDENCE_DIR,
      "retained-resources",
    );
    mkdirSync(directory, { recursive: true });
    const identity = createHash("sha256")
      .update(
        JSON.stringify([
          resource,
          resourceId,
          options.workspaceId,
          options.retainedRevision,
        ]),
      )
      .digest("hex");
    const path = resolve(directory, `${identity}.json`);
    const temporary = `${path}.${randomUUID()}.tmp`;
    writeFileSync(
      temporary,
      JSON.stringify(
        {
          ...evidence,
          resource,
          resource_id: resourceId,
          workspace_id: options.workspaceId ?? null,
          physically_deleted: false,
        },
        null,
        2,
      ),
      { mode: 0o600 },
    );
    renameSync(temporary, path);
    return;
  }
  if (resource === "file" && options.expectedRetention) {
    const removed = await appApi<{
      resource_kind?: string;
      resource_id?: string;
    }>(page, resourcePath, {
      method: "DELETE",
      headers: scopedHeaders,
      expectStatus: [200, 404, 409],
    });
    if (removed.status !== 409) return;
    if (
      removed.errorKey !== "resource_pinned" ||
      removed.data.resource_kind !== "file" ||
      removed.data.resource_id !== resourceId
    )
      throw new Error("file cleanup conflict is not verified retention");
    const query = new URLSearchParams({
      resource_kind: "file",
      resource_id: resourceId,
      resource_version: options.expectedRetention.resource_version,
    });
    const pins = (
      await appApi<
        { owner_kind: string; owner_id: string; resource_version: string }[]
      >(page, `/evaluation/archives/pins?${query}`, { headers: scopedHeaders })
    ).data;
    const owned = pins.filter(
      (pin) =>
        pin.resource_version === options.expectedRetention!.resource_version &&
        options.expectedRetention!.owners.some(
          (owner) =>
            owner.owner_kind === pin.owner_kind &&
            owner.owner_id === pin.owner_id,
        ),
    );
    if (!owned.length)
      throw new Error(
        "file retention has no currently readable owned published pin",
      );
    if (!process.env.ACCEPTANCE_EVIDENCE_DIR)
      throw new Error("retention evidence directory required");
    const directory = resolve(
      process.env.ACCEPTANCE_EVIDENCE_DIR,
      "retained-resources",
    );
    mkdirSync(directory, { recursive: true });
    writeFileSync(
      resolve(directory, `${randomUUID()}.json`),
      JSON.stringify(
        {
          status: "retained-pinned",
          resource: "file",
          resource_id: resourceId,
          resource_version: options.expectedRetention.resource_version,
          pins: owned,
          physically_deleted: false,
        },
        null,
        2,
      ),
    );
    return;
  }
  if (resource.startsWith("evaluation-")) {
    type State = {
      id?: string;
      revision: number;
      status?: string;
      cleanup_status?: string;
    };
    let current = await appApi<State>(page, resourcePath, {
      headers: scopedHeaders,
      expectStatus: [200, 404],
    });
    if (current.status === 404) {
      if (options.expectedUnknownRetention)
        throw new Error("expected unknown retention batch readback is missing");
      if (resource === "evaluation-batch" && options.retainedAccounting) {
        const { collectAccountingRetention } =
          await import("./accounting-retention");
        collectAccountingRetention(resourceId);
      }
      return;
    }
    if (resource === "evaluation-batch") {
      const terminal = new Set([
        "completed",
        "completed_with_errors",
        "cancelled",
        "failed",
        "rejected",
      ]);
      if (!terminal.has(current.data.status ?? "")) {
        await appApi(page, `${resourcePath}/commands/cancel`, {
          method: "POST",
          body: { request_id: randomUUID() },
          headers: scopedHeaders,
          expectStatus: [202],
        });
      }
      const deadline = Date.now() + 60_000;
      while (
        !terminal.has(current.data.status ?? "") ||
        current.data.cleanup_status !== "clean"
      ) {
        if (Date.now() > deadline)
          throw new Error("owned evaluation batch cleanup remains pending");
        await page.waitForTimeout(250);
        current = await appApi<State>(page, resourcePath, {
          headers: scopedHeaders,
        });
      }
    }
    if (resource === "evaluation-batch" && options.retainedAccounting) {
      const { collectAccountingRetention } =
        await import("./accounting-retention");
      collectAccountingRetention(resourceId);
    }
    if (resource === "evaluation-recording") {
      const deadline = Date.now() + 60_000;
      while (["queued", "running"].includes(current.data.status ?? "")) {
        if (Date.now() >= deadline)
          throw new Error(
            "owned evaluation recording generation remains pending",
          );
        await page.waitForTimeout(250);
        current = await appApi<State>(page, resourcePath, {
          headers: scopedHeaders,
        });
      }
    }
    let protectedBefore:
      | ReturnType<
          typeof import("./protected-retention").collectProtectedRetention
        >
      | undefined;
    if (options.expectedUnknownRetention) {
      if (
        current.data.id !== resourceId ||
        !Number.isInteger(current.data.revision) ||
        current.data.revision! < 1
      )
        throw new Error("expected unknown retention batch identity mismatch");
      const { collectProtectedRetention } =
        await import("./protected-retention");
      protectedBefore = collectProtectedRetention(resourceId, "before");
    }
    const archived = await appApi(page, "/evaluation/archives", {
      method: "POST",
      headers: scopedHeaders,
      expectStatus: resource === "evaluation-batch" ? [200, 400] : [200],
      body: {
        kind: resource.slice("evaluation-".length),
        resource_id: resourceId,
        expected_revision: current.data.revision,
        request_id: randomUUID(),
      },
    });
    if (archived.status === 400) {
      const observed = await appApi<State>(page, resourcePath, {
        headers: scopedHeaders,
      });
      if (
        observed.data.id !== resourceId ||
        observed.data.revision !== current.data.revision
      )
        throw new Error("rejected batch archive readback identity mismatch");
      if (options.expectedUnknownRetention) {
        if (
          observed.data.status !== current.data.status ||
          observed.data.cleanup_status !== "clean"
        )
          throw new Error("protected batch archive readback state mismatch");
        const { collectProtectedRetention, recordProtectedRetention } =
          await import("./protected-retention");
        const proof = collectProtectedRetention(
          resourceId,
          "after",
          protectedBefore,
        );
        recordProtectedRetention(resourceId, proof, {
          revision: observed.data.revision!,
          status: observed.data.status!,
          cleanup_status: "clean",
        });
        return;
      }
      recordBlockedCleanup(resource, resourceId, options.workspaceId, {
        operation: "archive",
        response_status: archived.status,
        response_error_key: archived.errorKey,
        observed_revision: observed.data.revision,
        observed_status: observed.data.status,
        observed_cleanup_status: observed.data.cleanup_status,
      });
      throw new Error(
        "owned evaluation batch archive rejected; obligation pending",
      );
    }
    if (options.expectedUnknownRetention)
      throw new Error("expected protected batch archive was not rejected");
    return;
  }
  if (resource === "session") {
    const current = await appApi<SessionState | null>(page, resourcePath, {
      headers: scopedHeaders,
      expectStatus: [200, 404],
    });
    if (current.status === 404) return;
    if (
      current.data?.status === "running" ||
      current.data?.status === "waiting"
    ) {
      await appApi(page, `${resourcePath}/stop`, {
        method: "POST",
        body: {},
        headers: scopedHeaders,
        expectStatus: [200, 409],
      });
      const deadline = Date.now() + 30_000;
      while (Date.now() < deadline) {
        const observed = await appApi<SessionState | null>(page, resourcePath, {
          headers: scopedHeaders,
          expectStatus: [200, 404],
        });
        if (
          observed.status === 404 ||
          observed.data?.status === "completed" ||
          observed.data?.status === "cancelled" ||
          observed.data?.status === "failed"
        ) {
          break;
        }
        await page.waitForTimeout(250);
      }
    }
    await appApi(page, `${resourcePath}/delete`, {
      method: "POST",
      headers: scopedHeaders,
      expectStatus: [200, 404],
    });
    return;
  }
  if (resource === "patrol-pack") {
    const current = await appApi<PatrolPackState | null>(page, resourcePath, {
      headers: scopedHeaders,
      expectStatus: [200, 404],
    });
    if (current.status === 404) return;
    if (current.data?.status === "active") {
      await appApi(page, `${resourcePath}/pause`, {
        method: "POST",
        body: {},
        headers: scopedHeaders,
        expectStatus: [200, 404],
      });
    }
  }
  const removed = await appApi(page, resourcePath, {
    method: "DELETE",
    headers: scopedHeaders,
    expectStatus: resource === "team" ? [200, 404, 409, 500] : [200, 404],
  });
  if (resource === "team" && [409, 500].includes(removed.status)) {
    const observed = await appApi<{ id: string }>(page, resourcePath, {
      headers: scopedHeaders,
    });
    if (observed.data.id !== resourceId)
      throw new Error("rejected team delete readback identity mismatch");
    recordBlockedCleanup(resource, resourceId, options.workspaceId, {
      operation: "delete",
      response_status: removed.status,
      response_error_key: removed.errorKey,
    });
    throw new Error("owned team delete rejected; obligation pending");
  }
}
