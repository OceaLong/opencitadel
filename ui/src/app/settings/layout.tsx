"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ArrowLeft, Brain, Cpu, Plug, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";

import { cn } from "@/lib/utils";

const NAV = [
  { href: "/settings/models", label: "模型管理", icon: Cpu },
  { href: "/settings/skills", label: "Skill 模板", icon: Sparkles },
  { href: "/settings/memory", label: "长期记忆", icon: Brain },
  { href: "/settings/integrations", label: "协议集成", icon: Plug },
];

export default function SettingsLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  return (
    <div className="bg-background flex min-h-full flex-col">
      <header className="border-border/70 bg-card/80 flex items-center gap-4 border-b px-6 py-4">
        <Button variant="ghost" size="sm" asChild>
          <Link href="/">
            <ArrowLeft className="mr-1 size-4" />
            返回
          </Link>
        </Button>
        <h1 className="text-lg font-semibold tracking-tight">设置中心</h1>
      </header>
      <div className="flex flex-1">
        <nav className="border-border/70 bg-card/40 w-56 shrink-0 space-y-1.5 border-r p-4">
          {NAV.map(({ href, label, icon: Icon }) => (
            <Link
              key={href}
              href={href}
              className={cn(
                "flex items-center gap-2 rounded-xl px-3 py-2.5 text-sm transition-colors",
                pathname === href
                  ? "bg-primary text-primary-foreground shadow-[var(--shadow-card)]"
                  : "text-muted-foreground hover:bg-muted/70 hover:text-foreground",
              )}
            >
              <Icon className="size-4" />
              {label}
            </Link>
          ))}
        </nav>
        <main className="flex-1 overflow-auto p-6">
          <div className="mx-auto w-full max-w-6xl">{children}</div>
        </main>
      </div>
    </div>
  );
}
