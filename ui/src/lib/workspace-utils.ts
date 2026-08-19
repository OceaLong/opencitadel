import { ACTIVE_WORKSPACE_KEY } from "@/lib/storage-keys";

export function resetWorkspaceIfMatches(teamId: string): void {
  if (typeof window === "undefined") return;
  const active = window.localStorage.getItem(ACTIVE_WORKSPACE_KEY) ?? "";
  if (active === teamId) {
    window.localStorage.setItem(ACTIVE_WORKSPACE_KEY, "");
  }
}
