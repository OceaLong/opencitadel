"use client";

import { FormEvent, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";

import { useAuth } from "@/components/auth-provider";

import { api, errorMessage } from "@/lib/api-client";

export default function LoginPage() {
  const router = useRouter();
  const params = useSearchParams();
  const { refresh } = useAuth();
  const [identity, setIdentity] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    try {
      await api("/auth/login", { method: "POST", json: { email_or_username: identity, password } });
      await refresh();
      const next = params.get("next");
      router.replace(next?.startsWith("/") ? next : "/");
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  return <main className="centered"><section className="auth-card card stack">
    <div><h1>登录 OpenCitadel</h1><p className="muted">进入受治理的私有 Agent 工作区。</p></div>
    {error ? <p className="error">{error}</p> : null}
    <form onSubmit={submit}>
      <label>邮箱或用户名<input autoComplete="username" required value={identity} onChange={(event) => setIdentity(event.target.value)} /></label>
      <label>密码<input autoComplete="current-password" minLength={8} required type="password" value={password} onChange={(event) => setPassword(event.target.value)} /></label>
      <button>登录</button>
    </form>
    <p className="muted">注册仅接受邀请。<Link className="success" href="/register">使用邀请注册</Link></p>
  </section></main>;
}
