"use client";

import { useCallback } from "react";
import { useTranslations } from "next-intl";

import { useAuth } from "@/providers/auth-provider";
import { useLoginPrompt } from "@/providers/login-prompt-provider";

export function useRequireAuth() {
  const { user, loading } = useAuth();
  const { promptLogin } = useLoginPrompt();
  const t = useTranslations("auth");

  const requireAuth = useCallback(
    (reason?: string): boolean => {
      if (loading) return false;
      if (!user) {
        promptLogin(reason ?? t("loginToUseAi"));
        return false;
      }
      return true;
    },
    [user, loading, promptLogin, t],
  );

  return {
    user,
    loading,
    isGuest: !loading && !user,
    requireAuth,
  };
}
