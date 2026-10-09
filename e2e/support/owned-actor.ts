import { randomBytes, randomUUID } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import type { BrowserContext, Page } from "@playwright/test";
import { appApi } from "./api";
import {
  registerCleanupAction,
  type OwnedActorCleanup,
} from "./cleanup-journal";
import {
  readActorRecovery,
  removeActorRecovery,
  saveActorRecovery,
  saveRestorationToken,
} from "./actor-recovery";

export type Actor = {
  page: Page;
  context: BrowserContext;
  cleanup: OwnedActorCleanup;
};
type User = {
  id: string;
  email: string;
  status: string;
  token_version: number;
  global_role: string;
  created_at: string;
};
async function privateContext(operator: Page): Promise<BrowserContext> {
  const browser = operator.context().browser();
  if (!browser) throw new Error("owned actor needs acceptance browser");
  const context = await browser.newContext({
    baseURL: new URL(operator.url()).origin,
  });
  // Playwright Test starts tracing every context made through its browser
  // fixture. Stop before any credentials enter this private actor context.
  // Its automatic close hook also stops tracing, so start an empty recording
  // immediately before close to satisfy that hook without retaining secrets.
  await context.tracing.stop();
  const close = context.close.bind(context);
  context.close = async (...args) => {
    await context.tracing.start();
    return close(...args);
  };
  return context;
}
async function scope(
  page: Page,
  userId: string,
  workspace?: string,
): Promise<void> {
  await page.evaluate(
    ({ userId, workspace }) => {
      const scopedKey = `opencitadel-active-workspace:${encodeURIComponent(userId)}`;
      if (workspace) {
        localStorage.setItem("opencitadel-active-workspace", workspace);
        localStorage.setItem(scopedKey, workspace);
      } else {
        localStorage.removeItem("opencitadel-active-workspace");
        localStorage.removeItem(scopedKey);
      }
    },
    { userId, workspace },
  );
}
function invitationToken(url: string): string {
  const match = new URL(url).pathname.match(/^\/invitations\/([^/]+)$/);
  if (!match) throw new Error("invitation receipt has invalid path");
  return match[1];
}
export async function createOwnedActor(
  operator: Page,
  teamId: string,
): Promise<Actor> {
  const recoveryId = randomUUID();
  const email = `a05-${recoveryId}@example.test`;
  const password = randomBytes(32).toString("base64url");
  const invite = (
    await appApi<{ url: string }>(operator, `/teams/${teamId}/invitations`, {
      method: "POST",
      body: { role: "member", email },
      headers: { "X-Workspace-Id": teamId },
    })
  ).data;
  const context = await privateContext(operator);
  try {
    const page = await context.newPage();
    await page.goto("/login");
    const registration = (
      await appApi<OwnedActorCleanup["registration"]>(
        page,
        `/invitations/${invitationToken(invite.url)}/register`,
        {
          method: "POST",
          body: { email, username: `a05-${recoveryId}`, password },
        },
      )
    ).data;
    const cleanup: OwnedActorCleanup = {
      action: "disable-owned-actor",
      resource_id: registration.user_id,
      workspace_id: teamId,
      email,
      recovery_id: recoveryId,
      registration,
    };
    registerCleanupAction(cleanup); // Returned ID is journaled before any further API read.
    saveActorRecovery({ ...cleanup, password });
    const me = (await appApi<User>(page, "/auth/me")).data;
    if (
      me.id !== registration.user_id ||
      me.email !== email ||
      me.status !== "active" ||
      me.global_role !== "user"
    )
      throw new Error("owned registration identity mismatch");
    await scope(page, me.id, teamId);
    await page.goto("/");
    return { page, context, cleanup };
  } catch {
    await context.close();
    throw new Error(
      "owned actor creation incomplete; journal recovery required",
    );
  }
}
export async function findOwnedActor(
  operator: Page,
  actor: OwnedActorCleanup,
): Promise<User> {
  const self = (await appApi<User>(operator, "/auth/me")).data;
  if (
    self.id === actor.resource_id ||
    self.email === actor.email ||
    actor.registration.user_id !== actor.resource_id ||
    actor.registration.team_id !== actor.workspace_id
  )
    throw new Error("owned actor binding mismatch");
  for (let offset = 0; ; offset += 200) {
    const result = (
      await appApi<{ users: User[]; total: number }>(
        operator,
        `/admin/users?limit=200&offset=${offset}`,
      )
    ).data;
    const found = result.users.find((user) => user.id === actor.resource_id);
    if (found) {
      if (
        found.email !== actor.email ||
        !Number.isFinite(Date.parse(found.created_at)) ||
        !Number.isFinite(Date.parse(actor.registration.joined_at)) ||
        Math.abs(
          Date.parse(found.created_at) -
            Date.parse(actor.registration.joined_at),
        ) > 60_000
      )
        throw new Error("owned actor creation receipt mismatch");
      return found;
    }
    if (offset + result.users.length >= result.total || !result.users.length)
      throw new Error("owned actor unavailable");
  }
}
export async function recoverOwnedActor(
  operator: Page,
  actor: OwnedActorCleanup,
): Promise<Actor> {
  const before = await findOwnedActor(operator, actor);
  if (before.status !== "active")
    throw new Error("owned creator is no longer active");
  const secret = readActorRecovery(actor);
  const context = await privateContext(operator);
  try {
    const page = await context.newPage();
    await page.goto("/login");
    await appApi(page, "/auth/login", {
      method: "POST",
      body: { email_or_username: actor.email, password: secret.password },
    });
    const me = (await appApi<User>(page, "/auth/me")).data;
    if (me.id !== actor.resource_id)
      throw new Error("recovered creator mismatch");
    if (before.global_role === "auditor")
      await appApi(operator, `/admin/users/${actor.resource_id}`, {
        method: "PATCH",
        body: { global_role: "user" },
      });
    const probe = await appApi(page, "/sessions", {
      headers: { "X-Workspace-Id": actor.workspace_id },
      expectStatus: [200, 403],
    });
    if (probe.status === 403) {
      let token = secret.restore_token;
      if (!token) {
        const invitation = (
          await appApi<{ url: string }>(
            operator,
            `/teams/${actor.workspace_id}/invitations`,
            {
              method: "POST",
              body: { role: "member", email: actor.email },
              headers: { "X-Workspace-Id": actor.workspace_id },
            },
          )
        ).data;
        token = invitationToken(invitation.url);
        saveRestorationToken(actor, token);
      }
      const restored = (
        await appApi<OwnedActorCleanup["registration"]>(
          page,
          `/invitations/${token}/accept`,
          { method: "POST" },
        )
      ).data;
      if (
        restored.user_id !== actor.resource_id ||
        restored.team_id !== actor.workspace_id
      )
        throw new Error("restored creator mismatch");
      saveRestorationToken(actor, undefined);
    }
    await scope(page, actor.resource_id, actor.workspace_id);
    return { page, context, cleanup: actor };
  } catch {
    await context.close();
    throw new Error("owned creator recovery failed");
  }
}
export async function disableOwnedActor(
  operator: Page,
  actor: OwnedActorCleanup,
): Promise<void> {
  const before = await findOwnedActor(operator, actor);
  let secret: ReturnType<typeof readActorRecovery>;
  try {
    secret = readActorRecovery(actor);
  } catch (error) {
    // A verified already-disabled identity may have completed private-file disposal
    // before journal acknowledgement. No other read failure is idempotent success.
    if (
      before.status === "disabled" &&
      (error as NodeJS.ErrnoException).code === "ENOENT"
    )
      return;
    throw error;
  }
  const context = await privateContext(operator);
  try {
    const page = await context.newPage();
    await page.goto("/login");
    if (before.status !== "disabled") {
      await appApi(page, "/auth/login", {
        method: "POST",
        body: { email_or_username: actor.email, password: secret.password },
      });
      const identity = (await appApi<User>(page, "/auth/me")).data;
      if (identity.id !== actor.resource_id)
        throw new Error("owned disable identity mismatch");
      const changed = (
        await appApi<User>(operator, `/admin/users/${actor.resource_id}`, {
          method: "PATCH",
          body: { status: "disabled" },
        })
      ).data;
      if (
        changed.id !== actor.resource_id ||
        changed.status !== "disabled" ||
        changed.token_version <= before.token_version
      )
        throw new Error("owned actor disable receipt mismatch");
      await appApi(page, "/auth/me", { expectStatus: 401 });
      await appApi(page, "/auth/refresh", {
        method: "POST",
        expectStatus: 401,
      });
    }
    const after = await findOwnedActor(operator, actor);
    if (after.status !== "disabled")
      throw new Error("owned actor disable readback failed");
    if (!process.env.ACCEPTANCE_EVIDENCE_DIR)
      throw new Error("actor cleanup evidence required");
    const root = resolve(
      process.env.ACCEPTANCE_EVIDENCE_DIR,
      "retained-resources",
    );
    mkdirSync(root, { recursive: true });
    writeFileSync(
      resolve(root, actor.recovery_id + "-actor.json"),
      JSON.stringify({
        run_id: process.env.ACCEPTANCE_RUN_ID,
        user_id: after.id,
        status: after.status,
        token_version: after.token_version,
        physically_deleted: false,
        immutable_history_retained: true,
      }),
      { mode: 0o600 },
    );

    await appApi(page, "/auth/login", {
      method: "POST",
      body: { email_or_username: actor.email, password: secret.password },
      expectStatus: [401, 403],
    });
  } finally {
    await context.close();
  }
  removeActorRecovery(actor);
}
