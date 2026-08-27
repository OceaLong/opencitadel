import { type ClientDataScope, clientDataScopeKey } from "./client-data-scope";

type CacheEntry<TValue> = {
  generation: number;
  pending?: Promise<TValue>;
  value?: TValue;
  hasValue: boolean;
};

function resourceKey(scope: ClientDataScope, resource: string): string {
  if (!resource.trim()) throw new Error("resource name must not be empty");
  return `${clientDataScopeKey(scope)}:${encodeURIComponent(resource)}`;
}

function scopePrefix(scope: ClientDataScope): string {
  return `${clientDataScopeKey(scope)}:`;
}

export class ScopedResourceCache<TValue> {
  private readonly entries = new Map<string, CacheEntry<TValue>>();
  private readonly generations = new Map<string, number>();

  load(
    scope: ClientDataScope,
    resource: string,
    loader: (scope: ClientDataScope) => Promise<TValue>,
  ): Promise<TValue> {
    const key = resourceKey(scope, resource);
    const generation = this.generations.get(key) ?? 0;
    const existing = this.entries.get(key);
    if (existing?.generation === generation) {
      if (existing.hasValue) return Promise.resolve(existing.value as TValue);
      if (existing.pending) return existing.pending;
    }

    const pending = Promise.resolve()
      .then(() => loader(scope))
      .then((value) => {
        if ((this.generations.get(key) ?? 0) === generation) {
          this.entries.set(key, { generation, value, hasValue: true });
        }
        return value;
      })
      .catch((error: unknown) => {
        const current = this.entries.get(key);
        if (current?.generation === generation && current.pending === pending) {
          this.entries.delete(key);
        }
        throw error;
      });

    this.entries.set(key, { generation, pending, hasValue: false });
    return pending;
  }

  peek(scope: ClientDataScope, resource: string): TValue | undefined {
    const key = resourceKey(scope, resource);
    const generation = this.generations.get(key) ?? 0;
    const entry = this.entries.get(key);
    return entry?.generation === generation && entry.hasValue ? entry.value : undefined;
  }

  invalidate(scope: ClientDataScope, resource: string): void {
    this.invalidateKey(resourceKey(scope, resource));
  }

  invalidateScope(scope: ClientDataScope): void {
    const prefix = scopePrefix(scope);
    const keys = new Set(
      [...this.entries.keys(), ...this.generations.keys()].filter((key) => key.startsWith(prefix)),
    );
    keys.forEach((key) => this.invalidateKey(key));
  }

  clear(): void {
    const keys = new Set([...this.entries.keys(), ...this.generations.keys()]);
    keys.forEach((key) => this.invalidateKey(key));
  }

  private invalidateKey(key: string): void {
    this.generations.set(key, (this.generations.get(key) ?? 0) + 1);
    this.entries.delete(key);
  }
}
