import { expect, test } from "@playwright/test";
import {
  mkdtempSync,
  rmSync,
  readFileSync,
  statSync,
  symlinkSync,
  realpathSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  runCleanupResources,
  runCleanupPhases,
  registerCleanupAction,
  readCleanupActions,
  completeCleanupAction,
  type CleanupEntry,
} from "../support/cleanup-journal";
import {
  saveActorRecovery,
  readActorRecovery,
  removeActorRecovery,
} from "../support/actor-recovery";

const entry = (id: string, value: CleanupEntry["value"]): CleanupEntry => ({
  schema_version: 1,
  run_id: "invocation",
  order: id,
  path: id,
  value,
});
const actor = {
  action: "disable-owned-actor" as const,
  resource_id: "user",
  workspace_id: "team",
  email: "owned@example.test",
  registration: {
    user_id: "user",
    team_id: "team",
    role: "member",
    joined_at: "2026-09-17T00:00:00Z",
  },
  recovery_id: "12345678-1234-4234-8234-123456789012",
};

test("failed private child preserves its team and actor; unrelated resources can clean", async () => {
  const actions = [
    entry("actor", actor),
    entry("team", {
      action: "delete-resource",
      resource: "team",
      resource_id: "team",
    }),
    entry("child", {
      action: "delete-resource",
      resource: "execution-export",
      resource_id: "export",
      workspace_id: "team",
      created_at: "2026-09-17T00:00:00Z",
      creator_id: "user",
    }),
    entry("other", {
      action: "delete-resource",
      resource: "file",
      resource_id: "other",
    }),
  ];
  const called: string[] = [],
    completed: string[] = [];
  const errors = await runCleanupResources(
    actions,
    async (e) => {
      called.push(e.path);
      if (e.path === "child") throw new Error("creator unavailable");
    },
    (e) => {
      completed.push(e.path);
    },
  );
  expect(called).toEqual(["child", "other"]);
  expect(completed).toEqual(["other"]);
  expect(errors).toHaveLength(3);
});

test("successful children precede actor and team regardless of journal timestamp", async () => {
  const called: string[] = [];
  const errors = await runCleanupResources(
    [
      entry("actor", actor),
      entry("team", {
        action: "delete-resource",
        resource: "team",
        resource_id: "team",
      }),
      entry("child", {
        action: "delete-resource",
        resource: "session",
        resource_id: "session",
        workspace_id: "team",
      }),
    ],
    async (e) => {
      called.push(e.path);
    },
    () => {},
  );
  expect(errors).toEqual([]);
  expect(called).toEqual(["child", "actor", "team"]);
});

test("pinned team failure does not prevent its owned actor from being disabled", async () => {
  const called: string[] = [];
  const completed: string[] = [];
  const errors = await runCleanupResources(
    [
      entry("team", {
        action: "delete-resource",
        resource: "team",
        resource_id: "team",
      }),
      entry("actor", actor),
    ],
    async (e) => {
      called.push(e.path);
      if (e.path === "team") throw new Error("resource is pinned");
    },
    (e) => completed.push(e.path),
  );
  expect(called).toEqual(["actor", "team"]);
  expect(completed).toEqual(["actor"]);
  expect(errors).toEqual([
    "cleanup journal team: resource verification failed",
  ]);
});

