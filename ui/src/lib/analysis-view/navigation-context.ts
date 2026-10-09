import { type AnalysisSelection, emptySelection } from "./selection";

// Session storage is tab-local; only public query inputs and identifiers are retained.
// It never supplies response data or authority. Reads still re-authorize the saved capture.
const PREFIX = "opencitadel:analysis-return:v1:";
export const CONTEXT_TTL_MS = 15 * 60_000;
const MAX_CHARACTERS = 2_400_000; // < 5 MiB even for UTF-16 DOM-storage accounting
const MAX_ENTRIES = 8;
const MAX_INSPECTED_KEYS = 1024;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
type PackedIds = { encoding: "uuid"; value: string } | { encoding: "text"; value: string[] };
type PackedSelection = {
  mode: AnalysisSelection["mode"];
  runIds: PackedIds;
  excludedIds: PackedIds;
  details: PackedIds;
};
export type NavigationContext = {
  path: string;
  params: URLSearchParams;
  selection?: AnalysisSelection;
  anchor?: string;
};
type Envelope = {
  version: 1;
  owner: string;
  path: string;
  capture: string;
  created: number;
  expires: number;
  params: string;
  selection: PackedSelection;
  anchor: string;
};
export class NavigationContextError extends Error {
  constructor(
    public reason: "unavailable" | "invalid" | "missing" | "expired" | "foreign" | "capacity",
  ) {
    super(reason);
  }
}
function pack(ids: string[]): PackedIds {
  if (ids.every((id) => UUID.test(id))) {
    const bytes = new Uint8Array(ids.length * 16);
    ids.forEach((id, index) => {
      const hex = id.replaceAll("-", "");
      for (let i = 0; i < 16; i++)
        bytes[index * 16 + i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
    });
    let binary = "";
    for (let i = 0; i < bytes.length; i += 8192)
      binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
    return { encoding: "uuid", value: btoa(binary) };
  }
  return { encoding: "text", value: ids };
}
function unpack(value: PackedIds): string[] {
  if (
    value?.encoding === "text" &&
    Array.isArray(value.value) &&
    value.value.every((id) => typeof id === "string")
  )
    return value.value;
  if (value?.encoding !== "uuid" || typeof value.value !== "string")
    throw new NavigationContextError("invalid");
  const binary = atob(value.value);
  if (binary.length % 16 !== 0 || binary.length / 16 > 100_000)
    throw new NavigationContextError("invalid");
  const result: string[] = [];
  for (let i = 0; i < binary.length; i += 16) {
    let hex = "";
    for (let j = 0; j < 16; j++)
      hex += binary
        .charCodeAt(i + j)
        .toString(16)
        .padStart(2, "0");
    result.push(
      `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`,
    );
  }
  return result;
}
function validateSelection(selection: AnalysisSelection) {
  if (
    !selection ||
    !["explicit", "all_matching"].includes(selection.mode) ||
    ![selection.runIds, selection.excludedIds, selection.details].every(
      (ids) =>
        Array.isArray(ids) && ids.length <= 100_000 && ids.every((id) => typeof id === "string"),
    ) ||
    selection.details.length > 5
  )
    throw new NavigationContextError("invalid");
}
function captureIdentity(path: string, params: URLSearchParams): string {
  if (path === "/analysis" && params.get("watermark")) return `analysis:${params.get("watermark")}`;
  if (
    /^\/analysis\/comparisons\/[\w-]{1,128}$/.test(path) &&
    /^[1-9]\d*$/.test(params.get("revision") ?? "")
  )
    return `comparison:${path}:${params.get("revision")}`;
  throw new NavigationContextError("invalid");
}
function checkedEnvelope(raw: string): Envelope {
  const value = JSON.parse(raw) as Envelope;
  if (
    !value ||
    value.version !== 1 ||
    typeof value.owner !== "string" ||
    typeof value.path !== "string" ||
    typeof value.params !== "string" ||
    value.params.length > 65536 ||
    typeof value.anchor !== "string" ||
    value.anchor.length > 512 ||
    !Number.isFinite(value.created) ||
    !Number.isFinite(value.expires) ||
    value.expires !== value.created + CONTEXT_TTL_MS ||
    value.created > Date.now() ||
    value.capture !== captureIdentity(value.path, new URLSearchParams(value.params))
  )
    throw new NavigationContextError("invalid");
  return value;
}
function guarded<T>(action: () => T): T {
  try {
    return action();
  } catch (error) {
    if (error instanceof NavigationContextError) throw error;
    throw new NavigationContextError("unavailable");
  }
}
export function saveNavigationContext(context: NavigationContext, owner: string): string {
  return guarded(() => {
    const selection = context.selection ?? emptySelection();
    validateSelection(selection);
    const params = new URLSearchParams(context.params);
    params.delete("context");
    params.delete("selection");
    const now = Date.now();
    const envelope: Envelope = {
      version: 1,
      owner,
      path: context.path,
      capture: captureIdentity(context.path, params),
      created: now,
      expires: now + CONTEXT_TTL_MS,
      params: params.toString(),
      selection: {
        mode: selection.mode,
        runIds: pack(selection.runIds),
        excludedIds: pack(selection.excludedIds),
        details: pack(selection.details),
      },
      anchor: context.anchor ?? "",
    };
    const raw = JSON.stringify(envelope);
    checkedEnvelope(raw);
    if (raw.length > MAX_CHARACTERS) throw new NavigationContextError("capacity");
    const storage = window.sessionStorage;
    if (storage.length > MAX_INSPECTED_KEYS) throw new NavigationContextError("capacity");
    const entries: { key: string; size: number; created: number }[] = [];
    // Snapshot only our namespace, never delete other application's tab state.
    const keys = Array.from({ length: storage.length }, (_, i) => storage.key(i)).filter(
      (key): key is string => !!key && key.startsWith(PREFIX),
    );
    for (const key of keys) {
      const existing = storage.getItem(key);
      if (!existing) continue;
      if (existing.length > MAX_CHARACTERS) {
        storage.removeItem(key);
        continue;
      }
      try {
        const parsed = checkedEnvelope(existing);
        if (parsed.expires <= now) {
          storage.removeItem(key);
          continue;
        }
        entries.push({ key, size: existing.length, created: parsed.created });
      } catch {
        storage.removeItem(key);
      }
    }
    entries.sort((a, b) => a.created - b.created);
    let total = entries.reduce((sum, entry) => sum + entry.size, 0);
    while (
      entries.length &&
      (total + raw.length > MAX_CHARACTERS || entries.length >= MAX_ENTRIES)
    ) {
      const removed = entries.shift()!;
      storage.removeItem(removed.key);
      total -= removed.size;
    }
    const locator = crypto.randomUUID();
    storage.setItem(PREFIX + locator, raw);
    return `${context.path}?context=${locator}${context.path !== "/analysis" ? `&revision=${params.get("revision")}` : ""}${context.anchor ? `#${encodeURIComponent(context.anchor)}` : ""}`;
  });
}
export function readNavigationContext(
  path: string,
  search: URLSearchParams,
  owner: string,
): NavigationContext | null {
  return guarded(() => {
    const locator = search.get("context");
    if (!locator) {
      if (search.has("selection")) throw new NavigationContextError("invalid");
      return null;
    }
    if (!UUID.test(locator)) throw new NavigationContextError("invalid");
    const raw = window.sessionStorage.getItem(PREFIX + locator);
    if (!raw) throw new NavigationContextError("missing");
    if (raw.length > MAX_CHARACTERS) throw new NavigationContextError("capacity");
    const envelope = checkedEnvelope(raw);
    if (
      envelope.owner !== owner ||
      envelope.path !== path ||
      (path !== "/analysis" &&
        search.get("revision") !== new URLSearchParams(envelope.params).get("revision"))
    )
      throw new NavigationContextError("foreign");
    if (envelope.expires <= Date.now()) throw new NavigationContextError("expired");
    const selection = {
      mode: envelope.selection.mode,
      runIds: unpack(envelope.selection.runIds),
      excludedIds: unpack(envelope.selection.excludedIds),
      details: unpack(envelope.selection.details),
    };
    validateSelection(selection);
    return {
      path,
      params: new URLSearchParams(envelope.params),
      selection,
      anchor: envelope.anchor,
    };
  });
}

export const navigationOwner = (access: {
  callerId?: string;
  ownerKey: string;
  workspaceId: string;
}) => JSON.stringify([access.callerId ?? access.ownerKey, access.workspaceId]);
