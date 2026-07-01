"use client";

import { useCallback } from "react";

import { useAuth } from "@/providers/auth-provider";
import { useLoginPrompt } from "@/providers/login-prompt-provider";

export function useRequireAuth() {
  const { user, loading } = useAuth();
  const { promptLogin } = useLoginPrompt();

  const requireAuth = useCallback(
    (reason?: string): boolean => {
      if (loading) return false;
      if (!user) {
        promptLogin(reason ?? "登录后即可使用 AI 功能");
        return false;
      }
      return true;
    },
    [user, loading, promptLogin],
  );

  return {
    user,
    loading,
    isGuest: !loading && !user,
    requireAuth,
  };
}
