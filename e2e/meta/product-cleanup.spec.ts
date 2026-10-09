import { expect, test, type Page } from "@playwright/test";

import { cleanupProductResource } from "../support/product-cleanup";
import {
  readProtectedRetentionProof,
  recordProtectedRetention,
} from "../support/protected-retention";
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

test("protected retention validates current owner, both raw hashes, immutable obligations and preserves the open obligation", async ({}, testInfo) => {
  const root = testInfo.outputPath("protected");
  mkdirSync(root, { recursive: true });
  const batchId = "12345678-1234-1234-1234-123456789abc";
  const owner = "operator";
  const environment = {
    ACCEPTANCE_EVIDENCE_DIR: root,
    ACCEPTANCE_RUN_ID: "current",
    ACCEPTANCE_PROJECT_ID: "owned",
    ACCEPTANCE_STRICT_INVOCATION_ID: "current-invocation",
  };
  const binding = {
    schema_version: 1,
    run_id: "current",
    project: "owned",
    invocation_id: "current-invocation",
    revision: "code",
    kernel_image: "immutable-image",
  };
  const work = {
    intent_id: "owned-intent",
    scope_key: "user:operator",
    checked_at: "2026-10-08T08:20:00+00:00",
  };
  const beforeRaw = {
    batch_id: batchId,
    held: "private-before-only",
    tables: { judge_work: [work] },
  };
  const afterRaw = {
    batch_id: batchId,
    held: "private-after-only",
    tables: {
      judge_work: [{ ...work, checked_at: "2026-10-08T08:20:01+00:00" }],
    },
  };
  const sha = (value: unknown) =>
    createHash("sha256").update(JSON.stringify(value)).digest("hex");
  const verification = {
    resource_id: batchId,
    batch_scope: "user:operator",
    owner_id: owner,
    physically_deleted: false,
    archived: false,
    settled: false,
    future_obligation: "open",
    immutable_unknown_obligations: true,
    local_active_sends: 0,
    environment_status: "clean",
  };
  const initial = {
    schema_version: 1,
    binding,
    status: "protected-retention-observed",
    raw_sha256: sha(beforeRaw),
    verification,
  };
  const final = {
    schema_version: 1,
    binding,
    status: "verified-protected-retention",
    before_sha256: sha(beforeRaw),
    after_sha256: sha(afterRaw),
    evidence: { before: beforeRaw, after: afterRaw },
    comparison_policy:
      "typed-source-equality-with-monotonic-judge-work-checked-at",
    observed_clock_transitions: [
      {
        table: "judge_work",
        field: "checked_at",
        intent_id: work.intent_id,
        scope_key: work.scope_key,
        before: work.checked_at,
        after: afterRaw.tables.judge_work[0].checked_at,
      },
    ],
    verification,
  };
  const prefix = `protected-retention-${batchId}`;
  const write = (name: string, value: unknown) =>
    writeFileSync(resolve(root, name), JSON.stringify(value), { mode: 0o600 });
  const restore = () => {
    write("strict-binding.json", binding);
    write("strict-bootstrap.json", {
      run_id: "current",
      project: "owned",
      operator_id: owner,
    });
    write(`${prefix}.before.raw.json`, beforeRaw);
    write(`${prefix}.after.raw.json`, afterRaw);
    write(`${prefix}.before.json`, initial);
    write(`${prefix}.json`, final);
  };
  restore();
  const before = readProtectedRetentionProof(
    batchId,
    "before",
    undefined,
    environment,
  );
  const proof = readProtectedRetentionProof(
    batchId,
    "after",
    before,
    environment,
  );
  expect(proof.after_sha256).toBe(sha(afterRaw));
  for (const invalid of [
    { ...final, binding: { ...binding, invocation_id: "historical" } },
    { ...final, before_sha256: "wrong" },
    { ...final, after_sha256: "wrong" },
    { ...final, evidence: { before: beforeRaw, after: {} } },
    { ...final, comparison_policy: "ignore-any-field" },
    { ...final, observed_clock_transitions: [] },
    ...[
      { batch_scope: "team:foreign" },
      { owner_id: "foreign" },
      { immutable_unknown_obligations: false },
      { settled: true },
      { archived: true },
      { physically_deleted: true },
      { local_active_sends: 1 },
      { environment_status: "unsafe" },
      { future_obligation: "closed" },
    ].map((change) => ({
      ...final,
      verification: { ...verification, ...change },
    })),
  ]) {
    write(`${prefix}.json`, invalid);
    expect(() =>
      readProtectedRetentionProof(batchId, "after", before, environment),
    ).toThrow(/unverified/);
  }
  restore();
  for (const [oldClock, newClock] of [
    ["2026-10-08T08:20:00.123456+00:00", "2026-10-08T08:20:00.123455+00:00"],
    ["2026-02-30T08:20:00+00:00", "2026-03-02T08:20:01+00:00"],
  ]) {
    const rawBefore = {
      ...beforeRaw,
      tables: { judge_work: [{ ...work, checked_at: oldClock }] },
    };
    const rawAfter = {
      ...afterRaw,
      tables: { judge_work: [{ ...work, checked_at: newClock }] },
    };
    write(`${prefix}.before.raw.json`, rawBefore);
    write(`${prefix}.after.raw.json`, rawAfter);
    write(`${prefix}.before.json`, { ...initial, raw_sha256: sha(rawBefore) });
    write(`${prefix}.json`, {
      ...final,
      before_sha256: sha(rawBefore),
      after_sha256: sha(rawAfter),
      evidence: { before: rawBefore, after: rawAfter },
      observed_clock_transitions: [
        {
          ...final.observed_clock_transitions[0],
          before: oldClock,
          after: newClock,
        },
      ],
    });
    const boundBefore = readProtectedRetentionProof(
      batchId,
      "before",
      undefined,
      environment,
    );
    expect(() =>
      readProtectedRetentionProof(batchId, "after", boundBefore, environment),
    ).toThrow(/unverified/);
  }
  restore();
  for (const invalidEnvironment of [
    { ...environment, ACCEPTANCE_RUN_ID: "foreign" },
    { ...environment, ACCEPTANCE_PROJECT_ID: "foreign" },
    { ...environment, ACCEPTANCE_STRICT_INVOCATION_ID: "foreign" },
    { ...environment, ACCEPTANCE_STRICT_INVOCATION_ID: undefined },
  ])
    expect(() =>
      readProtectedRetentionProof(
        batchId,
        "before",
        undefined,
        invalidEnvironment,
      ),
    ).toThrow(/binding mismatch/);
  write(`${prefix}.before.json`, {
    ...initial,
    verification: { ...verification, settled: true },
  });
  expect(() =>
    readProtectedRetentionProof(batchId, "before", undefined, environment),
  ).toThrow(/before proof/);
  restore();
  write(`${prefix}.before.raw.json`, { ...beforeRaw, tampered: true });
  expect(() =>
    readProtectedRetentionProof(batchId, "after", before, environment),
  ).toThrow(/before proof/);
  restore();
  write(`${prefix}.after.raw.json`, { ...afterRaw, tampered: true });
  expect(() =>
    readProtectedRetentionProof(batchId, "after", before, environment),
  ).toThrow(/after proof/);
  restore();
  expect(() =>
    readProtectedRetentionProof(batchId, "after", undefined, environment),
  ).toThrow(/after proof/);
  const currentEnvironment = {
    ...environment,
    // Only the external host execution is replaced; receipt, raw SHA and
    // current invocation validation remain real filesystem reads in this meta test.
    ACCEPTANCE_HOST_PYTHON: "/usr/bin/true",
  };
  const previous = Object.fromEntries(
    Object.keys(currentEnvironment).map((key) => [key, process.env[key]]),
  );
  Object.assign(process.env, currentEnvironment);
  try {
    await test.step("a valid before proof never permits an unexpected successful archive", async () => {
      const requests: string[] = [];
      const page = {
        evaluate: async (_fn: unknown, value: any) => {
          requests.push(`${value.requestInit.method}:${value.requestPath}`);
          return {
            status: 200,
            payload: {
              code: 200,
              msg: "success",
              data:
                value.requestInit.method === "GET"
                  ? {
                      id: batchId,
                      revision: 3,
                      status: "completed_with_errors",
                      cleanup_status: "clean",
                    }
                  : { state: "archived", retained: true },
            },
          };
        },
      } as unknown as Page;
      await expect(
        cleanupProductResource(page, "evaluation-batch", batchId, {
          expectedUnknownRetention: true,
        }),
      ).rejects.toThrow("expected protected batch archive was not rejected");
      expect(requests).toEqual([
        `GET:/evaluation/batches/${batchId}`,
        "POST:/evaluation/archives",
      ]);
    });
    recordProtectedRetention(batchId, proof, {
      revision: 3,
      status: "completed_with_errors",
      cleanup_status: "clean",
    });
    const retainedPath = resolve(root, "retained-resources", `${prefix}.json`);
    const retained = JSON.parse(readFileSync(retainedPath, "utf8"));
    expect(retained).toMatchObject({
      status: "verified-protected-retention",
      physically_deleted: false,
      archived: false,
      settled: false,
      future_obligation: "open",
      cleanup_obligation: "protected-retention-accounting-open",
      disposition_verified: true,
    });
    expect(Object.keys(retained.proof_references)).toHaveLength(4);
    expect(readFileSync(retainedPath, "utf8")).not.toContain("private-");
    expect(statSync(retainedPath).mode & 0o777).toBe(0o600);
  } finally {
    for (const [key, value] of Object.entries(previous)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
});

test("expected unknown retention requires current runner proof before an archive attempt", async () => {
  const previous = process.env.ACCEPTANCE_HOST_PYTHON;
  delete process.env.ACCEPTANCE_HOST_PYTHON;
  const requests: string[] = [];
  const page = {
    evaluate: async (_fn: unknown, value: any) => {
      requests.push(value.requestInit.method);
      return {
        status: 200,
        payload: {
          code: 200,
          msg: "success",
          data: {
            id: "batch-1",
            revision: 3,
            status: "completed_with_errors",
            cleanup_status: "clean",
          },
        },
      };
    },
  } as unknown as Page;
  try {
    await expect(
      cleanupProductResource(page, "evaluation-batch", "batch-1", {
        expectedUnknownRetention: true,
      } as any),
    ).rejects.toThrow("owning runner");
    expect(requests).toEqual(["GET"]);
  } finally {
    if (previous !== undefined) process.env.ACCEPTANCE_HOST_PYTHON = previous;
  }
});

test("pauses an active Patrol Pack before deleting it", async () => {
  const requests: Array<{
    requestPath: string;
    requestInit: {
      method: string;
      headers?: Record<string, string>;
    };
  }> = [];
  const page = {
    evaluate: async (
      _callback: unknown,
      value: {
        requestPath: string;
        requestInit: {
          method: string;
          headers?: Record<string, string>;
        };
      },
    ) => {
      requests.push(value);
      const data =
        value.requestInit.method === "GET" ? { status: "active" } : {};
      return {
        status: 200,
        payload: { code: 200, msg: "success", data },
      };
    },
  } as unknown as Page;

  await cleanupProductResource(page, "patrol-pack", "pack-1", {
    workspaceId: "team-1",
  });

  expect(
    requests.map(({ requestPath, requestInit }) => [
      requestInit.method,
      requestPath,
    ]),
  ).toEqual([
    ["GET", "/patrol-packs/pack-1"],
    ["POST", "/patrol-packs/pack-1/pause"],
    ["DELETE", "/patrol-packs/pack-1"],
  ]);
  expect(requests.map(({ requestInit }) => requestInit.headers)).toEqual([
    { "X-Workspace-Id": "team-1" },
    { "X-Workspace-Id": "team-1" },
    { "X-Workspace-Id": "team-1" },
  ]);
});

test("workspace binding cleanup stays team-scoped when the browser has reset to personal", async () => {
  let request:
    | {
        requestPath: string;
        requestInit: { method: string; headers?: Record<string, string> };
      }
    | undefined;
  const page = {
    evaluate: async (_callback: unknown, value: typeof request) => {
      request = value;
      return {
        status: 404,
        payload: { code: 404, msg: "already removed", data: null },
      };
    },
  } as unknown as Page;
  await cleanupProductResource(page, "inference-binding", "chat", {
    workspaceId: "team-binding",
  });
  expect(request).toEqual({
    requestPath: "/inference/bindings/chat?binding_scope=workspace",
    requestInit: {
      method: "DELETE",
      body: undefined,
      headers: { "X-Workspace-Id": "team-binding" },
    },
  });
  await expect(
    cleanupProductResource(page, "inference-binding", "chat"),
  ).rejects.toThrow("explicit workspace ID");
});

test("evaluation cleanup archives exact revision without deleting retained evidence", async () => {
  const requests: any[] = [];
  const page = {
    evaluate: async (_callback: unknown, value: any) => {
      requests.push(value);
      return {
        status: 200,
        payload: {
          code: 200,
          msg: "success",
          data:
            value.requestInit.method === "GET"
              ? { id: "dataset-1", revision: 3 }
              : { state: "archived", retained: true },
        },
      };
    },
  } as unknown as Page;
  await cleanupProductResource(page, "evaluation-dataset", "dataset-1");
  expect(requests.map((value) => value.requestInit.method)).toEqual([
    "GET",
    "POST",
  ]);
  expect(requests[1].requestPath).toBe("/evaluation/archives");
  const body = requests[1].requestInit.body;
  expect(body).toMatchObject({
    kind: "dataset",
    resource_id: "dataset-1",
    expected_revision: 3,
  });
});

test("rejected batch archive stays pending and records public readback without claiming archive", async ({}, testInfo) => {
  const { readFileSync, readdirSync } = await import("node:fs");
  const { resolve } = await import("node:path");
  const previous = process.env.ACCEPTANCE_EVIDENCE_DIR;
  process.env.ACCEPTANCE_EVIDENCE_DIR = testInfo.outputPath("blocked-batch");
  const requests: string[] = [];
  const page = {
    evaluate: async (_fn: unknown, request: any) => {
      requests.push(`${request.requestInit.method}:${request.requestPath}`);
      return request.requestInit.method === "POST"
        ? {
            status: 400,
            payload: {
              code: 400,
              msg: "invalid_argument",
              data: { code: "invalid_argument" },
            },
          }
        : {
            status: 200,
            payload: {
              code: 200,
              msg: "success",
              data: {
                id: "batch-1",
                revision: 3,
                status: "completed_with_errors",
                cleanup_status: "clean",
              },
            },
          };
    },
  } as unknown as Page;
  try {
    await expect(
      cleanupProductResource(page, "evaluation-batch", "batch-1"),
    ).rejects.toThrow("obligation pending");
    expect(requests).toEqual([
      "GET:/evaluation/batches/batch-1",
      "POST:/evaluation/archives",
      "GET:/evaluation/batches/batch-1",
    ]);
    const directory = resolve(
      process.env.ACCEPTANCE_EVIDENCE_DIR!,
      "retained-resources",
    );
    const evidence = JSON.parse(
      readFileSync(resolve(directory, readdirSync(directory)[0]), "utf8"),
    );
    expect(evidence).toMatchObject({
      status: "cleanup-blocked",
      resource: "evaluation-batch",
      operation: "archive",
      response_status: 400,
      observed_revision: 3,
      physically_deleted: false,
      cleanup_obligation: "pending",
      disposition_verified: false,
    });
  } finally {
    if (previous === undefined) delete process.env.ACCEPTANCE_EVIDENCE_DIR;
    else process.env.ACCEPTANCE_EVIDENCE_DIR = previous;
  }
});

for (const status of [409, 500]) {
  test(`rejected team delete ${status} records readback and never completes cleanup`, async ({}, testInfo) => {
    const { readFileSync, readdirSync } = await import("node:fs");
    const { resolve } = await import("node:path");
    const previous = process.env.ACCEPTANCE_EVIDENCE_DIR;
    process.env.ACCEPTANCE_EVIDENCE_DIR = testInfo.outputPath("blocked-team");
    const page = {
      evaluate: async (_fn: unknown, request: any) =>
        request.requestInit.method === "DELETE"
          ? {
              status,
              payload: {
                code: status,
                msg: "server error",
                error_key:
                  status === 409 ? "resource_pinned" : "errors.serverError",
                data:
                  status === 409
                    ? { resource_kind: "team", resource_id: "team-1" }
                    : {},
              },
            }
          : {
              status: 200,
              payload: { code: 200, msg: "success", data: { id: "team-1" } },
            },
    } as unknown as Page;
    try {
      await expect(
        cleanupProductResource(page, "team", "team-1"),
      ).rejects.toThrow("obligation pending");
      const directory = resolve(
        process.env.ACCEPTANCE_EVIDENCE_DIR!,
        "retained-resources",
      );
      const evidence = JSON.parse(
        readFileSync(resolve(directory, readdirSync(directory)[0]), "utf8"),
      );
      expect(evidence).toMatchObject({
        status: "cleanup-blocked",
        resource: "team",
        operation: "delete",
        response_status: status,
        response_error_key:
          status === 409 ? "resource_pinned" : "errors.serverError",
        physically_deleted: false,
        cleanup_obligation: "pending",
        disposition_verified: false,
      });
    } finally {
      if (previous === undefined) delete process.env.ACCEPTANCE_EVIDENCE_DIR;
      else process.env.ACCEPTANCE_EVIDENCE_DIR = previous;
    }
  });
}

test("retention accepts only exact actual owned file pins and writes explicit evidence", async ({}, testInfo) => {
  const { readFileSync, readdirSync } = await import("node:fs");
  const { resolve } = await import("node:path");
  const previous = process.env.ACCEPTANCE_EVIDENCE_DIR;
  process.env.ACCEPTANCE_EVIDENCE_DIR = testInfo.outputPath("evidence");
  const owned = {
    owner_kind: "dataset_version",
    owner_id: "owned-version",
    resource_version: "digest",
  };
  const page = {
    evaluate: async (_fn: unknown, request: any) =>
      request.requestInit.method === "DELETE"
        ? {
            status: 409,
            payload: {
              code: 409,
              msg: "pinned",
              error_key: "resource_pinned",
              data: { resource_kind: "file", resource_id: "owned-file" },
            },
          }
        : {
            status: 200,
            payload: { code: 200, msg: "success", data: [owned] },
          },
  } as unknown as Page;
  try {
    await cleanupProductResource(page, "file", "owned-file", {
      expectedRetention: { resource_version: "digest", owners: [owned] },
    });
    const directory = resolve(
      process.env.ACCEPTANCE_EVIDENCE_DIR!,
      "retained-resources",
    );
    const evidence = JSON.parse(
      readFileSync(resolve(directory, readdirSync(directory)[0]), "utf8"),
    );
    expect(evidence).toMatchObject({
      status: "retained-pinned",
      resource_id: "owned-file",
      physically_deleted: false,
      pins: [owned],
    });
    await expect(
      cleanupProductResource(page, "file", "owned-file", {
        expectedRetention: {
          resource_version: "digest",
          owners: [{ owner_kind: "dataset_version", owner_id: "foreign" }],
        },
      }),
    ).rejects.toThrow("no currently readable owned");
    await expect(
      cleanupProductResource(page, "file", "foreign-file", {
        expectedRetention: { resource_version: "digest", owners: [owned] },
      }),
    ).rejects.toThrow("not verified retention");
  } finally {
    if (previous === undefined) delete process.env.ACCEPTANCE_EVIDENCE_DIR;
    else process.env.ACCEPTANCE_EVIDENCE_DIR = previous;
  }
});

test("recording cleanup waits for generation before archiving its final revision", async () => {
  const requests: any[] = [];
  let reads = 0;
  const page = {
    waitForTimeout: async () => {},
    evaluate: async (_fn: unknown, request: any) => {
      requests.push(request);
      if (request.requestInit.method === "GET")
        return {
          status: 200,
          payload: {
            code: 200,
            msg: "success",
            data:
              ++reads === 1
                ? { status: "running", revision: 1 }
                : { status: "ready", revision: 2 },
          },
        };
      return { status: 200, payload: { code: 200, msg: "success", data: {} } };
    },
  } as unknown as Page;
  await cleanupProductResource(page, "evaluation-recording", "owned-recording");
  expect(reads).toBe(2);
  expect(requests.at(-1).requestInit.body.expected_revision).toBe(2);
});

test("comparison cleanup verifies the exact owned revision and records retained history", async ({}, testInfo) => {
  const { readdirSync, readFileSync } = await import("node:fs");
  const { resolve } = await import("node:path");
  const previous = process.env.ACCEPTANCE_EVIDENCE_DIR;
  process.env.ACCEPTANCE_EVIDENCE_DIR = testInfo.outputPath("retention");
  const requests: any[] = [];
  const page = {
    evaluate: async (_fn: unknown, request: any) => {
      requests.push(request);
      return {
        status: 200,
        payload: {
          code: 200,
          msg: "success",
          data: { comparison_id: "owned", revision: 2 },
        },
      };
    },
  } as unknown as Page;
  try {
    for (let repeat = 0; repeat < 2; repeat++)
      await cleanupProductResource(page, "execution-comparison", "owned", {
        retainedRevision: 2,
        workspaceId: "team",
      });
    expect(requests.map((x) => [x.requestInit.method, x.requestPath])).toEqual([
      ["GET", "/execution-comparisons/owned?revision=2"],
      ["GET", "/execution-comparisons/owned?revision=2"],
    ]);
    const dir = resolve(
      process.env.ACCEPTANCE_EVIDENCE_DIR!,
      "retained-resources",
    );
    expect(readdirSync(dir)).toHaveLength(1);
    expect(
      JSON.parse(readFileSync(resolve(dir, readdirSync(dir)[0]), "utf8")),
    ).toMatchObject({
      status: "retained-history",
      resource_id: "owned",
      revision: 2,
      physically_deleted: false,
    });
    await expect(
      cleanupProductResource(page, "execution-comparison", "foreign", {
        retainedRevision: 2,
      }),
    ).rejects.toThrow("identity");
    await expect(
      cleanupProductResource(page, "execution-comparison", "owned"),
    ).rejects.toThrow("revision");
  } finally {
    if (previous === undefined) delete process.env.ACCEPTANCE_EVIDENCE_DIR;
    else process.env.ACCEPTANCE_EVIDENCE_DIR = previous;
  }
});

test("export cleanup retains terminal evidence and a durable expiry obligation, never claims object deletion", async ({}, testInfo) => {
  const { readdirSync, readFileSync } = await import("node:fs");
  const { resolve } = await import("node:path");
  const previous = process.env.ACCEPTANCE_EVIDENCE_DIR;
  process.env.ACCEPTANCE_EVIDENCE_DIR = testInfo.outputPath("export");
  const createdAt = "2026-09-17T01:00:00Z";
  let status = "ready";
  const page = {
    evaluate: async () => ({
      status: 200,
      payload: {
        code: 200,
        msg: "success",
        data: {
          id: "owned",
          status,
          created_at: createdAt,
          expires_at: "2026-09-18T01:00:00Z",
        },
      },
    }),
  } as unknown as Page;
  try {
    await cleanupProductResource(page, "execution-export", "owned", {
      createdAt,
    });
    const dir = resolve(
      process.env.ACCEPTANCE_EVIDENCE_DIR!,
      "retained-resources",
    );
    expect(
      JSON.parse(readFileSync(resolve(dir, readdirSync(dir)[0]), "utf8")),
    ).toMatchObject({
      status: "retained-until-expiry",
      physically_deleted: false,
      gc_obligation: "pending-kernel-expiry-and-object-GC",
    });
    for (status of ["queued", "running", "unknown"])
      await expect(
        cleanupProductResource(page, "execution-export", "owned", {
          createdAt,
        }),
      ).rejects.toThrow("generation remains pending");
    await expect(
      cleanupProductResource(page, "execution-export", "foreign", {
        createdAt,
      }),
    ).rejects.toThrow("identity");
  } finally {
    if (previous === undefined) delete process.env.ACCEPTANCE_EVIDENCE_DIR;
    else process.env.ACCEPTANCE_EVIDENCE_DIR = previous;
  }
});

test("accounting retention cannot bypass a missing runner proof even after an archive is no longer visible", async () => {
  const previous = process.env.ACCEPTANCE_HOST_PYTHON;
  delete process.env.ACCEPTANCE_HOST_PYTHON;
  const methods: string[] = [];
  try {
    for (const status of [200, 404]) {
      const page = {
        evaluate: async (_fn: unknown, args: any) => {
          methods.push(args.requestInit.method);
          return {
            status,
            payload: {
              code: status,
              msg: "fixture",
              data: {
                status: "completed",
                cleanup_status: "clean",
                revision: 1,
              },
            },
          };
        },
      } as unknown as Page;
      await expect(
        cleanupProductResource(page, "evaluation-batch", "owned", {
          retainedAccounting: true,
        }),
      ).rejects.toThrow("owning runner");
    }
    expect(methods).toEqual(["GET", "GET"]);
  } finally {
    if (previous !== undefined) process.env.ACCEPTANCE_HOST_PYTHON = previous;
  }
});
