"use client";

import { FormEvent, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";

import { useAuth } from "@/components/auth-provider";

import { api, errorMessage } from "@/lib/api-client";

export default function RegisterPage() {
  const params = useSearchParams();
  const router = useRouter();
  const { refresh } = useAuth();
  const [token, setToken] = useState(params.get("token") || "");
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    try {
      await api("/auth/register", {
        method: "POST",
        json: { invitationToken: token, email, username, displayName, password },
      });
      await refresh();
      router.replace("/");
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  return <main className="centered"><section className="auth-card card stack">
    <div><h1>邀请注册</h1><p className="muted">账户创建和团队加入使用同一枚一次性邀请。</p></div>
    {error ? <p className="error">{error}</p> : null}
    <form onSubmit={submit}>
      <label>邀请令牌<input minLength={32} required value={token} onChange={(event) => setToken(event.target.value)} /></label>
      <label>邮箱<input type="email" required value={email} onChange={(event) => setEmail(event.target.value)} /></label>
      <label>用户名<input minLength={2} required value={username} onChange={(event) => setUsername(event.target.value)} /></label>
      <label>显示名称<input value={displayName} onChange={(event) => setDisplayName(event.target.value)} /></label>
      <label>密码<input autoComplete="new-password" minLength={12} required type="password" value={password} onChange={(event) => setPassword(event.target.value)} /></label>
      <button>创建账户</button>
    </form>
    <Link className="muted" href="/login">返回登录</Link>
  </section></main>;
}
