import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { expect, test } from "@playwright/test";

import {
  readBootstrapState,
  type BootstrapState,
  writeBootstrapState,
} from "../support/bootstrap-state";

test("round-trips the observed inference capability transition", () => {
  const directory = mkdtempSync(join(tmpdir(), "opencitadel-bootstrap-state-"));
  const previousEvidenceDir = process.env.ACCEPTANCE_EVIDENCE_DIR;
  const previousRunId = process.env.ACCEPTANCE_RUN_ID;
  process.env.ACCEPTANCE_EVIDENCE_DIR = directory;
  process.env.ACCEPTANCE_RUN_ID = "acceptance-bootstrap-state";
  const unconfigured = {
    state: "not_configured" as const,
    model_id: null,
    reason_key: "inference.errors.bindingNotConfigured",
  };
  const available = {
    state: "available" as const,
    model_id: "model-1",
    reason_key: null,
  };
  const state: BootstrapState = {
    schema_version: 1,
    run_id: "acceptance-bootstrap-state",
    model_ids: { chat: "model-1", embedding: "model-2" },
    binding_purposes: ["chat", "embedding", "rerank"],
    capability_transition: {
      before: {
        chat: unconfigured,
        embeddings: unconfigured,
        rerank: unconfigured,
      },
      after: {
        chat: available,
        embeddings: { ...available, model_id: "model-2" },
        rerank: available,
      },
    },
    cleanup_completed: false,
  };

  try {
    writeBootstrapState(state);
    expect(readBootstrapState()).toEqual(state);
    expect(
      JSON.parse(readFileSync(join(directory, "bootstrap.json"), "utf8")),
    ).toEqual(state);
  } finally {
    if (previousEvidenceDir === undefined) {
      delete process.env.ACCEPTANCE_EVIDENCE_DIR;
    } else {
      process.env.ACCEPTANCE_EVIDENCE_DIR = previousEvidenceDir;
    }
    if (previousRunId === undefined) {
      delete process.env.ACCEPTANCE_RUN_ID;
    } else {
      process.env.ACCEPTANCE_RUN_ID = previousRunId;
    }
    rmSync(directory, { recursive: true, force: true });
  }
});
