"use client";

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
    setActive(readLocalStorageKey(LEGACY_ACTIVE_WORKSPACE_KEY, ACTIVE_WORKSPACE_KEY));
    void teamApi.list().then((data) => setTeams(data.teams)).catch(() => setTeams([]));
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
    <select
      className="border-input bg-background mb-3 h-9 w-full rounded-md border px-2 text-sm"
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
  );
}
