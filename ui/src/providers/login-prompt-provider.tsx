"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { LoginDialog } from "@/components/login-dialog";
import { AUTH_REQUIRED_EVENT } from "@/lib/auth-events";

type LoginPromptContextValue = {
  promptLogin: (reason?: string) => void;
  closeLogin: () => void;
  open: boolean;
  reason: string | null;
};

const LoginPromptContext = createContext<LoginPromptContextValue | null>(null);

export function LoginPromptProvider({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState<string | null>(null);

  const promptLogin = useCallback((nextReason?: string) => {
    setReason(nextReason ?? null);
    setOpen(true);
  }, []);

  const closeLogin = useCallback(() => {
    setOpen(false);
    setReason(null);
  }, []);

  useEffect(() => {
    const handleAuthRequired = (event: Event) => {
      const customEvent = event as CustomEvent<{ reason?: string }>;
      promptLogin(customEvent.detail?.reason);
    };

    window.addEventListener(AUTH_REQUIRED_EVENT, handleAuthRequired);
    return () => window.removeEventListener(AUTH_REQUIRED_EVENT, handleAuthRequired);
  }, [promptLogin]);

  const value = useMemo(
    () => ({ promptLogin, closeLogin, open, reason }),
    [promptLogin, closeLogin, open, reason],
  );

  return (
    <LoginPromptContext.Provider value={value}>
      {children}
      <LoginDialog open={open} reason={reason} onOpenChange={setOpen} onSuccess={closeLogin} />
    </LoginPromptContext.Provider>
  );
}

export function useLoginPrompt() {
  const value = useContext(LoginPromptContext);
  if (!value) {
    throw new Error("useLoginPrompt must be used within LoginPromptProvider");
  }
  return value;
}
