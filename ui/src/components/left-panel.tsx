"use client";

import { usePathname, useRouter } from "next/navigation";
import { Plus } from "lucide-react";

import { SessionList } from "@/components/session-list";
import { Button } from "@/components/ui/button";
import { Kbd, KbdGroup } from "@/components/ui/kbd";
import { Sidebar, SidebarContent, SidebarHeader, SidebarTrigger } from "@/components/ui/sidebar";
import { WorkspaceSwitcher } from "@/components/workspace-switcher";

export function LeftPanel() {
  const router = useRouter();
  const pathname = usePathname();

  if (
    pathname.startsWith("/marketplace") ||
    pathname.startsWith("/q/") ||
    pathname.startsWith("/room/") ||
    pathname.startsWith("/share/")
  ) {
    return null;
  }

  return (
    <Sidebar>
      {/* 顶部的切换按钮 */}
      <SidebarHeader>
        <SidebarTrigger className="cursor-pointer" />
      </SidebarHeader>
      {/* 中间内容 */}
      <SidebarContent className="p-2">
        <WorkspaceSwitcher />
        {/* 新建任务 */}
        <Button variant="outline" className="mb-3 cursor-pointer" onClick={() => router.push("/")}>
          <Plus />
          新建任务
          <KbdGroup>
            <Kbd>⌘</Kbd>
            <Kbd>K</Kbd>
          </KbdGroup>
        </Button>
        {/* 会话列表 */}
        <SessionList />
      </SidebarContent>
    </Sidebar>
  );
}
