// @vitest-environment jsdom

import { act, useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { ACTIVE_WORKSPACE_KEY, activeWorkspaceStorageKey } from "@/lib/storage-keys";

import { renderComponent } from "@/test-utils/render";

import {
  ClientDataProvider,
  type ClientDataScopeContextValue,
  useClientDataScope,
} from "./client-data-provider";

let clientData: ClientDataScopeContextValue | null = null;

function Probe() {
  const value = useClientDataScope();
  useEffect(() => {
    clientData = value;
  }, [value]);
  return <div>{value.scope ? `${value.scope.userId}:${value.scope.workspaceId}` : "anon"}</div>;
}

describe("ClientDataProvider", () => {
  beforeEach(() => {
    const values = new Map<string, string>();
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      value: {
        get length() {
          return values.size;
        },
        clear: () => values.clear(),
        getItem: (key: string) => values.get(key) ?? null,
        key: (index: number) => [...values.keys()][index] ?? null,
        removeItem: (key: string) => values.delete(key),
        setItem: (key: string, value: string) => values.set(key, value),
      } satisfies Storage,
    });
  });

  afterEach(() => {
    clientData = null;
    window.localStorage.clear();
    document.body.replaceChildren();
  });

  it("derives a cache scope only after an authenticated identity is bound", async () => {
    window.localStorage.setItem(activeWorkspaceStorageKey("u1"), "team-1");
    const { container, unmount } = await renderComponent(
      <ClientDataProvider>
        <Probe />
      </ClientDataProvider>,
    );

    expect(container.textContent).toBe("anon");
    expect(window.localStorage.getItem(ACTIVE_WORKSPACE_KEY)).toBeNull();

    await act(async () => clientData?.bindAuthenticatedUser("u1"));

    expect(container.textContent).toBe("u1:team-1");
    expect(window.localStorage.getItem(ACTIVE_WORKSPACE_KEY)).toBe("team-1");
    await unmount();
  });

  it("clears authenticated cache and workspace state before logout navigation", async () => {
    const { container, unmount } = await renderComponent(
      <ClientDataProvider>
        <Probe />
      </ClientDataProvider>,
    );
    await act(async () => {
      clientData?.bindAuthenticatedUser("u1");
      clientData?.setWorkspaceId("team-1");
    });
    await clientData?.loadResource("skills", async () => "private");
    expect(clientData?.peekResource("skills")).toBe("private");

    await act(async () => clientData?.clearAuthenticatedData());

    expect(container.textContent).toBe("anon");
    expect(clientData?.peekResource("skills")).toBeUndefined();
    expect(window.localStorage.getItem(ACTIVE_WORKSPACE_KEY)).toBeNull();
    await unmount();
  });
});
