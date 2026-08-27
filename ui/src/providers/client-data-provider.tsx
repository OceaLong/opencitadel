"use client";

import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";

import type { ClientDataScope } from "@/lib/data/client-data-scope";
import { ScopedResourceCache } from "@/lib/data/scoped-resource-cache";
import { ACTIVE_WORKSPACE_KEY, activeWorkspaceStorageKey } from "@/lib/storage-keys";

export type ClientDataScopeContextValue = {
  scope: ClientDataScope | null;
  scopeRevision: number;
  bindAuthenticatedUser: (userId: string | null) => void;
  setWorkspaceId: (workspaceId: string) => void;
  resetWorkspaceIfMatches: (workspaceId: string) => void;
  loadResource: <TValue>(
    resource: ClientResourceName,
    loader: (scope: ClientDataScope) => Promise<TValue>,
  ) => Promise<TValue>;
  peekResource: <TValue>(resource: ClientResourceName) => TValue | undefined;
  invalidateResource: (resource: ClientResourceName) => void;
  resourceRevision: (resource: ClientResourceName) => number;
  invalidateCurrentScope: () => void;
  clearAuthenticatedData: () => void;
};

export type ClientResourceName = "inference" | "skills";

const ClientDataContext = createContext<ClientDataScopeContextValue | null>(null);

function readWorkspace(userId: string): string {
  if (typeof window === "undefined") return "";
  return window.localStorage.getItem(activeWorkspaceStorageKey(userId)) ?? "";
}

function writeWorkspace(userId: string, workspaceId: string): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(activeWorkspaceStorageKey(userId), workspaceId);
  window.localStorage.setItem(ACTIVE_WORKSPACE_KEY, workspaceId);
}

function clearWorkspaceMirror(): void {
  if (typeof window !== "undefined") {
    window.localStorage.removeItem(ACTIVE_WORKSPACE_KEY);
  }
}

export function ClientDataProvider({ children }: { children: React.ReactNode }) {
  const cacheRef = useRef(new ScopedResourceCache<unknown>());
  const scopeRef = useRef<ClientDataScope | null>(null);
  const [scope, setScope] = useState<ClientDataScope | null>(null);
  const [scopeRevision, setScopeRevision] = useState(0);
  const [resourceRevisions, setResourceRevisions] = useState<
    Partial<Record<ClientResourceName, number>>
  >({});

  const publishScope = useCallback((next: ClientDataScope | null) => {
    scopeRef.current = next;
    setScope(next);
    setScopeRevision((value) => value + 1);
  }, []);

  const clearAuthenticatedData = useCallback(() => {
    cacheRef.current.clear();
    clearWorkspaceMirror();
    publishScope(null);
  }, [publishScope]);

  const bindAuthenticatedUser = useCallback(
    (userId: string | null) => {
      if (!userId) {
        clearAuthenticatedData();
        return;
      }
      const current = scopeRef.current;
      if (current?.userId === userId) return;
      if (current) cacheRef.current.invalidateScope(current);
      const workspaceId = readWorkspace(userId);
      writeWorkspace(userId, workspaceId);
      publishScope({ userId, workspaceId });
    },
    [clearAuthenticatedData, publishScope],
  );

  const setWorkspaceId = useCallback(
    (workspaceId: string) => {
      const current = scopeRef.current;
      if (!current) throw new Error("cannot select a workspace without an authenticated user");
      if (current.workspaceId === workspaceId) return;
      cacheRef.current.invalidateScope(current);
      writeWorkspace(current.userId, workspaceId);
      publishScope({ userId: current.userId, workspaceId });
    },
    [publishScope],
  );

  const resetWorkspaceIfMatches = useCallback(
    (workspaceId: string) => {
      if (scopeRef.current?.workspaceId === workspaceId) {
        setWorkspaceId("");
      }
    },
    [setWorkspaceId],
  );

  const loadResource = useCallback(
    <TValue,>(
      resource: ClientResourceName,
      loader: (scope: ClientDataScope) => Promise<TValue>,
    ): Promise<TValue> => {
      const current = scopeRef.current;
      if (!current)
        return Promise.reject(new Error("authenticated client data scope is unavailable"));
      return cacheRef.current.load(
        current,
        resource,
        loader as (scope: ClientDataScope) => Promise<unknown>,
      ) as Promise<TValue>;
    },
    [],
  );

  const peekResource = useCallback(<TValue,>(resource: ClientResourceName): TValue | undefined => {
    const current = scopeRef.current;
    if (!current) return undefined;
    return cacheRef.current.peek(current, resource) as TValue | undefined;
  }, []);

  const invalidateResource = useCallback((resource: ClientResourceName) => {
    const current = scopeRef.current;
    if (!current) return;
    cacheRef.current.invalidate(current, resource);
    setResourceRevisions((values) => ({
      ...values,
      [resource]: (values[resource] ?? 0) + 1,
    }));
  }, []);

  const resourceRevision = useCallback(
    (resource: ClientResourceName) => resourceRevisions[resource] ?? 0,
    [resourceRevisions],
  );

  const invalidateCurrentScope = useCallback(() => {
    const current = scopeRef.current;
    if (!current) return;
    cacheRef.current.invalidateScope(current);
    setScopeRevision((value) => value + 1);
  }, []);

  const value = useMemo<ClientDataScopeContextValue>(
    () => ({
      scope,
      scopeRevision,
      bindAuthenticatedUser,
      setWorkspaceId,
      resetWorkspaceIfMatches,
      loadResource,
      peekResource,
      invalidateResource,
      resourceRevision,
      invalidateCurrentScope,
      clearAuthenticatedData,
    }),
    [
      bindAuthenticatedUser,
      clearAuthenticatedData,
      invalidateCurrentScope,
      invalidateResource,
      loadResource,
      peekResource,
      resourceRevision,
      resetWorkspaceIfMatches,
      scopeRevision,
      scope,
      setWorkspaceId,
    ],
  );

  return <ClientDataContext.Provider value={value}>{children}</ClientDataContext.Provider>;
}

export function useClientDataScope(): ClientDataScopeContextValue {
  const value = useContext(ClientDataContext);
  if (!value) throw new Error("useClientDataScope must be used within ClientDataProvider");
  return value;
}
