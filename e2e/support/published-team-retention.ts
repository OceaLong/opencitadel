import type { Page } from "@playwright/test";

import { appApi } from "./api";

export type PublishedTeamRetention = {
  run_id: string;
  team_id: string;
  team_name: string;
  team_created_at: string;
  owner_id: string;
  recording: {
    job_id: string;
    source_run_id: string;
    version_id: string;
    revision: number;
    slot_count: number;
    tool_names: string[];
  };
};

export function validatePublishedTeamRetention(
  value: PublishedTeamRetention,
  teamId: string,
  runId: string,
): void {
  const uuid =
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  if (
    value.run_id !== runId ||
    value.team_id !== teamId ||
    !value.team_name.startsWith(`${runId}-`) ||
    !Number.isFinite(Date.parse(value.team_created_at)) ||
    ![
      value.team_id,
      value.owner_id,
      value.recording.job_id,
      value.recording.source_run_id,
      value.recording.version_id,
    ].every((id) => uuid.test(id)) ||
    !Number.isInteger(value.recording.revision) ||
    value.recording.revision < 1 ||
    !Number.isInteger(value.recording.slot_count) ||
    value.recording.slot_count < 1 ||
    !Array.isArray(value.recording.tool_names) ||
    value.recording.tool_names.length === 0 ||
    value.recording.tool_names.some((name) => !name.trim()) ||
    new Set(value.recording.tool_names).size !==
      value.recording.tool_names.length
  )
    throw new Error("published team retention binding mismatch");
}

/** The result endpoint authorizes this scope and validates every fixed recording pin. */
export async function verifyPublishedTeamRetention(
  page: Page,
  expected: PublishedTeamRetention,
): Promise<Record<string, unknown>> {
  validatePublishedTeamRetention(
    expected,
    expected.team_id,
    process.env.ACCEPTANCE_RUN_ID ?? "",
  );
  const headers = { "X-Workspace-Id": expected.team_id };
  const team = (
    await appApi<{
      id: string;
      name: string;
      created_by: string;
      created_at: string;
    }>(page, `/teams/${expected.team_id}`, { headers })
  ).data;
  const caller = (await appApi<{ id: string }>(page, "/auth/me", { headers }))
    .data;
  const members = (
    await appApi<{
      members: { user_id: string; role: string }[];
    }>(page, `/teams/${expected.team_id}/members`, { headers })
  ).data.members;
  if (
    team.id !== expected.team_id ||
    team.name !== expected.team_name ||
    team.created_at !== expected.team_created_at ||
    team.created_by !== expected.owner_id ||
    caller.id !== expected.owner_id ||
    members.filter(
      (member) =>
        member.user_id === expected.owner_id && member.role === "owner",
    ).length !== 1
  )
    throw new Error("published team retention current owner mismatch");
  const recording = (
    await appApi<{
      id: string;
      source_run_id: string;
      status: string;
      result_version: string;
    }>(page, `/evaluation/recordings/${expected.recording.job_id}`, { headers })
  ).data;
  if (
    recording.id !== expected.recording.job_id ||
    recording.source_run_id !== expected.recording.source_run_id ||
    recording.status !== "ready" ||
    recording.result_version !== expected.recording.version_id
  )
    throw new Error("published team retention recording identity mismatch");
  const result = (
    await appApi<{
      version_id: string;
      revision: number;
      slot_count: number;
      tool_names: string[];
    }>(page, `/evaluation/recordings/${expected.recording.job_id}/result`, {
      headers,
    })
  ).data;
  if (
    result.version_id !== expected.recording.version_id ||
    result.revision !== expected.recording.revision ||
    result.slot_count !== expected.recording.slot_count ||
    JSON.stringify([...result.tool_names].sort()) !==
      JSON.stringify([...expected.recording.tool_names].sort())
  )
    throw new Error("published team retention fixed metadata mismatch");
  return {
    status: "retained-published-history",
    run_id: expected.run_id,
    resource: "team",
    resource_id: expected.team_id,
    team_name: team.name,
    team_created_at: team.created_at,
    owner_id: expected.owner_id,
    retention_owner: {
      owner_kind: "recording_version",
      owner_id: result.version_id,
    },
    recording: expected.recording,
    verification: "current-owner-and-scoped-recording-pin-validation",
    physically_deleted: false,
    archived: false,
    disposition_verified: true,
    retention_obligation: "permanent-published-history",
    gc_obligation: "retain-team-scope-and-owner-access",
  };
}
