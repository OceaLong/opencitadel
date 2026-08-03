"use client";

import { use, useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { PatrolRunDetailView } from "@/components/patrol/patrol-run-detail";
import { ScrollablePageContent } from "@/components/scrollable-page-content";
import { Skeleton } from "@/components/ui/skeleton";

import { patrolsApi } from "@/lib/api/patrols";
import type { PatrolRunDetail } from "@/lib/api/types";
import { useAuth } from "@/providers/auth-provider";

export default function PatrolRunPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const t = useTranslations("patrol");
  const { user } = useAuth();
  const [run, setRun] = useState<PatrolRunDetail | null>(null);
  const load = useCallback(async () => {
    try {
      setRun(await patrolsApi.getRun(id));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("errors.runLoad"));
    }
  }, [id, t]);
  useEffect(() => {
    let active = true;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const next = await patrolsApi.getRun(id);
        if (!active) return;
        setRun(next);
        if (["queued", "running"].includes(next.status)) {
          timer = window.setTimeout(() => void poll(), 3000);
        }
      } catch (error) {
        if (active) toast.error(error instanceof Error ? error.message : t("errors.runLoad"));
      }
    };
    timer = window.setTimeout(() => void poll(), 0);
    return () => {
      active = false;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [id, t]);
  if (!run)
    return (
      <ScrollablePageContent>
        <div className="grid gap-3 p-6">
          <Skeleton className="h-44" />
          <Skeleton className="h-72" />
        </div>
      </ScrollablePageContent>
    );
  return (
    <ScrollablePageContent>
      <div className="p-4 sm:p-6">
        <PatrolRunDetailView
          run={run}
          readOnly={user?.global_role === "auditor"}
          onRefresh={() => void load()}
        />
      </div>
    </ScrollablePageContent>
  );
}
