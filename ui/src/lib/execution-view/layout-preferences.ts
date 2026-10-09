import { type ClientDataScope, clientDataScopeKey } from "@/lib/data/client-data-scope";
export type WorkbenchLayout = {
  view?: "task" | "debug";
  shortcutsEnabled?: boolean;
  shortcuts?: Partial<Record<"task" | "debug" | "live" | "conversation", string>>;
  detailWidth?: number;
  conversationOpen?: boolean;
  conversationHeight?: number;
  contextCollapsed?: boolean;
};
type LayoutStorage = Pick<Storage, "getItem" | "setItem">;
export const layoutStorageKey = (scope: ClientDataScope) =>
  `execution-workbench:layout:${clientDataScopeKey(scope)}`;
function normalize(value: unknown): WorkbenchLayout {
  if (!value || typeof value !== "object") return {};
  const raw = value as Record<string, unknown>;
  const layout: WorkbenchLayout = {};
  if (raw.view === "task" || raw.view === "debug") layout.view = raw.view;
  if (typeof raw.detailWidth === "number" && Number.isFinite(raw.detailWidth))
    layout.detailWidth = Math.max(320, Math.min(640, raw.detailWidth));
  if (typeof raw.conversationHeight === "number" && Number.isFinite(raw.conversationHeight))
    layout.conversationHeight = Math.max(184, Math.min(440, raw.conversationHeight));
  if (typeof raw.conversationOpen === "boolean") layout.conversationOpen = raw.conversationOpen;
  if (typeof raw.contextCollapsed === "boolean") layout.contextCollapsed = raw.contextCollapsed;
  if (typeof raw.shortcutsEnabled === "boolean") layout.shortcutsEnabled = raw.shortcutsEnabled;
  if (raw.shortcuts && typeof raw.shortcuts === "object") {
    layout.shortcuts = {};
    for (const action of ["task", "debug", "live", "conversation"] as const) {
      const key = (raw.shortcuts as Record<string, unknown>)[action];
      if (typeof key === "string" && /^[a-z0-9]$/i.test(key))
        layout.shortcuts[action] = key.toLowerCase();
    }
  }
  return layout;
}
export function readLayout(scope: ClientDataScope, storage?: LayoutStorage): WorkbenchLayout {
  try {
    return normalize(
      JSON.parse((storage ?? window.localStorage).getItem(layoutStorageKey(scope)) ?? "{}"),
    );
  } catch {
    return {};
  }
}
export function saveLayout(
  scope: ClientDataScope,
  layout: WorkbenchLayout,
  storage?: LayoutStorage,
): WorkbenchLayout {
  const safe = normalize(layout);
  try {
    (storage ?? window.localStorage).setItem(layoutStorageKey(scope), JSON.stringify(safe));
  } catch {
    /* Preferences are optional in storage-restricted browsers. */
  }
  return safe;
}