test("private recovery binds invocation and returned actor receipt, rejects symlink and foreign binding", () => {
  const root = mkdtempSync(
    join(realpathSync(tmpdir()), "actor-recovery-test-"),
  );
  const env = {
    ACCEPTANCE_EVIDENCE_DIR: join(root, "public"),
    ACCEPTANCE_RUN_ID: "invocation",
    ACCEPTANCE_ACTOR_RECOVERY_DIR: join(root, "private"),
  };
  try {
    const saved = saveActorRecovery(
      { ...actor, password: "Test-only-random-secret" },
      env,
    );
    expect(statSync(saved.path).mode & 0o777).toBe(0o600);
    expect(readActorRecovery(actor, env).password).toBe(
      "Test-only-random-secret",
    );
    expect(() =>
      readActorRecovery({ ...actor, resource_id: "other" }, env),
    ).toThrow(/binding/);
    expect(() =>
      readActorRecovery(actor, { ...env, ACCEPTANCE_RUN_ID: "foreign" }),
    ).toThrow(/binding/);
    const alias = join(root, "alias");
    symlinkSync(join(root, "private"), alias);
    expect(() =>
      readActorRecovery(actor, {
        ...env,
        ACCEPTANCE_ACTOR_RECOVERY_DIR: alias,
      }),
    ).toThrow(/symlink/);
    expect(readFileSync(saved.path, "utf8")).not.toContain("access_token");
    removeActorRecovery(actor, env);
    expect(() => readActorRecovery(actor, env)).toThrow();
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("recovery cannot be placed inside published evidence", () => {
  const root = mkdtempSync(
    join(realpathSync(tmpdir()), "actor-recovery-boundary-"),
  );
  try {
    expect(() =>
      saveActorRecovery(
        { ...actor, password: "secret" },
        {
          ACCEPTANCE_EVIDENCE_DIR: root,
          ACCEPTANCE_RUN_ID: "invocation",
          ACCEPTANCE_ACTOR_RECOVERY_DIR: join(root, "private"),
        },
      ),
    ).toThrow(/published/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("redacted export retention requires creator, exact request/source and completed prior bytes", async () => {
  const { verifyCreatorExportRetention } =
    await import("../support/creator-export");
  const proof = {
    run_id: "invocation",
    caller_id: "user",
    workspace_id: "team",
    export_id: "export",
    request_id: "request",
    source_kind: "comparison" as const,
    comparison_id: "comparison",
    revision: 1,
    created_at: "2026-09-17T00:00:00Z",
    expires_at: "2026-09-18T00:00:00Z",
    ready: true,
    download: {
      status: 200,
      bytes: 12,
      sha256: "a".repeat(64),
      complete: true,
    },
  };
  const binding = {
    run_id: "invocation",
    caller_id: "user",
    workspace_id: "team",
    export_id: "export",
    request_id: "request",
    comparison_id: "comparison",
    revision: 1,
    created_at: proof.created_at,
  };
  expect(
    verifyCreatorExportRetention(
      binding,
      "user",
      { id: "export", status: "expired" },
      proof,
    ),
  ).toMatchObject({ expires_at: proof.expires_at, physically_deleted: false });
  expect(() =>
    verifyCreatorExportRetention(
      binding,
      "owner",
      { id: "export", status: "expired" },
      proof,
    ),
  ).toThrow(/creator/);
  expect(() =>
    verifyCreatorExportRetention(
      binding,
      "user",
      { id: "export", status: "expired" },
      { ...proof, request_id: "foreign" },
    ),
  ).toThrow(/binding/);
  expect(() =>
    verifyCreatorExportRetention(
      binding,
      "user",
      { id: "export", status: "expired" },
      { ...proof, download: { ...proof.download, complete: false } },
    ),
  ).toThrow(/download/);
  expect(() =>
    verifyCreatorExportRetention(
      binding,
      "user",
      { id: "export", status: "queued" },
      proof,
    ),
  ).toThrow(/pending/);
  expect(() =>
    verifyCreatorExportRetention(
      binding,
      "user",
      { id: "export", status: "ready" },
      proof,
    ),
  ).toThrow(/timestamp/);
});

test("actor read verification refuses bootstrap identity and mismatched creation receipt", async () => {
  const { findOwnedActor } = await import("../support/owned-actor");
  const page = (self: string, created = "2026-09-17T00:00:00Z") =>
    ({
      evaluate: async (_fn: unknown, args: any) => ({
        status: 200,
        payload: {
          code: 200,
          msg: "OK",
          data:
            args.requestPath === "/auth/me"
              ? { id: self, email: "operator@example.test" }
              : {
                  users: [
                    {
                      id: "user",
                      email: actor.email,
                      created_at: created,
                      status: "active",
                    },
                  ],
                  total: 1,
                },
        },
      }),
    }) as any;
  await expect(findOwnedActor(page("user"), actor)).rejects.toThrow(/binding/);
  await expect(
    findOwnedActor(page("operator", "invalid"), actor),
  ).rejects.toThrow(/receipt/);
  await expect(
    findOwnedActor(page("operator", "2025-01-01T00:00:00Z"), actor),
  ).rejects.toThrow(/receipt/);
  await expect(findOwnedActor(page("operator"), actor)).resolves.toMatchObject({
    id: "user",
  });
});

test("already-disabled exact actor replay never increments tokens or recreates credentials", async () => {
  const { disableOwnedActor } = await import("../support/owned-actor");
  const root = mkdtempSync(
    join(realpathSync(tmpdir()), "disabled-actor-replay-"),
  );
  const before = { ...process.env };
  const paths: string[] = [];
  try {
    process.env.ACCEPTANCE_EVIDENCE_DIR = join(root, "public");
    process.env.ACCEPTANCE_RUN_ID = "invocation";
    process.env.ACCEPTANCE_ACTOR_RECOVERY_DIR = join(root, "private");
    const page = {
      evaluate: async (_fn: unknown, args: any) => {
        paths.push(args.requestPath);
        expect(args.requestInit.method).toBe("GET");
        return {
          status: 200,
          payload: {
            code: 200,
            msg: "OK",
            data:
              args.requestPath === "/auth/me"
                ? { id: "operator", email: "operator@example.test" }
                : {
                    users: [
                      {
                        id: "user",
                        email: actor.email,
                        created_at: actor.registration.joined_at,
                        status: "disabled",
                        token_version: 7,
                      },
                    ],
                    total: 1,
                  },
          },
        };
      },
    } as any;
    await disableOwnedActor(page, actor);
    expect(paths).toEqual(["/auth/me", "/admin/users?limit=200&offset=0"]);
  } finally {
    process.env = before;
    rmSync(root, { recursive: true, force: true });
  }
});

test("restoration token persists across fresh reads and explicit disposal preserves credentials", async () => {
  const { saveRestorationToken } = await import("../support/actor-recovery");
  const root = mkdtempSync(join(realpathSync(tmpdir()), "actor-token-replay-"));
  const env = {
    ACCEPTANCE_EVIDENCE_DIR: join(root, "public"),
    ACCEPTANCE_RUN_ID: "invocation",
    ACCEPTANCE_ACTOR_RECOVERY_DIR: join(root, "private"),
  };
  try {
    saveActorRecovery({ ...actor, password: "owned-test-secret" }, env);
    saveRestorationToken(actor, "t".repeat(40), env);
    expect(readActorRecovery(actor, env).restore_token).toBe("t".repeat(40));
    saveRestorationToken(actor, undefined, env);
    expect(readActorRecovery(actor, env).restore_token).toBeUndefined();
    expect(readActorRecovery(actor, env).password).toBe("owned-test-secret");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

for (const fails of [true, false]) {
  test(`actual journal phases keep actor recovery behind child barrier: failure=${fails}`, async () => {
    const root = mkdtempSync(join(realpathSync(tmpdir()), "actor-phases-"));
    const env = {
      ACCEPTANCE_EVIDENCE_DIR: join(root, "public"),
      ACCEPTANCE_RUN_ID: "invocation",
      ACCEPTANCE_ACTOR_RECOVERY_DIR: join(root, "private"),
    };
    try {
      saveActorRecovery({ ...actor, password: "test-recovery" }, env);
      registerCleanupAction(actor, env);
      registerCleanupAction(
        {
          action: "delete-resource",
          resource: "execution-export",
          resource_id: "export",
          workspace_id: "team",
          creator_id: "user",
          created_at: "2026-09-17T00:00:00Z",
        },
        env,
      );
      registerCleanupAction(
        {
          action: "restore-runtime-policy",
          policy: "execution",
          revision_id: "old",
        },
        env,
      );
      const calls: string[] = [];
      const errors = await runCleanupPhases(
        readCleanupActions(env),
        async (entry) => {
          calls.push(entry.value.action);
          if (entry.value.action === "delete-resource" && fails)
            throw new Error("creator export pending");
          if (entry.value.action === "disable-owned-actor")
            removeActorRecovery(actor, env);
        },
        completeCleanupAction,
        async () => {
          calls.push("bootstrap");
        },
      );
      expect(
        calls.filter((value) => value === "disable-owned-actor"),
      ).toHaveLength(fails ? 0 : 1);
      expect(calls.slice(-2)).toEqual(["bootstrap", "restore-runtime-policy"]);
      const pending = readCleanupActions(env);
      if (fails) {
        expect(errors).toHaveLength(2);
        expect(pending.map((entry) => entry.value.action).sort()).toEqual([
          "delete-resource",
          "disable-owned-actor",
        ]);
        expect(readActorRecovery(actor, env).password).toBe("test-recovery");
      } else {
        expect(errors).toEqual([]);
        expect(pending).toEqual([]);
        expect(() => readActorRecovery(actor, env)).toThrow();
      }
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });
}
