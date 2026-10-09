import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { basename, resolve } from "node:path";

import type { RunView } from "../../ui/src/lib/api/types/execution-view";
import { acceptanceId } from "./ids";

export type RetryFact = {
  event_type: string;
  stream_version: number;
  activity_id: string | null;
  activity_type: string | null;
};

/** Read only the current runner-owned database; never a URL supplied by a caller. */
export function readOwnedRunRetry(
  run: RunView,
  sessionId: string,
  actorId: string,
): RetryFact[] {
  const runId = run.run_id;
  acceptanceId("retry-proof");
  if (
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(
      runId,
    )
  )
    throw new Error("invalid run identity");
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  if (
    !uuid.test(sessionId) ||
    !uuid.test(actorId) ||
    run.scope.owner_user_id !== actorId ||
    run.scope.team_id != null ||
    run.source?.entity_type !== "session" ||
    run.source.entity_id !== sessionId
  )
    throw new Error("owned personal session scope required");
  const invocation = process.env.ACCEPTANCE_RUN_ID!;
  const evidence = process.env.ACCEPTANCE_EVIDENCE_DIR;
  if (!evidence || basename(resolve(evidence)) !== invocation)
    throw new Error("owned evidence directory required");
  const lifecycle = JSON.parse(
    readFileSync(resolve(evidence, "lifecycle.json"), "utf8"),
  );
  const bootstrap = JSON.parse(
    readFileSync(resolve(evidence, "bootstrap.json"), "utf8"),
  );
  if (bootstrap.run_id !== invocation || bootstrap.cleanup_completed)
    throw new Error("active matching bootstrap required");
  const project = `opencitadel-acceptance-${invocation}`
    .slice(0, 48)
    .replace(/-+$/, "");
  if (
    lifecycle.run_id !== invocation ||
    lifecycle.project_name !== project ||
    lifecycle.state !== "stack_ready"
  )
    throw new Error("active owned stack required");
  const container = `${project}-opencitadel-postgres-1`;
  const docker = (args: string[]) =>
    execFileSync("docker", args, {
      encoding: "utf8",
      timeout: 10_000,
      maxBuffer: 1024 * 1024,
    });
  const labels = JSON.parse(
    docker(["inspect", "--format", "{{json .Config.Labels}}", container]),
  );
  if (
    labels["com.docker.compose.project"] !== project ||
    labels["com.docker.compose.service"] !== "opencitadel-postgres" ||
    labels["com.opencitadel.acceptance.project"] !== project ||
    labels["com.opencitadel.acceptance.run"] !== invocation
  )
    throw new Error("container ownership mismatch");
  const sql = `BEGIN READ ONLY; SELECT COALESCE(json_agg(f ORDER BY stream_version),'[]'::json) FROM (SELECT event_type, stream_version, public_payload->>'activity_id' AS activity_id, public_payload->>'activity_type' AS activity_type FROM execution_events WHERE stream_type='run' AND stream_id='${runId}' AND owner_user_id='${actorId}' AND team_id IS NULL AND EXISTS(SELECT 1 FROM execution_run_projection r WHERE r.run_id='${runId}' AND r.owner_user_id='${actorId}' AND r.team_id IS NULL AND r.source_entity_type='session' AND r.source_entity_id='${sessionId}') AND event_type IN ('RunAttemptFailed','RunRetried','ActivityRequested')) f; COMMIT;`;
  const output = docker([
    "exec",
    container,
    "sh",
    "-c",
    'exec psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -XAtq -v ON_ERROR_STOP=1 -c "$1"',
    "owned-retry-proof",
    sql,
  ]);
  return JSON.parse(output.trim());
}
