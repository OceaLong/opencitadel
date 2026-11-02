import { expect, test, type Page } from "@playwright/test";
import { readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";

import {
  bindPublishedTeamRetention,
  completeCleanupAction,
  readCleanupActions,
  registerCleanupAction,
  runCleanupResources,
} from "../support/cleanup-journal";
import { cleanupProductResource } from "../support/product-cleanup";
import type { PublishedTeamRetention } from "../support/published-team-retention";

const expected: PublishedTeamRetention = {
  run_id: "owned-current-run",
  team_id: "11111111-1111-4111-8111-111111111111",
  team_name: "owned-current-run-authority",
  team_created_at: "2026-10-10T10:00:00Z",
  owner_id: "22222222-2222-4222-8222-222222222222",
  recording: {
    job_id: "33333333-3333-4333-8333-333333333333",
    source_run_id: "44444444-4444-4444-8444-444444444444",
    version_id: "55555555-5555-4555-8555-555555555555",
    revision: 1,
    slot_count: 2,
    tool_names: ["__retrieval__", "artifact_write"],
  },
};

let previous: { root?: string; run?: string };
test.beforeEach(async ({}, testInfo) => {
  previous = {
    root: process.env.ACCEPTANCE_EVIDENCE_DIR,
    run: process.env.ACCEPTANCE_RUN_ID,
  };
  process.env.ACCEPTANCE_EVIDENCE_DIR = testInfo.outputPath("retention");
  process.env.ACCEPTANCE_RUN_ID = expected.run_id;
});
test.afterEach(async () => {
  for (const [key, value] of [
    ["ACCEPTANCE_EVIDENCE_DIR", previous.root],
    ["ACCEPTANCE_RUN_ID", previous.run],
  ]) {
    if (value === undefined) delete process.env[key!];
    else process.env[key!] = value;
  }
});

function responses(): Record<string, any> {
  return {
    [`DELETE:/teams/${expected.team_id}`]: {
      status: 409,
      payload: {
        code: 409,
        msg: "Resource is retained by published references",
        error_key: "resource_pinned",
        data: { resource_kind: "team", resource_id: expected.team_id },
      },
    },
    [`GET:/teams/${expected.team_id}`]: {
      id: expected.team_id,
      name: expected.team_name,
      created_by: expected.owner_id,
      created_at: expected.team_created_at,
    },
    "GET:/auth/me": { id: expected.owner_id },
    // TeamMemberDetail deliberately has no team_id; the endpoint binds the team.
    [`GET:/teams/${expected.team_id}/members`]: {
      members: [
        {
          user_id: expected.owner_id,
          role: "owner",
          joined_at: expected.team_created_at,
        },
      ],
    },
    [`GET:/evaluation/recordings/${expected.recording.job_id}`]: {
      id: expected.recording.job_id,
      source_run_id: expected.recording.source_run_id,
      revision: 3,
      status: "ready",
      result_version: expected.recording.version_id,
      error: null,
    },
    [`GET:/evaluation/recordings/${expected.recording.job_id}/result`]: {
      version_id: expected.recording.version_id,
      revision: expected.recording.revision,
      slot_count: expected.recording.slot_count,
      tool_names: [...expected.recording.tool_names].reverse(),
    },
  };
}

function pageFor(data: Record<string, any>, requests: any[] = []): Page {
  return {
    evaluate: async (_fn: unknown, request: any) => {
      requests.push(request);
      const value =
        data[`${request.requestInit.method}:${request.requestPath}`];
      if (value === undefined) throw new Error("unexpected public request");
      return typeof value.status === "number"
        ? value
        : { status: 200, payload: { code: 200, msg: "success", data: value } };
    },
  } as unknown as Page;
}

function journal() {
  const entry = registerCleanupAction({
    action: "delete-resource",
    resource: "team",
    resource_id: expected.team_id,
  });
  bindPublishedTeamRetention(entry, structuredClone(expected));
  return entry;
}

async function runJournal(page: Page): Promise<string[]> {
  return runCleanupResources(
    readCleanupActions(),
    async (entry) => {
      if (entry.value.action !== "delete-resource")
        throw new Error("wrong action");
      await cleanupProductResource(
        page,
        entry.value.resource,
        entry.value.resource_id,
        {
          expectedTeamRetention: entry.value.expected_team_retention,
        },
      );
    },
    completeCleanupAction,
  );
}

test("exact current published recording preserves team owner and permanent history without claiming deletion", async () => {
  const entry = journal();
  expect(readCleanupActions()[0].value).toMatchObject({
    expected_team_retention: expected,
  });
  const requests: any[] = [];
  const page = pageFor(responses(), requests);
  expect(await runJournal(page)).toEqual([]);
  expect(readCleanupActions()).toEqual([]);
  completeCleanupAction(entry); // Durable completion replay is idempotent.
  await cleanupProductResource(page, "team", expected.team_id, {
    expectedTeamRetention: expected,
  });
  const directory = resolve(
    process.env.ACCEPTANCE_EVIDENCE_DIR!,
    "retained-resources",
  );
  expect(readdirSync(directory)).toHaveLength(1);
  const evidence = JSON.parse(
    readFileSync(resolve(directory, readdirSync(directory)[0]), "utf8"),
  );
  expect(evidence).toMatchObject({
    status: "retained-published-history",
    resource_id: expected.team_id,
    owner_id: expected.owner_id,
    recording: expected.recording,
    retention_owner: {
      owner_kind: "recording_version",
      owner_id: expected.recording.version_id,
    },
    physically_deleted: false,
    archived: false,
    disposition_verified: true,
    retention_obligation: "permanent-published-history",
    gc_obligation: "retain-team-scope-and-owner-access",
  });
  for (const request of requests.filter(
    (request) => request.requestInit.method === "GET",
  ))
    expect(request.requestInit.headers).toEqual({
      "X-Workspace-Id": expected.team_id,
    });
});

const rejected = [
  [
    "foreign team readback",
    (data: Record<string, any>) => {
      data[`GET:/teams/${expected.team_id}`].id = "foreign";
    },
  ],
  [
    "changed creator",
    (data: Record<string, any>) => {
      data[`GET:/teams/${expected.team_id}`].created_by = "foreign";
    },
  ],
  [
    "changed team timestamp",
    (data: Record<string, any>) => {
      data[`GET:/teams/${expected.team_id}`].created_at =
        "2026-10-11T10:00:00Z";
    },
  ],
  [
    "changed caller",
    (data: Record<string, any>) => {
      data["GET:/auth/me"].id = "foreign";
    },
  ],
  [
    "missing current owner role",
    (data: Record<string, any>) => {
      data[`GET:/teams/${expected.team_id}/members`].members[0].role = "member";
    },
  ],
  [
    "foreign source run",
    (data: Record<string, any>) => {
      data[
        `GET:/evaluation/recordings/${expected.recording.job_id}`
      ].source_run_id = "foreign";
    },
  ],
  [
    "recording not published",
    (data: Record<string, any>) => {
      data[`GET:/evaluation/recordings/${expected.recording.job_id}`].status =
        "running";
    },
  ],
  [
    "changed fixed result version",
    (data: Record<string, any>) => {
      data[
        `GET:/evaluation/recordings/${expected.recording.job_id}/result`
      ].version_id = "foreign";
    },
  ],
  [
    "changed fixed revision",
    (data: Record<string, any>) => {
      data[`GET:/evaluation/recordings/${expected.recording.job_id}/result`]
        .revision++;
    },
  ],
  [
    "changed fixed slots",
    (data: Record<string, any>) => {
      data[
        `GET:/evaluation/recordings/${expected.recording.job_id}/result`
      ].slot_count = 0;
    },
  ],
  [
    "changed fixed tool metadata",
    (data: Record<string, any>) => {
      data[
        `GET:/evaluation/recordings/${expected.recording.job_id}/result`
      ].tool_names = ["shell_exec"];
    },
  ],
  [
    "current scope denied",
    (data: Record<string, any>) => {
      data[`GET:/evaluation/recordings/${expected.recording.job_id}`] = {
        status: 403,
        payload: { code: 403, msg: "denied", data: {} },
      };
    },
  ],
  [
    "current pin unavailable",
    (data: Record<string, any>) => {
      data[`GET:/evaluation/recordings/${expected.recording.job_id}/result`] = {
        status: 409,
        payload: { code: 409, msg: "recording_source_unavailable", data: {} },
      };
    },
  ],
  [
    "foreign conflict identity",
    (data: Record<string, any>) => {
      data[`DELETE:/teams/${expected.team_id}`].payload.data.resource_id =
        "foreign";
    },
  ],
  [
    "server error despite explicit binding",
    (data: Record<string, any>) => {
      data[`DELETE:/teams/${expected.team_id}`] = {
        status: 500,
        payload: { code: 500, msg: "error", data: {} },
      };
    },
  ],
  [
    "unexpected deletion despite permanent history",
    (data: Record<string, any>) => {
      data[`DELETE:/teams/${expected.team_id}`] = {
        status: 200,
        payload: { code: 200, msg: "success", data: {} },
      };
    },
  ],
  [
    "permanent history team missing",
    (data: Record<string, any>) => {
      data[`DELETE:/teams/${expected.team_id}`] = {
        status: 404,
        payload: { code: 404, msg: "not found", data: {} },
      };
    },
  ],
] as const;

for (const [name, change] of rejected)
  test(`${name} keeps the team obligation pending`, async () => {
    journal();
    const data = responses();
    change(data);
    expect(await runJournal(pageFor(data))).toHaveLength(1);
    expect(readCleanupActions()).toHaveLength(1);
  });

test("published retention journal rejects another resource, scope, invocation or team binding", () => {
  for (const override of [
    { resource: "file" },
    { workspace_id: expected.team_id },
    { resource_id: "66666666-6666-4666-8666-666666666666" },
    { expected_team_retention: { ...expected, run_id: "foreign-run" } },
    {
      expected_team_retention: {
        ...expected,
        recording: { ...expected.recording, slot_count: 0 },
      },
    },
  ])
    expect(() =>
      registerCleanupAction({
        action: "delete-resource",
        resource: "team",
        resource_id: expected.team_id,
        expected_team_retention: expected,
        ...override,
      } as any),
    ).toThrow(/published.*retention/);
  expect(readCleanupActions()).toEqual([]);
});
