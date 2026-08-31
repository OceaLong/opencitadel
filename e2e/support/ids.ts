const IDENTIFIER = /^[a-z0-9][a-z0-9-]{2,47}$/;
const SUFFIX = /^[a-z0-9][a-z0-9-]{0,47}$/;

export function acceptanceId(
  suffix: string,
  environment: NodeJS.ProcessEnv = process.env,
): string {
  const runId = environment.ACCEPTANCE_RUN_ID;
  if (!runId || !IDENTIFIER.test(runId)) {
    throw new Error("ACCEPTANCE_RUN_ID must be a validated lowercase identifier");
  }
  if (!SUFFIX.test(suffix)) {
    throw new Error("acceptance resource suffix must be a lowercase identifier");
  }
  return `${runId}-${suffix}`;
}
