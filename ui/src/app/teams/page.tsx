"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { api, errorMessage, WORKSPACE_KEY } from "@/lib/api-client";

type Team = { id: string; name: string; description: string; role?: string; archivedAt?: string | null };

export default function TeamsPage() {
  const router = useRouter();
  const [teams, setTeams] = useState<Team[]>([]);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try { setTeams(await api<Team[]>("/teams")); setError(""); }
    catch (caught) { setError(errorMessage(caught)); }
  }, []);
  useEffect(() => void load(), [load]);

  async function create(event: FormEvent) {
    event.preventDefault();
    try {
      await api("/teams", { method: "POST", json: { name, description } });
      setName(""); setDescription(""); await load();
    } catch (caught) { setError(errorMessage(caught)); }
  }

  async function invite(team: Team) {
    const email = window.prompt(`邀请成员加入 ${team.name}：请输入邮箱`);
    if (!email) return;
    try {
      const value = await api<{ token: string }>(`/teams/${team.id}/invitations`, { method: "POST", json: { email, role: "member" } });
      await navigator.clipboard.writeText(`${window.location.origin}/register?token=${value.token}`);
      window.alert("邀请链接已复制。令牌只在创建时返回一次。 ");
    } catch (caught) { setError(errorMessage(caught)); }
  }

  return <div className="stack">
    <header className="page-header"><div><h1>团队</h1><p className="muted">团队工作区共享 Run、知识、推理配置与配额边界。</p></div><button className="secondary" onClick={() => void load()}>刷新</button></header>
    {error ? <p className="error">{error}</p> : null}
    <section className="card"><form className="grid" onSubmit={create}><label>名称<input required value={name} onChange={(event) => setName(event.target.value)} /></label><label>描述<input value={description} onChange={(event) => setDescription(event.target.value)} /></label><button>创建团队</button></form></section>
    <section className="grid">{teams.map((team) => <article className="card stack" key={team.id}><div className="row"><h2>{team.name}</h2><span className="pill">{team.role || "member"}</span></div><p className="muted">{team.description || "无描述"}</p><div className="actions"><button onClick={() => { window.localStorage.setItem(WORKSPACE_KEY, team.id); router.push("/"); }}>进入工作区</button><button className="secondary" onClick={() => void invite(team)}>邀请</button></div></article>)}</section>
  </div>;
}
