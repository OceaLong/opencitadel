"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

import { useAuth } from "@/components/auth-provider";

import { api, errorMessage } from "@/lib/api-client";

type User = { id: string; email: string; username: string; globalRole: string; status: string };
type Team = { id: string; name: string; archivedAt?: string | null };
type Audit = { id: string; action: string; resourceType: string; resourceId: string; actorUserId?: string; createdAt: string };
type Policy = { generation: number; digest: string; policy: Record<string, unknown>; note: string };

export default function AdminPage() {
  const { user } = useAuth();
  const [users, setUsers] = useState<User[]>([]);
  const [teams, setTeams] = useState<Team[]>([]);
  const [audit, setAudit] = useState<Audit[]>([]);
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [policyText, setPolicyText] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const values = await Promise.all([
        api<User[]>("/admin/users"), api<Team[]>("/admin/teams"),
        api<Audit[]>("/admin/audit"), api<Policy>("/governance-policy"),
      ]);
      setUsers(values[0]); setTeams(values[1]); setAudit(values[2]);
      setPolicy(values[3]); setPolicyText(JSON.stringify(values[3].policy, null, 2)); setError("");
    } catch (caught) { setError(errorMessage(caught)); }
  }, []);
  useEffect(() => { if (user?.globalRole === "admin") void load(); }, [load, user]);

  async function updateUser(item: User, patch: object) {
    try { await api(`/admin/users/${item.id}`, { method: "PATCH", json: patch }); await load(); }
    catch (caught) { setError(errorMessage(caught)); }
  }

  async function savePolicy(event: FormEvent) {
    event.preventDefault();
    if (!policy) return;
    try {
      await api("/governance-policy", { method: "PUT", json: {
        expectedGeneration: policy.generation, note: "Updated from governance console", policy: JSON.parse(policyText),
      } });
      await load();
    } catch (caught) { setError(errorMessage(caught)); }
  }

  if (user?.globalRole !== "admin") return <p className="error">仅管理员可访问治理后台。</p>;
  return <div className="stack">
    <header className="page-header"><div><h1>治理后台</h1><p className="muted">身份、团队、配额、策略和审计共用一套明确的管理边界。</p></div><button className="secondary" onClick={() => void load()}>刷新</button></header>
    {error ? <p className="error">{error}</p> : null}
    <section className="card stack"><h2>用户</h2><table><thead><tr><th>用户</th><th>角色</th><th>状态</th><th>操作</th></tr></thead><tbody>{users.map((item) => <tr key={item.id}><td>{item.username}<br /><small className="muted">{item.email}</small></td><td>{item.globalRole}</td><td>{item.status}</td><td><div className="actions"><button className="secondary" onClick={() => void updateUser(item, { enabled: item.status !== "active" })}>{item.status === "active" ? "停用" : "启用"}</button><select value={item.globalRole} onChange={(event) => void updateUser(item, { globalRole: event.target.value })}><option value="user">user</option><option value="auditor">auditor</option><option value="admin">admin</option></select></div></td></tr>)}</tbody></table></section>
    <section className="card stack"><h2>治理策略</h2>{policy ? <p className="muted">Generation {policy.generation} · {policy.digest}</p> : null}<form className="stack" onSubmit={savePolicy}><textarea value={policyText} onChange={(event) => setPolicyText(event.target.value)} /><button>CAS 更新策略</button></form></section>
    <section className="grid"><article className="card"><h2>团队</h2>{teams.map((item) => <p key={item.id}>{item.name} <small className="muted">{item.id}</small></p>)}</article><article className="card"><h2>审计链</h2>{audit.map((item) => <p key={item.id}><strong>{item.action}</strong><br /><small className="muted">{item.resourceType}/{item.resourceId} · {new Date(item.createdAt).toLocaleString()}</small></p>)}</article></section>
  </div>;
}
