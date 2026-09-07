import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { expect, test } from "@playwright/test";

import {
  completeCleanupAction,
  partitionCleanupActions,
  readCleanupActions,
  registerCleanupAction,
} from "../support/cleanup-journal";

test("journals allowlisted cleanup actions and completes them durably", () => {
  const root = mkdtempSync(join(tmpdir(), "opencitadel-cleanup-journal-"));
  const environment = {
    ACCEPTANCE_EVIDENCE_DIR: root,
    ACCEPTANCE_RUN_ID: "run-a1",
  };
  try {
    registerCleanupAction(
      {
        action: "restore-runtime-policy",
        policy: "execution",
        revision_id: "revision-1",
      },
      environment,
    );
    registerCleanupAction(
      {
        action: "set-integration-enabled",
        integration: "mcp-server",
        resource_id: "collector-1",
        enabled: false,
      },
      environment,
    );
    registerCleanupAction(
      {
        action: "delete-resource",
        resource: "patrol-pack",
        resource_id: "pack-1",
        workspace_id: "team-1",
      },
      environment,
    );
    registerCleanupAction(
      {
        action: "delete-resource",
        resource: "session",
        resource_id: "session-1",
      },
      environment,
    );
    registerCleanupAction(
      {
        action: "delete-resource",
        resource: "inference-model",
        resource_id: "model-1",
      },
      environment,
    );
    registerCleanupAction(
      {
        action: "delete-resource",
        resource: "memory",
        resource_id: "memory-1",
      },
      environment,
    );

    const pending = readCleanupActions(environment);
    expect(pending.map((entry) => entry.value.action).sort()).toEqual([
      "delete-resource",
      "delete-resource",
      "delete-resource",
      "delete-resource",
      "restore-runtime-policy",
      "set-integration-enabled",
    ]);
    const phases = partitionCleanupActions(pending);
    expect(
      phases.resources.find(
        (entry) =>
          entry.value.action === "delete-resource" &&
          entry.value.resource === "patrol-pack",
      )?.value,
    ).toMatchObject({ workspace_id: "team-1" });
    expect(phases.resources.map((entry) => entry.value.action)).toEqual([
      "delete-resource",
      "delete-resource",
      "delete-resource",
      "delete-resource",
    ]);
    expect(phases.state.map((entry) => entry.value.action).sort()).toEqual([
      "restore-runtime-policy",
      "set-integration-enabled",
    ]);

    completeCleanupAction(pending[0]);
    expect(readCleanupActions(environment)).toHaveLength(5);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("failed workspace override cleanup removes its binding before its team", () => {
  const root = mkdtempSync(join(tmpdir(), "opencitadel-binding-cleanup-"));
  const environment = {
    ACCEPTANCE_EVIDENCE_DIR: root,
    ACCEPTANCE_RUN_ID: "binding-failure",
  };
  try {
    registerCleanupAction(
      {
        action: "delete-resource",
        resource: "team",
        resource_id: "team-binding",
      },
      environment,
    );
    registerCleanupAction(
      {
        action: "delete-resource",
        resource: "inference-binding",
        resource_id: "chat",
        workspace_id: "team-binding",
      },
      environment,
    );
    // An assertion may abort immediately after PUT. Both intents remain durable.
    const actions = partitionCleanupActions(readCleanupActions(environment));
    expect(actions.resources.map((entry) => entry.value)).toEqual([
      {
        action: "delete-resource",
        resource: "inference-binding",
        resource_id: "chat",
        workspace_id: "team-binding",
      },
      {
        action: "delete-resource",
        resource: "team",
        resource_id: "team-binding",
      },
    ]);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
