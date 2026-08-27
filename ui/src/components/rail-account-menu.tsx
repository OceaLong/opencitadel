"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import { LogIn, LogOut, Users } from "lucide-react";

import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

import { useAuth } from "@/providers/auth-provider";
import { useLoginPrompt } from "@/providers/login-prompt-provider";

function initials(name: string): string {
  const trimmed = name.trim();
  return trimmed ? trimmed.slice(0, 1).toUpperCase() : "U";
}

export function RailAccountMenu() {
  const { user, logout } = useAuth();
  const { promptLogin } = useLoginPrompt();
  const tAccount = useTranslations("account");
  const tAuth = useTranslations("auth");
  const tCommon = useTranslations("common");

  if (!user) {
    return (
      <button
        type="button"
        className="text-muted-foreground hover:bg-muted/70 hover:text-foreground flex size-9 items-center justify-center rounded-lg transition-colors"
        aria-label={tAuth("loginRegister")}
        title={tAuth("loginRegister")}
        onClick={() => promptLogin()}
      >
        <LogIn className="size-4" />
      </button>
    );
  }

  const displayName = user.display_name || user.username || user.email || tCommon("user");

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className="hover:ring-ring flex items-center justify-center rounded-full transition-shadow hover:ring-2"
          aria-label={displayName}
        >
          <Avatar className="size-8">
            {user.avatar_url ? <AvatarImage src={user.avatar_url} alt={displayName} /> : null}
            <AvatarFallback>{initials(displayName)}</AvatarFallback>
          </Avatar>
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent side="right" align="end" className="w-48">
        <DropdownMenuLabel className="truncate">{displayName}</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {/* Admin 与设置的一级入口在 rail 底部图标上，头像菜单只保留身份域条目，避免双入口冗余 */}
        <DropdownMenuItem asChild>
          <Link href="/teams" className="cursor-pointer">
            <Users className="size-4" />
            {tAccount("teams")}
          </Link>
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => void logout()}>
          <LogOut className="size-4" />
          {tAuth("logout")}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
