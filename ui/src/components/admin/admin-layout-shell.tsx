"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ArrowLeft, ClipboardList, LayoutDashboard, MailPlus, Users } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";

import { cn } from "@/lib/utils";
import { useAuth } from "@/providers/auth-provider";

const NAV = [
  { href: "/admin", label: "概览", icon: LayoutDashboard, exact: true },
  { href: "/admin/users", label: "用户", icon: Users },
  { href: "/admin/invitations", label: "邀请", icon: MailPlus },
  { href: "/admin/audit", label: "审计", icon: ClipboardList },
];

export function AdminLayoutShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div className="bg-background flex min-h-screen items-center justify-center text-sm text-muted-foreground">
        加载中...
      </div>
    );
  }

  if (user?.global_role !== "admin") {
    return (
      <div className="bg-background flex min-h-screen flex-col items-center justify-center gap-4 p-6">
        <p className="text-muted-foreground text-sm">需要管理员权限才能访问后台。</p>
        <Button variant="outline" asChild>
          <Link href="/">返回首页</Link>
        </Button>
      </div>
    );
  }

  return (
    <div className="bg-background flex min-h-screen flex-col">
      <header className="border-border/70 bg-card/80 flex items-center gap-4 border-b px-6 py-4">
        <Button variant="ghost" size="sm" asChild>
          <Link href="/">
            <ArrowLeft className="mr-1 size-4" />
            返回
          </Link>
        </Button>
        <div>
          <h1 className="text-lg font-semibold tracking-tight">后台管理</h1>
          <p className="text-muted-foreground text-xs">平台运营、用户与用量监控</p>
        </div>
      </header>
      <div className="flex flex-1">
        <nav className="border-border/70 bg-card/40 w-56 shrink-0 space-y-1.5 border-r p-4">
          {NAV.map(({ href, label, icon: Icon, exact }) => {
            const active = exact ? pathname === href : pathname.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                className={cn(
                  "flex items-center gap-2 rounded-xl px-3 py-2.5 text-sm transition-colors",
                  active
                    ? "bg-primary text-primary-foreground shadow-[var(--shadow-card)]"
                    : "text-muted-foreground hover:bg-muted/70 hover:text-foreground",
                )}
              >
                <Icon className="size-4" />
                {label}
              </Link>
            );
          })}
        </nav>
        <main className="flex-1 overflow-auto p-6">
          <div className="mx-auto w-full max-w-6xl">{children}</div>
        </main>
      </div>
    </div>
  );
}
