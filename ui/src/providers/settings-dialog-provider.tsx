"use client";

import dynamic from "next/dynamic";
import { createContext, useCallback, useContext, useMemo, useState } from "react";

import type { SettingTab } from "@/hooks/use-open-citadel-settings";

const SettingsDialog = dynamic(
  () =>
    import("@/components/open-citadel-settings").then((mod) => mod.SettingsDialog),
  { ssr: false },
);

type SettingsDialogContextValue = {
  openSettings: (tab?: SettingTab) => void;
  closeSettings: () => void;
  open: boolean;
};

const SettingsDialogContext = createContext<SettingsDialogContextValue | null>(null);

export function SettingsDialogProvider({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const [initialTab, setInitialTab] = useState<SettingTab>("common-setting");

  const openSettings = useCallback((tab: SettingTab = "common-setting") => {
    setInitialTab(tab);
    setOpen(true);
  }, []);

  const closeSettings = useCallback(() => {
    setOpen(false);
  }, []);

  const value = useMemo(
    () => ({ openSettings, closeSettings, open }),
    [openSettings, closeSettings, open],
  );

  return (
    <SettingsDialogContext.Provider value={value}>
      {children}
      {open ? (
        <SettingsDialog open={open} onOpenChange={setOpen} initialTab={initialTab} />
      ) : null}
    </SettingsDialogContext.Provider>
  );
}

export function useSettingsDialog() {
  const value = useContext(SettingsDialogContext);
  if (!value) {
    throw new Error("useSettingsDialog must be used within SettingsDialogProvider");
  }
  return value;
}
