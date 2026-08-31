import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import type { Browser, Page } from "@playwright/test";

import { appApi, expect, test } from "./fixtures/acceptance.fixture";
import { registerCleanupAction } from "./support/cleanup-journal";
import { acceptanceId } from "./support/ids";

type Team = {
  id: string;
  name: string;
  description: string;
};

type TeamList = {
  teams: Team[];
};

type TeamMember = {
  user_id: string;
  role: "owner" | "admin" | "member";
  email: string;
};

type Session = {
  session_id: string;
  title: string;
};

type User = {
  email: string;
  global_role: string;
};

type UserFixture = {
  invitee: {
    emailDomain: string;
    usernamePrefix: string;
    password: string;
  };
};

const userFixture = JSON.parse(
  readFileSync(resolve(__dirname, "fixtures/users.json"), "utf8"),
) as UserFixture;

function cover(requirementId: string): void {
  test
    .info()
    .annotations.push({ type: "acceptance", description: requirementId });
}

function adminCredentials(): { email: string; password: string } {
  const email = process.env.BOOTSTRAP_ADMIN_EMAIL;
  const password = process.env.BOOTSTRAP_ADMIN_PASSWORD;
  if (!email || !password) {
    throw new Error(
      "BOOTSTRAP_ADMIN_EMAIL and BOOTSTRAP_ADMIN_PASSWORD are required",
    );
  }
  return { email, password };
}

async function createOwnedTeam(
  page: Page,
  suffix: string,
  description = "Acceptance-only team",
): Promise<Team> {
  const team = (
    await appApi<Team>(page, "/teams", {
      method: "POST",
      body: { name: acceptanceId(suffix), description },
    })
  ).data;
  registerCleanupAction({
    action: "delete-resource",
    resource: "team",
    resource_id: team.id,
  });
  return team;
}

async function selectWorkspace(page: Page, team: Team): Promise<void> {
  await page.getByRole("button", { name: /Workspace|工作区/ }).click();
  await page.getByRole("button", { name: team.name, exact: true }).click();
  await page.waitForLoadState("domcontentloaded");
  await expect
    .poll(() =>
      page.evaluate(() =>
        window.localStorage.getItem("opencitadel-active-workspace"),
      ),
    )
    .toBe(team.id);
}

async function openAccountMenu(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Administrator" }).click();
}

async function logoutThroughUi(page: Page): Promise<void> {
  await openAccountMenu(page);
  const logoutResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/auth/logout") &&
      response.request().method() === "POST",
  );
  await page.getByRole("menuitem", { name: /Sign out|退出登录/ }).click();
  expect((await logoutResponse).status()).toBe(200);
  await expect(
    page.getByRole("button", { name: /Sign in \/ Register|登录 \/ 注册/ }),
  ).toBeVisible();
  await expect(page).toHaveURL(/\/$/);
}

async function inviteeIdentity(): Promise<{
  email: string;
  username: string;
  password: string;
}> {
  const unique = acceptanceId("invitee").slice(-40);
  return {
    email: `${unique}@${userFixture.invitee.emailDomain}`,
    username: `${userFixture.invitee.usernamePrefix}-${unique}`.slice(0, 64),
    password: userFixture.invitee.password,
  };
}

async function isolatedPage(browser: Browser): Promise<{
  page: Page;
  close: () => Promise<void>;
}> {
  const context = await browser.newContext({
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:8088",
  });
  return {
    page: await context.newPage(),
    close: () => context.close(),
  };
}

test("administrator signs in through the product form", async ({ page }) => {
  cover("ID-LOGIN");
  const credentials = adminCredentials();

  await page.goto("/login");
  await page
    .getByLabel(/Email or username|邮箱或用户名/)
    .fill(credentials.email);
  await page.getByLabel(/^Password$|^密码$/).fill(credentials.password);
  await page.getByRole("button", { name: /^Sign in$|^登录$/ }).click();

  await expect(page).toHaveURL(/\/$/);
  const current = await appApi<User>(page, "/auth/me");
  expect(current.data.email).toBe(credentials.email);
  expect(current.data.global_role).toBe("admin");
});

test("administrator signs out and authenticated browser state is cleared", async ({
  operatorPage: page,
}) => {
  cover("ID-LOGOUT");
  await page.evaluate(() => {
    window.localStorage.setItem(
      "opencitadel-active-workspace",
      "stale-workspace",
    );
  });

  await logoutThroughUi(page);

  await appApi(page, "/auth/me", { expectStatus: 401 });
  expect(
    await page.evaluate(() =>
      window.localStorage.getItem("opencitadel-active-workspace"),
    ),
  ).toBeNull();
  await expect(
    page.getByRole("button", { name: /Sign in \/ Register|登录 \/ 注册/ }),
  ).toBeVisible();
});

