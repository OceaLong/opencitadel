"use client";

import Link from "next/link";
import { LayoutDashboard, LogIn, LogOut, Settings } from "lucide-react";

import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { SidebarFooter } from "@/components/ui/sidebar";

import { useAuth } from "@/providers/auth-provider";
import { useLoginPrompt } from "@/providers/login-prompt-provider";

function getDisplayName(user: {
  display_name?: string;
  username?: string;
  email?: string;
}): string {
  return user.display_name || user.username || user.email || "用户";
}

function getInitials(name: string): string {
  const trimmed = name.trim();
  if (!trimmed) return "U";
  return trimmed.slice(0, 1).toUpperCase();
}

export function AccountMenu() {
  const { user, logout } = useAuth();
  const { promptLogin } = useLoginPrompt();

  if (!user) {
    return (
      <SidebarFooter className="border-border/60 border-t p-2">
        <Button
          variant="outline"
          className="w-full justify-start gap-2"
          onClick={() => promptLogin()}
        >
          <LogIn className="size-4" />
          登录 / 注册
        </Button>
      </SidebarFooter>
    );
  }

  const displayName = getDisplayName(user);

  return (
    <SidebarFooter className="border-border/60 border-t p-2">
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" className="h-auto w-full justify-start gap-2 px-2 py-2">
            <Avatar size="sm">
              {user.avatar_url ? <AvatarImage src={user.avatar_url} alt={displayName} /> : null}
              <AvatarFallback>{getInitials(displayName)}</AvatarFallback>
            </Avatar>
            <div className="min-w-0 flex-1 text-left">
              <div className="truncate text-sm font-medium">{displayName}</div>
              <div className="text-muted-foreground truncate text-xs">{user.email}</div>
            </div>
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" side="top" className="w-56">
          {user.global_role === "admin" ? (
            <DropdownMenuItem asChild>
              <Link href="/admin">
                <LayoutDashboard className="size-4" />
                后台管理
              </Link>
            </DropdownMenuItem>
          ) : null}
          <DropdownMenuItem asChild>
            <Link href="/settings">
              <Settings className="size-4" />
              系统设置
            </Link>
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            variant="destructive"
            onClick={() => {
              void logout();
            }}
          >
            <LogOut className="size-4" />
            退出登录
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </SidebarFooter>
  );
}
