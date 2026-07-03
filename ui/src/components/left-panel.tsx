"use client";

import { usePathname, useRouter } from "next/navigation";
import { useTranslations } from "next-intl";

import { SessionList } from "@/components/session-list";
import { AccountMenu } from "@/components/account-menu";
import { Button } from "@/components/ui/button";
import { Kbd, KbdGroup } from "@/components/ui/kbd";
import { Sidebar, SidebarContent, SidebarHeader, SidebarTrigger } from "@/components/ui/sidebar";
import { WorkspaceSwitcher } from "@/components/workspace-switcher";
import { useLoginPrompt } from "@/providers/login-prompt-provider";
import { useAuth } from "@/providers/auth-provider";
import { IconAdd } from "@/lib/icons";

export function LeftPanel() {
  const router = useRouter();
  const pathname = usePathname();
  const t = useTranslations("leftPanel");
  const { user } = useAuth();
  const { promptLogin } = useLoginPrompt();

  if (pathname.startsWith("/marketplace")) {
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
        <Button
          variant="outline"
          className="mb-3 cursor-pointer"
          onClick={() => {
            if (!user) {
              promptLogin(t("loginRequired"));
              return;
            }
            router.push("/");
          }}
        >
          <IconAdd />
          {t("newTask")}
          <KbdGroup>
            <Kbd>⌘</Kbd>
            <Kbd>K</Kbd>
          </KbdGroup>
        </Button>
        {/* 会话列表 */}
        <SessionList />
      </SidebarContent>
      <AccountMenu />
    </Sidebar>
  );
}
