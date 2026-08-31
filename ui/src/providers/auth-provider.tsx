"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { authApi, type AuthUser } from "@/lib/api/auth";
import { useClientDataScope } from "@/providers/client-data-provider";

type AuthContextValue = {
  user: AuthUser | null;
  loading: boolean;
  refresh: () => Promise<void>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const { bindAuthenticatedUser, clearAuthenticatedData } = useClientDataScope();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const authenticatedUser = await authApi.me();
      bindAuthenticatedUser(authenticatedUser.id);
      setUser(authenticatedUser);
    } catch {
      clearAuthenticatedData();
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, [bindAuthenticatedUser, clearAuthenticatedData]);

  const logout = useCallback(async () => {
    try {
      await authApi.logout();
    } finally {
      clearAuthenticatedData();
      setUser(null);
      window.location.href = "/";
    }
  }, [clearAuthenticatedData]);

  useEffect(() => {
    void refresh();

    // A history traversal can restore a document whose React state still
    // contains the previous user even though its HttpOnly session cookies
    // have expired or were cleared in another tab. Revalidate at both SPA
    // and browser-cache history boundaries before protected content renders
    // again from stale client state.
    const revalidateHistoryEntry = () => {
      void refresh();
    };
    window.addEventListener("popstate", revalidateHistoryEntry);
    window.addEventListener("pageshow", revalidateHistoryEntry);
    return () => {
      window.removeEventListener("popstate", revalidateHistoryEntry);
      window.removeEventListener("pageshow", revalidateHistoryEntry);
    };
  }, [refresh]);

  const value = useMemo(
    () => ({ user, loading, refresh, logout }),
    [user, loading, refresh, logout],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) {
    throw new Error("useAuth must be used within AuthProvider");
  }
  return value;
}
