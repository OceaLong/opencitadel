"use client";

import { type ReactNode, useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { Check, ChevronDown, User, Users } from "lucide-react";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

import { type Team, teamApi } from "@/lib/api/team";
import { cn } from "@/lib/utils";
import { useAuth } from "@/providers/auth-provider";
import { useClientDataScope } from "@/providers/client-data-provider";

export function WorkspaceSwitcher({ trigger }: { trigger?: ReactNode }) {
  const { user } = useAuth();
  const { scope, setWorkspaceId } = useClientDataScope();
  const t = useTranslations("workspace");
  const [teams, setTeams] = useState<Team[]>([]);
  const active = scope?.workspaceId ?? "";

  useEffect(() => {
    if (!user || !scope) return;
    void teamApi
      .list()
      .then((data) => {
        const teamIds = new Set(data.teams.map((team) => team.id));
        if (active && !teamIds.has(active)) {
          setWorkspaceId("");
        }
        setTeams(data.teams);
      })
      .catch(() => {
        setTeams([]);
      });
  }, [active, scope, setWorkspaceId, user]);

  const activeTeam = useMemo(() => teams.find((team) => team.id === active), [active, teams]);
  const displayLabel = activeTeam?.name ?? t("personal");
  const TriggerIcon = activeTeam ? Users : User;

  if (!user) {
    return null;
  }

  function change(value: string) {
    setWorkspaceId(value);
    window.location.reload();
  }

  const options = [
    { id: "", label: t("personal"), icon: User },
    ...teams.map((team) => ({ id: team.id, label: team.name, icon: Users })),
  ];

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        {trigger ?? (
          <button
            type="button"
            className="bg-muted/50 hover:bg-muted/80 mb-3 flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 transition-colors"
            aria-label={t("label")}
          >
            <TriggerIcon className="text-muted-foreground size-4 shrink-0" />
            <span className="min-w-0 flex-1 truncate text-left text-sm font-medium">
              {displayLabel}
            </span>
            <ChevronDown className="size-4 shrink-0 opacity-60" />
          </button>
        )}
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
              <OptionIcon className="text-muted-foreground size-4 shrink-0" />
              <span className="min-w-0 flex-1 truncate text-sm font-medium">{option.label}</span>
              {isSelected ? <Check className="text-primary size-4 shrink-0" /> : null}
            </button>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
