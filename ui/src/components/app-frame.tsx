"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";

import { useAuth } from "@/components/auth-provider";

import { api, WORKSPACE_KEY } from "@/lib/api-client";
import { isActivePath, NAVIGATION } from "@/lib/navigation";

type Team = { id: string; name: string };
const PUBLIC_PREFIXES = ["/login", "/register", "/invitations/"];

export function AppFrame({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { user, loading, logout } = useAuth();
  const [teams, setTeams] = useState<Team[]>([]);
  const [workspace, setWorkspace] = useState("");
  const isPublic = PUBLIC_PREFIXES.some((prefix) => pathname.startsWith(prefix));

  useEffect(() => {
    if (!loading && !user && !isPublic) router.replace(`/login?next=${encodeURIComponent(pathname)}`);
  }, [isPublic, loading, pathname, router, user]);

  useEffect(() => {
    if (!user) return;
    setWorkspace(window.localStorage.getItem(WORKSPACE_KEY) || "");
    void api<Team[]>("/teams").then(setTeams).catch(() => setTeams([]));
  }, [user]);

  if (isPublic) return <>{children}</>;
  if (loading || !user) return <main className="centered">正在加载…</main>;

  function changeWorkspace(value: string) {
    setWorkspace(value);
    if (value) window.localStorage.setItem(WORKSPACE_KEY, value);
    else window.localStorage.removeItem(WORKSPACE_KEY);
    window.location.reload();
  }

  return (
    <div className="app-frame">
      <aside className="sidebar">
        <Link className="brand" href="/">OpenCitadel</Link>
        <p className="brand-subtitle">Governed Agent Kernel</p>
        <nav>
          {NAVIGATION.map((item) => (
            <Link className={isActivePath(pathname, item.href) ? "active" : ""} href={item.href} key={item.href}>
              {item.label}
            </Link>
          ))}
          {user.globalRole === "admin" ? (
            <Link className={pathname.startsWith("/admin") ? "active" : ""} href="/admin">治理后台</Link>
          ) : null}
        </nav>
        <div className="sidebar-footer">
          <label>
            工作区
            <select value={workspace} onChange={(event) => changeWorkspace(event.target.value)}>
              <option value="">个人</option>
              {teams.map((team) => <option value={team.id} key={team.id}>{team.name}</option>)}
            </select>
          </label>
          <small>{user.displayName || user.username}</small>
          <button className="secondary" onClick={() => void logout()}>退出</button>
        </div>
      </aside>
      <main className="workspace">{children}</main>
    </div>
  );
}
