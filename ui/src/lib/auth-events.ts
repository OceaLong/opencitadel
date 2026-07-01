export const AUTH_REQUIRED_EVENT = "auth:required";

export function dispatchAuthRequired(reason?: string): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(AUTH_REQUIRED_EVENT, { detail: { reason } }));
}
