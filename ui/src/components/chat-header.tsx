"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { Code2, LayoutGrid } from "lucide-react";
import type { CSSProperties } from "react";

import { ManusIcon } from "@/components/manus-icon";

const ManusSettings = dynamic(
  () => import("@/components/manus-settings").then((mod) => mod.ManusSettings),
  { ssr: false },
);
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { SidebarTrigger, useSidebar } from "@/components/ui/sidebar";

export function ChatHeader() {
  const { open, isMobile } = useSidebar();

  return (
    <header className="z-50 flex w-full items-center justify-between px-4 py-2">
      <div className="flex items-center gap-2">
        {(!open || isMobile) && <SidebarTrigger className="cursor-pointer" />}
        <Link
          href="/"
          className="border-border/60 bg-card text-foreground hover:bg-muted/60 flex h-9 items-center gap-2 rounded-xl border px-3 shadow-[var(--shadow-card)] transition-colors"
          style={{ "--logo-color": "currentColor" } as CSSProperties}
          aria-label="返回首页"
        >
          <ManusIcon />
          <span className="sr-only">MyManus</span>
        </Link>
      </div>
      <div className="flex items-center gap-1">
        <Button variant="outline" size="icon-sm" asChild aria-label="代码知识库" title="代码知识库">
          <Link href="/codebase">
            <Code2 className="size-4" />
          </Link>
        </Button>
        <Button variant="outline" size="icon-sm" asChild aria-label="应用市场" title="应用市场">
          <Link href="/marketplace">
            <LayoutGrid className="size-4" />
          </Link>
        </Button>
        <ThemeToggle />
        <ManusSettings />
      </div>
    </header>
  );
}
