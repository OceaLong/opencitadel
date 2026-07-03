"use client";

import Link from "next/link";
import { LayoutDashboard, LogIn, LogOut, Settings } from "lucide-react";
import { useTranslations } from "next-intl";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { SidebarFooter } from "@/components/ui/sidebar";

import { cn } from "@/lib/utils";
import { useAuth } from "@/providers/auth-provider";
import { useLoginPrompt } from "@/providers/login-prompt-provider";
import { useSettingsDialog } from "@/providers/settings-dialog-provider";

function getDisplayName(
  user: { display_name?: string; username?: string; email?: string },
  fallback: string,
): string {
  return user.display_name || user.username || user.email || fallback;
}

function getInitials(name: string): string {
  const trimmed = name.trim();
  if (!trimmed) return "U";
  return trimmed.slice(0, 1).toUpperCase();
}

function AccountMenuItem({
  children,
  className,
  onClick,
  href,
}: {
  children: ReactNode;
  className?: string;
  onClick?: () => void;
  href?: string;
}) {
  const itemClassName = cn(
    "flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition-colors hover:bg-accent [&_svg]:size-4 [&_svg]:shrink-0 [&_svg]:text-muted-foreground",
    className,
  );

  if (href) {
    return (
      <Link href={href} className={itemClassName} onClick={onClick}>
        {children}
      </Link>
    );
  }

  return (
    <button type="button" className={itemClassName} onClick={onClick}>
      {children}
    </button>
  );
}

export function AccountMenu() {
  const { user, logout } = useAuth();
  const { promptLogin } = useLoginPrompt();
  const { openSettings } = useSettingsDialog();
  const tAccount = useTranslations("account");
  const tAuth = useTranslations("auth");
  const tCommon = useTranslations("common");
  const [open, setOpen] = useState(false);
  const footerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;

    const handleClickOutside = (event: MouseEvent) => {
      if (footerRef.current && !footerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };

    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [open]);

  const closeMenu = () => setOpen(false);

  if (!user) {
    return (
      <SidebarFooter className="p-2">
        <button
          type="button"
          className="flex w-full items-center justify-center gap-2 rounded-xl bg-muted/50 px-2.5 py-2 text-sm transition-colors hover:bg-muted/80"
          onClick={() => promptLogin()}
        >
          <LogIn className="size-4 text-muted-foreground" />
          {tAuth("loginRegister")}
        </button>
      </SidebarFooter>
    );
  }

  const displayName = getDisplayName(user, tCommon("user"));

  return (
    <SidebarFooter ref={footerRef} className="p-2">
      {open ? (
        <div className="bg-popover mb-1 animate-in fade-in-0 slide-in-from-bottom-2 rounded-xl border py-1 shadow-sm duration-200">
          {user.global_role === "admin" ? (
            <AccountMenuItem href="/admin" onClick={closeMenu}>
              <LayoutDashboard />
              {tAccount("adminPanel")}
            </AccountMenuItem>
          ) : null}
          <AccountMenuItem
            onClick={() => {
              closeMenu();
              openSettings("common-setting");
            }}
          >
            <Settings />
            {tAccount("settings")}
          </AccountMenuItem>
          <AccountMenuItem
            onClick={() => {
              closeMenu();
              void logout();
            }}
          >
            <LogOut />
            {tAuth("logout")}
          </AccountMenuItem>
        </div>
      ) : null}
      <button
        type="button"
        className="flex w-full items-center gap-2.5 rounded-xl bg-muted/50 px-2.5 py-2 transition-colors hover:bg-muted/80"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
      >
        <Avatar>
          {user.avatar_url ? <AvatarImage src={user.avatar_url} alt={displayName} /> : null}
          <AvatarFallback>{getInitials(displayName)}</AvatarFallback>
        </Avatar>
        <span className="min-w-0 flex-1 truncate text-left text-sm font-medium">{displayName}</span>
      </button>
    </SidebarFooter>
  );
}
