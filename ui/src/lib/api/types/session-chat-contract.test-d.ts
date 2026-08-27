import type { ChatParams } from "./session";

const turn: ChatParams = {
  message: "inspect the deployment",
  request_id: "30000000-0000-0000-0000-000000000001",
};
const resume: ChatParams = { event_id: "cursor-1" };

// @ts-expect-error A new turn must carry its stable idempotency identity.
const turnWithoutRequestId: ChatParams = { message: "inspect the deployment" };

// @ts-expect-error A resume-only stream cannot masquerade as a turn retry.
const resumeWithRequestId: ChatParams = {
  event_id: "cursor-1",
  request_id: "30000000-0000-0000-0000-000000000001",
};

void [turn, resume, turnWithoutRequestId, resumeWithRequestId];
