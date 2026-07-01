"use client";

import { useEffect, useState } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { BookOpen, Code2, LayoutGrid } from "lucide-react";
import type { CSSProperties } from "react";

import { ManusIcon } from "@/components/manus-icon";
import { Badge } from "@/components/ui/badge";

import { isModelUnavailableStatus, llmStatusApi } from "@/lib/api/llm-status";
import type { LLMStatusData } from "@/lib/api/types";

const ManusSettings = dynamic(
  () => import("@/components/manus-settings").then((mod) => mod.ManusSettings),
  { ssr: false },
);
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { SidebarTrigger, useSidebar } from "@/components/ui/sidebar";

function ChatHeaderSidebarTrigger() {
  const { open, isMobile } = useSidebar();

  if (open && !isMobile) return null;

  return <SidebarTrigger className="cursor-pointer" />;
}

export function ChatHeader({ showSidebarTrigger = true }: { showSidebarTrigger?: boolean }) {
  const [llmStatus, setLlmStatus] = useState<LLMStatusData["status"]>("unknown");

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      try {
        const data = await llmStatusApi.getStatus();
        if (mounted) setLlmStatus(data.status ?? "unknown");
      } catch {
        if (mounted) setLlmStatus("unknown");
      }
    };
    void load();
    const timer = setInterval(load, isModelUnavailableStatus(llmStatus) ? 10_000 : 30_000);
    return () => {
      mounted = false;
      clearInterval(timer);
    };
  }, [llmStatus]);

  return (
    <header className="z-50 flex w-full items-center justify-between px-4 py-2">
      <div className="flex items-center gap-2">
        {showSidebarTrigger && <ChatHeaderSidebarTrigger />}
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
        <Badge
          variant={isModelUnavailableStatus(llmStatus) ? "destructive" : "secondary"}
          className="hidden text-[10px] sm:inline-flex"
        >
          模型{llmStatus === "unknown" ? "状态未知" : isModelUnavailableStatus(llmStatus) ? "不可用" : "正常"}
        </Badge>
        <Button variant="outline" size="icon-sm" asChild aria-label="代码知识库" title="代码知识库">
          <Link href="/codebase">
            <Code2 className="size-4" />
          </Link>
        </Button>
        <Button variant="outline" size="icon-sm" asChild aria-label="文档知识库" title="文档知识库">
          <Link href="/knowledge">
            <BookOpen className="size-4" />
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
