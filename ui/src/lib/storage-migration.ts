export function migrateLocalStorageKey(oldKey: string, newKey: string): string | null {
  if (typeof window === "undefined") {
    return null;
  }

  const current = window.localStorage.getItem(newKey);
  if (current !== null) {
    return current;
  }

  const legacy = window.localStorage.getItem(oldKey);
  if (legacy !== null) {
    window.localStorage.setItem(newKey, legacy);
    window.localStorage.removeItem(oldKey);
  }

  return legacy;
}

export function readLocalStorageKey(oldKey: string, newKey: string): string {
  return migrateLocalStorageKey(oldKey, newKey) ?? "";
}

export function writeLocalStorageKey(oldKey: string, newKey: string, value: string): void {
  if (typeof window === "undefined") {
    return;
  }

  window.localStorage.setItem(newKey, value);
  if (oldKey !== newKey) {
    window.localStorage.removeItem(oldKey);
  }
}
