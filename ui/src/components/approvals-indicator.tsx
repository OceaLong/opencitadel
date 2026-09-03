"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";
import { ClipboardCheck } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

import { approvalsApi } from "@/lib/api/approvals";
import { cn } from "@/lib/utils";
import { useAuth } from "@/providers/auth-provider";

/** 角标轮询间隔（毫秒）。 */
const POLL_INTERVAL_MS = 60_000;
/** 单次拉取上限：只用于角标计数，达到上限显示 9+。 */
const COUNT_FETCH_LIMIT = 10;

/**
 * 顶栏"待我审批"入口：铃铛旁的图标按钮，带待审批数量角标（60s 轮询），
 * 点击进入 /approvals 收件箱。路由变化时立即刷新一次，保证决策后角标及时回落。
 */
export function ApprovalsIndicator({ className }: { className?: string }) {
  const t = useTranslations("approvals");
  const pathname = usePathname();
  const { user, loading: authLoading } = useAuth();
  const [pendingCount, setPendingCount] = useState(0);

  useEffect(() => {
    // 未登录时组件不渲染（下方 return null），无需在此重置计数。
    if (authLoading || !user) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const data = await approvalsApi.list(
          { status: "pending", limit: COUNT_FETCH_LIMIT },
          { skipAuthRefresh: true, skipAuthRedirect: true },
        );
        if (!cancelled) setPendingCount(data.items.length);
      } catch {
        // 静默失败：角标只是辅助提示，不打断当前页面。
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [authLoading, user, pathname]);

  if (authLoading || !user) return null;

  return (
    <Button
      variant="outline"
      size="icon-sm"
      className={cn("relative", className)}
      aria-label={t("indicatorLabel")}
      title={t("indicatorLabel")}
      asChild
    >
      <Link href="/approvals">
        <ClipboardCheck className="size-4" />
        {pendingCount > 0 && (
          <Badge
            variant="destructive"
            className="text-2xs absolute -top-1 -right-1 flex size-4 items-center justify-center rounded-full p-0"
          >
            {pendingCount > 9 ? "9+" : pendingCount}
          </Badge>
        )}
      </Link>
    </Button>
  );
}
