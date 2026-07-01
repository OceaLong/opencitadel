"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { adminApi, type AdminUser, type AuditLog } from "@/lib/api/admin";
import { useAuth } from "@/providers/auth-provider";

export default function AdminPage() {
  const { user } = useAuth();
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [audit, setAudit] = useState<AuditLog[]>([]);
  const [usage, setUsage] = useState<Record<string, number>>({});
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteUrl, setInviteUrl] = useState("");

  useEffect(() => {
    if (user?.global_role !== "admin") return;
    void adminApi.users().then((data) => setUsers(data.users));
    void adminApi.audit().then((data) => setAudit(data.logs));
    void adminApi.usage().then(setUsage);
  }, [user]);

  async function invite() {
    const data = await adminApi.invite(inviteEmail);
    setInviteUrl(data.url);
  }

  if (user?.global_role !== "admin") {
    return <main className="p-6 text-sm">需要管理员权限。</main>;
  }

  return (
    <main className="h-screen overflow-auto p-6">
      <h1 className="text-2xl font-semibold">管理员看板</h1>

      <section className="mt-6 grid gap-3 md:grid-cols-4">
        <Stat label="Prompt Tokens" value={usage.prompt_tokens || 0} />
        <Stat label="Completion Tokens" value={usage.completion_tokens || 0} />
        <Stat label="Total Tokens" value={usage.total_tokens || 0} />
        <Stat label="Calls" value={usage.call_count || 0} />
      </section>

      <section className="mt-8 rounded-lg border p-4">
        <h2 className="font-medium">平台邀请</h2>
        <div className="mt-3 flex gap-2">
          <Input value={inviteEmail} onChange={(event) => setInviteEmail(event.target.value)} placeholder="user@example.com" />
          <Button onClick={invite}>生成邀请链接</Button>
        </div>
        {inviteUrl && <p className="mt-2 break-all text-sm text-muted-foreground">{inviteUrl}</p>}
      </section>

      <section className="mt-8 rounded-lg border p-4">
        <h2 className="font-medium">用户</h2>
        <div className="mt-3 space-y-2">
          {users.map((item) => (
            <div key={item.id} className="flex items-center justify-between rounded border p-2 text-sm">
              <span>{item.email}</span>
              <span>{item.global_role} / {item.status}</span>
            </div>
          ))}
        </div>
      </section>

      <section className="mt-8 rounded-lg border p-4">
        <h2 className="font-medium">审计日志</h2>
        <a className="text-primary mt-2 inline-block text-sm underline" href="/api/admin/audit/export">导出 CSV</a>
        <div className="mt-3 space-y-2">
          {audit.map((item) => (
            <div key={item.id} className="rounded border p-2 text-xs">
              {item.created_at} {item.actor_user_id || "system"} {item.action} {item.resource_type}:{item.resource_id}
            </div>
          ))}
        </div>
      </section>
    </main>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg border p-4">
      <div className="text-muted-foreground text-sm">{label}</div>
      <div className="mt-1 text-2xl font-semibold">{value}</div>
    </div>
  );
}
