import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { completedOwnedRun } from "../support/owned-execution";

for (const rejectNewApproval of [false, true]) {
  test(`owned run ignores stale decided inbox rows but ${rejectNewApproval ? "surfaces a new approval 404" : "decides every new approval"}`, async () => {
    const evidence = mkdtempSync(
      join(tmpdir(), "opencitadel-owned-execution-"),
    );
    const oldRun = process.env.ACCEPTANCE_RUN_ID;
    const oldEvidence = process.env.ACCEPTANCE_EVIDENCE_DIR;
    process.env.ACCEPTANCE_RUN_ID = "owned-helper-test";
    process.env.ACCEPTANCE_EVIDENCE_DIR = evidence;
    let inboxReads = 0;
    let viewReads = 0;
    const writes: string[] = [];
    const page = {
      evaluate: async (_callback: unknown, argument?: any) => {
        if (!argument) return "";
        if (!argument.requestPath) return [];
        const path = argument.requestPath as string;
        let data: any;
        let status = 200;
        if (path === "/sessions") data = { session_id: "owned-session" };
        else if (path.startsWith("/execution-runs?"))
          data = { items: [{ run_id: "owned-run" }] };
        else if (path.startsWith("/approvals?")) {
          inboxReads++;
          data = {
            items: [
              { approval_id: "first", run_id: "owned-run" },
              ...(inboxReads > 1
                ? [{ approval_id: "second", run_id: "owned-run" }]
                : []),
              { approval_id: "foreign", run_id: "another-run" },
            ],
          };
        } else if (path.endsWith("/commands/decide")) {
          const id = path.split("/")[2];
          writes.push(id);
          if (rejectNewApproval && id === "second") status = 404;
          data = { decision: "approved" };
        } else if (path.endsWith("/view")) {
          viewReads++;
          data = { run: { status: viewReads >= 3 ? "completed" : "waiting" } };
        } else throw new Error(`unexpected helper API: ${path}`);
        return {
          status,
          payload: {
            code: status,
            msg: status === 200 ? "success" : "pending approval missing",
            data,
          },
        };
      },
    } as unknown as Page;
    try {
      const outcome = completedOwnedRun(
        page,
        "model",
        "owned multi-tool input",
      );
      if (rejectNewApproval) await expect(outcome).rejects.toThrow("got 404");
      else expect((await outcome).approvals).toEqual(["first", "second"]);
      expect(writes).toEqual(["first", "second"]);
    } finally {
      if (oldRun === undefined) delete process.env.ACCEPTANCE_RUN_ID;
      else process.env.ACCEPTANCE_RUN_ID = oldRun;
      if (oldEvidence === undefined) delete process.env.ACCEPTANCE_EVIDENCE_DIR;
      else process.env.ACCEPTANCE_EVIDENCE_DIR = oldEvidence;
      rmSync(evidence, { recursive: true, force: true });
    }
  });
}
