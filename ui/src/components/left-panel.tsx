"use client";

import { useRouter } from "next/navigation";
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
  const t = useTranslations("leftPanel");
  const { user } = useAuth();
  const { promptLogin } = useLoginPrompt();

  return (
    <Sidebar>
      <SidebarHeader>
        <SidebarTrigger className="cursor-pointer" />
      </SidebarHeader>
      <SidebarContent className="p-2">
        <WorkspaceSwitcher />
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
          <KbdGroup className="hidden md:inline-flex">
            <Kbd>⌘</Kbd>
            <Kbd>K</Kbd>
          </KbdGroup>
        </Button>
        <SessionList />
      </SidebarContent>
      <AccountMenu />
    </Sidebar>
  );
}
