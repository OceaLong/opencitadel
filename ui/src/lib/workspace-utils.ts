import { ACTIVE_WORKSPACE_KEY, LEGACY_ACTIVE_WORKSPACE_KEY } from "@/lib/storage-keys";
import { readLocalStorageKey, writeLocalStorageKey } from "@/lib/storage-migration";

export function resetWorkspaceIfMatches(teamId: string): void {
  const active = readLocalStorageKey(LEGACY_ACTIVE_WORKSPACE_KEY, ACTIVE_WORKSPACE_KEY);
  if (active === teamId) {
    writeLocalStorageKey(LEGACY_ACTIVE_WORKSPACE_KEY, ACTIVE_WORKSPACE_KEY, "");
  }
}
