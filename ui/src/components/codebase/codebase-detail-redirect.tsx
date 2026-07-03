"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { IconLoading } from "@/lib/icons";
import { sessionApi } from "@/lib/api/session";

export function CodebaseDetailRedirect({ codebaseId }: { codebaseId: string }) {
  const router = useRouter();
  const t = useTranslations("redirect");

  useEffect(() => {
    void (async () => {
      try {
        const data = await sessionApi.createSession({ codebase_id: codebaseId, mode: "ask" });
        router.replace(`/sessions/${data.session_id}`);
      } catch (err) {
        toast.error(err instanceof Error ? err.message : t("startTaskFailed"));
        router.replace("/codebase");
      }
    })();
  }, [codebaseId, router, t]);

  return (
    <div className="text-muted-foreground flex h-full flex-col items-center justify-center gap-2 text-sm">
      <IconLoading className="size-5 animate-spin" />
      {t("openingCodebaseTask")}
    </div>
  );
}
