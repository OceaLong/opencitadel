"use client";

import { FormEvent, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

import { authApi } from "@/lib/api/auth";
import { useAuth } from "@/providers/auth-provider";

export default function RegisterPage() {
  const router = useRouter();
  const params = useSearchParams();
  const inviteToken = useMemo(() => params.get("invite_token") || "", [params]);
  const { refresh } = useAuth();
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError("");
    try {
      await authApi.register({ invite_token: inviteToken, email, username, password });
      await refresh();
      router.replace("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "注册失败");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="bg-background flex min-h-screen items-center justify-center p-6">
      <form onSubmit={onSubmit} className="border-border bg-card w-full max-w-sm space-y-4 rounded-xl border p-6 shadow-sm">
        <div>
          <h1 className="text-xl font-semibold">接受邀请</h1>
          <p className="text-muted-foreground mt-1 text-sm">使用管理员提供的邀请链接创建账号。</p>
        </div>
        {!inviteToken && <p className="text-destructive text-sm">缺少邀请 token。</p>}
        <Input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="邮箱" />
        <Input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="用户名" />
        <Input value={password} onChange={(e) => setPassword(e.target.value)} placeholder="密码（至少 8 位）" type="password" />
        {error && <p className="text-destructive text-sm">{error}</p>}
        <Button className="w-full" disabled={loading || !inviteToken}>{loading ? "注册中..." : "注册并登录"}</Button>
      </form>
    </main>
  );
}
