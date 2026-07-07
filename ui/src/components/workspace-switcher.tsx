"use client";

import { useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { Check, ChevronDown, User, Users } from "lucide-react";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

import { type Team, teamApi } from "@/lib/api/team";
import { ACTIVE_WORKSPACE_KEY, LEGACY_ACTIVE_WORKSPACE_KEY } from "@/lib/storage-keys";
import { readLocalStorageKey, writeLocalStorageKey } from "@/lib/storage-migration";
import { cn } from "@/lib/utils";
import { useAuth } from "@/providers/auth-provider";

export function WorkspaceSwitcher() {
  const { user } = useAuth();
  const t = useTranslations("workspace");
  const [teams, setTeams] = useState<Team[]>([]);
  const [active, setActive] = useState("");

  useEffect(() => {
    if (!user) return;
    const stored = readLocalStorageKey(LEGACY_ACTIVE_WORKSPACE_KEY, ACTIVE_WORKSPACE_KEY);
    void teamApi
      .list()
      .then((data) => {
        const teamIds = new Set(data.teams.map((team) => team.id));
        const nextActive = stored && teamIds.has(stored) ? stored : "";
        if (nextActive !== stored) {
          writeLocalStorageKey(LEGACY_ACTIVE_WORKSPACE_KEY, ACTIVE_WORKSPACE_KEY, "");
        }
        setActive(nextActive);
        setTeams(data.teams);
      })
      .catch(() => {
        setTeams([]);
        setActive("");
      });
  }, [user]);

  const activeTeam = useMemo(
    () => teams.find((team) => team.id === active),
    [active, teams],
  );
  const displayLabel = activeTeam?.name ?? t("personal");
  const TriggerIcon = activeTeam ? Users : User;

  if (!user) {
    return null;
  }

  function change(value: string) {
    setActive(value);
    writeLocalStorageKey(LEGACY_ACTIVE_WORKSPACE_KEY, ACTIVE_WORKSPACE_KEY, value);
    window.location.reload();
  }

  const options = [
    { id: "", label: t("personal"), icon: User },
    ...teams.map((team) => ({ id: team.id, label: team.name, icon: Users })),
  ];

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className="mb-3 flex w-full items-center gap-2.5 rounded-xl bg-muted/50 px-2.5 py-2 transition-colors hover:bg-muted/80"
          aria-label={t("label")}
        >
          <TriggerIcon className="size-4 shrink-0 text-muted-foreground" />
          <span className="min-w-0 flex-1 truncate text-left text-sm font-medium">{displayLabel}</span>
          <ChevronDown className="size-4 shrink-0 opacity-60" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-[min(100vw-2rem,280px)] p-1.5">
        {options.map((option) => {
          const isSelected = active === option.id;
          const OptionIcon = option.icon;
          return (
            <button
              key={option.id || "personal"}
              type="button"
              className={cn(
                "hover:bg-muted flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left transition-colors",
                isSelected && "bg-muted/60",
              )}
              onClick={() => change(option.id)}
            >
              <OptionIcon className="size-4 shrink-0 text-muted-foreground" />
              <span className="min-w-0 flex-1 truncate text-sm font-medium">{option.label}</span>
              {isSelected ? <Check className="text-primary size-4 shrink-0" /> : null}
            </button>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
