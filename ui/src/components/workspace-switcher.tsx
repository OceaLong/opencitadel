"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";

import { teamApi, type Team } from "@/lib/api/team";
import { ACTIVE_WORKSPACE_KEY, LEGACY_ACTIVE_WORKSPACE_KEY } from "@/lib/storage-keys";
import { readLocalStorageKey, writeLocalStorageKey } from "@/lib/storage-migration";
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

  if (!user) {
    return null;
  }

  function change(value: string) {
    setActive(value);
    writeLocalStorageKey(LEGACY_ACTIVE_WORKSPACE_KEY, ACTIVE_WORKSPACE_KEY, value);
    window.location.reload();
  }

  return (
    <div className="mb-3 space-y-2">
      <select
        className="border-input bg-background h-9 w-full rounded-md border px-2 text-sm"
        value={active}
        onChange={(event) => change(event.target.value)}
        aria-label={t("label")}
      >
        <option value="">{t("personal")}</option>
        {teams.map((team) => (
          <option key={team.id} value={team.id}>
            {team.name}
          </option>
        ))}
      </select>
      <Link
        href="/teams"
        className="text-muted-foreground hover:text-foreground block text-xs underline-offset-4 hover:underline"
      >
        {t("manageTeams")}
      </Link>
    </div>
  );
}
