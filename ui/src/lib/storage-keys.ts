/** Stable browser-storage keys owned by the OpenCitadel UI. */
export const THEME_KEY = "opencitadel-theme";

export const ACTIVE_WORKSPACE_KEY = "opencitadel-active-workspace";

export function activeWorkspaceStorageKey(userId: string): string {
  return `${ACTIVE_WORKSPACE_KEY}:${encodeURIComponent(userId)}`;
}
