/** Legacy localStorage keys from the Manus-era client; kept for one-way migration reads only. */
export const LEGACY_THEME_KEY = "my-manus-theme";
export const THEME_KEY = "opencitadel-theme";

/** Legacy workspace selection key; migrated to ACTIVE_WORKSPACE_KEY on read. */
export const LEGACY_ACTIVE_WORKSPACE_KEY = "my-manus-active-workspace";
export const ACTIVE_WORKSPACE_KEY = "opencitadel-active-workspace";

/** Legacy marketplace recents key; migrated to MARKETPLACE_RECENT_KEY on read. */
export const LEGACY_MARKETPLACE_RECENT_KEY = "my-manus-marketplace-recent";
export const MARKETPLACE_RECENT_KEY = "opencitadel-marketplace-recent";
