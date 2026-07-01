"use client";

import { useEffect, useState } from "react";

import { teamApi, type Team } from "@/lib/api/team";

export function WorkspaceSwitcher() {
  const [teams, setTeams] = useState<Team[]>([]);
  const [active, setActive] = useState("");

  useEffect(() => {
    setActive(window.localStorage.getItem("my-manus-active-workspace") || "");
    void teamApi.list().then((data) => setTeams(data.teams)).catch(() => setTeams([]));
  }, []);

  function change(value: string) {
    setActive(value);
    window.localStorage.setItem("my-manus-active-workspace", value);
    window.location.reload();
  }

  return (
    <select
      className="border-input bg-background mb-3 h-9 w-full rounded-md border px-2 text-sm"
      value={active}
      onChange={(event) => change(event.target.value)}
      aria-label="工作区"
    >
      <option value="">个人工作区</option>
      {teams.map((team) => (
        <option key={team.id} value={team.id}>
          {team.name}
        </option>
      ))}
    </select>
  );
}