test("administrator creates and selects a team workspace", async ({
  operatorPage: page,
}) => {
  cover("ID-TEAM");
  const name = acceptanceId("team-ui");

  await page.goto("/teams");
  await page
    .getByRole("button", { name: /^Create team$|^创建团队$/ })
    .first()
    .click();
  await page.getByLabel(/^Name$|^名称$/).fill(name);
  await page
    .getByLabel(/^Description$|^描述$/)
    .fill("Created through the acceptance UI");
  await page.getByRole("button", { name: /^Create$|^创建$/ }).click();
  await expect(page.getByRole("heading", { name })).toBeVisible();

  const teams = await appApi<TeamList>(page, "/teams");
  const team = teams.data.teams.find((candidate) => candidate.name === name);
  expect(
    team,
    "new team must be returned by the public team API",
  ).toBeDefined();
  registerCleanupAction({
    action: "delete-resource",
    resource: "team",
    resource_id: team!.id,
  });

  await page.goto("/");
  await selectWorkspace(page, team!);
});

test("an invited user registers, joins the team, and receives the invited role", async ({
  browser,
  operatorPage,
}) => {
  cover("ID-INVITE");
  const team = await createOwnedTeam(operatorPage, "team-invite");
  const identity = await inviteeIdentity();
  const invitation = await appApi<{ url: string }>(
    operatorPage,
    `/teams/${encodeURIComponent(team.id)}/invitations`,
    {
      method: "POST",
      body: { role: "member", email: identity.email },
    },
  );
  const invitationPath = new URL(invitation.data.url).pathname;
  const invitee = await isolatedPage(browser);

  try {
    await invitee.page.goto(invitationPath);
    await invitee.page.getByLabel(/^Email$|^邮箱$/).fill(identity.email);
    await invitee.page
      .getByLabel(/^Username$|^用户名$/)
      .fill(identity.username);
    await invitee.page
      .getByLabel(/Password \(min 8 characters\)|密码（至少 8 位）/)
      .fill(identity.password);
    await invitee.page
      .getByRole("button", { name: /^Register and join$|^注册并加入$/ })
      .click();
    await expect(invitee.page).toHaveURL(/\/$/);

    const current = await appApi<User>(invitee.page, "/auth/me");
    expect(current.data.email).toBe(identity.email);
    const members = await appApi<{ members: TeamMember[] }>(
      operatorPage,
      `/teams/${encodeURIComponent(team.id)}/members`,
    );
    expect(members.data.members).toContainEqual(
      expect.objectContaining({ email: identity.email, role: "member" }),
    );
  } finally {
    await invitee.close();
  }
});

test("workspace changes isolate scoped resources and invalidate the old scope", async ({
  operatorPage: page,
}) => {
  cover("ID-SCOPE");
  const teamA = await createOwnedTeam(page, "scope-a");
  const teamB = await createOwnedTeam(page, "scope-b");
  const sessionTitle = acceptanceId("scope-session");
  let sessionId = "";

  try {
    await page.goto("/");
    await selectWorkspace(page, teamA);
    sessionId = (
      await appApi<{ session_id: string }>(page, "/sessions", {
        method: "POST",
        body: { title: sessionTitle },
      })
    ).data.session_id;

    await selectWorkspace(page, teamB);
    const teamBSessions = await appApi<{ sessions: Session[] }>(
      page,
      "/sessions",
    );
    expect(
      teamBSessions.data.sessions.map((session) => session.title),
    ).not.toContain(sessionTitle);
    await appApi(page, `/sessions/${encodeURIComponent(sessionId)}`, {
      expectStatus: 404,
    });

    await selectWorkspace(page, teamA);
    const teamASessions = await appApi<{ sessions: Session[] }>(
      page,
      "/sessions",
    );
    expect(
      teamASessions.data.sessions.map((session) => session.title),
    ).toContain(sessionTitle);
  } finally {
    if (sessionId) {
      await page.evaluate((workspaceId) => {
        window.localStorage.setItem(
          "opencitadel-active-workspace",
          workspaceId,
        );
      }, teamA.id);
      await appApi(page, `/sessions/${encodeURIComponent(sessionId)}/delete`, {
        method: "POST",
        expectStatus: [200, 404],
      });
    }
  }
});

test("anonymous back navigation cannot reveal cached protected team data", async ({
  operatorPage: page,
}) => {
  cover("ID-ANON");
  const sensitiveDescription = acceptanceId("private-team-content");
  const team = await createOwnedTeam(page, "team-anon", sensitiveDescription);

  await page.goto(`/teams/${encodeURIComponent(team.id)}`);
  await expect(page.getByText(sensitiveDescription)).toBeVisible();
  await page.goto("/");
  await page.context().clearCookies();
  await page.goBack();

  await expect(page).toHaveURL(/\/login(?:\?|$)/);
  await expect(page.getByText(sensitiveDescription)).toHaveCount(0);
  await appApi(page, "/auth/me", { expectStatus: 401 });
});
