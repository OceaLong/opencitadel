"use client";

import { memo, useCallback } from "react";
import {
  AlertCircle,
  MoreHorizontal,
} from "lucide-react";
import { useTranslations } from "next-intl";

import { Avatar, AvatarGroupCount } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Item, ItemActions, ItemContent, ItemDescription, ItemMedia } from "@/components/ui/item";

import type { Session } from "@/lib/api";
import {
  getSessionContextIcon,
  getSessionContextKind,
  IconDelete,
  IconLoading,
} from "@/lib/icons";
import { cn, formatRelativeDate } from "@/lib/utils";

type SessionItemProps = {
  session: Session;
  isActive: boolean;
  onClick: (sessionId: string) => void;
  onDelete: (session: Session) => void;
};

export const SessionItem = memo(function SessionItem({
  session,
  isActive,
  onClick,
  onDelete,
}: SessionItemProps) {
  const t = useTranslations("sessionList");
  const tCommon = useTranslations("common");
  const handleClick = useCallback(() => {
    onClick(session.session_id);
  }, [onClick, session.session_id]);

  const handleDelete = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      onDelete(session);
    },
    [onDelete, session],
  );

  const description = session.latest_message || tCommon("noMessages");
  const dateLabel = formatRelativeDate(session.latest_message_at);
  const isRunning = session.status === "running" || session.status === "waiting";
  const isFailed = session.status === "failed";
  const contextKind = getSessionContextKind(session);
  const ContextIcon = getSessionContextIcon(contextKind);
  const contextLabel = contextKind !== "general" ? t(`filter.${contextKind}`) : null;

  return (
    <Item
      className={cn(
        "hover:bg-muted/70 cursor-pointer items-start gap-2 rounded-xl p-2 transition-colors",
        isActive && "bg-card shadow-[var(--shadow-card)]",
      )}
      onClick={handleClick}
    >
      <ItemMedia>
        <Avatar className="size-8">
          <AvatarGroupCount>
            {isRunning ? (
              <IconLoading className="animate-spin" />
            ) : isFailed ? (
              <AlertCircle className="text-destructive" />
            ) : (
              <ContextIcon className="size-4" />
            )}
          </AvatarGroupCount>
        </Avatar>
      </ItemMedia>
      <ItemContent className="min-w-0 gap-0">
        <div className="flex items-center gap-1.5">
          <p className="truncate text-sm font-medium">{session.title || tCommon("newTask")}</p>
          {contextLabel && (
            <Badge variant="secondary" className="h-4 shrink-0 px-1 text-2xs">
              {contextLabel}
            </Badge>
          )}
        </div>
        <p className="text-muted-foreground truncate text-xs">{description}</p>
      </ItemContent>
      <ItemActions className="flex flex-col gap-0 self-start pt-0.5">
        {session.unread_message_count > 0 && (
          <span className="bg-primary text-primary-foreground mb-0.5 inline-flex min-w-5 items-center justify-center rounded-full px-1.5 text-2xs font-medium">
            {session.unread_message_count > 99 ? "99+" : session.unread_message_count}
          </span>
        )}
        <ItemDescription className="text-xs whitespace-nowrap">{dateLabel}</ItemDescription>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              size="icon-xs"
              variant="ghost"
              className="cursor-pointer"
              onClick={(e) => e.stopPropagation()}
            >
              <MoreHorizontal />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="center" side="bottom">
            <DropdownMenuItem
              variant="destructive"
              className="cursor-pointer"
              onClick={handleDelete}
            >
              <IconDelete />
              {tCommon("delete")}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </ItemActions>
    </Item>
  );
});
