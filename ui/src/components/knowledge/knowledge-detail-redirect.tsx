"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { knowledgeApi } from "@/lib/api/knowledge";
import { sessionApi } from "@/lib/api/session";
import { IconLoading } from "@/lib/icons";

export function KnowledgeDetailRedirect({ knowledgeBaseId }: { knowledgeBaseId: string }) {
  const router = useRouter();
  const t = useTranslations("redirect");

  useEffect(() => {
    void (async () => {
      try {
        const knowledgeBase = await knowledgeApi.get(knowledgeBaseId);
        if (!knowledgeBase.active_version_id) {
          throw new Error(t("noActiveVersion"));
        }
        const data = await sessionApi.createSession({
          knowledge_base_id: knowledgeBaseId,
          knowledge_base_version_id: knowledgeBase.active_version_id,
          mode: "ask",
        });
        router.replace(`/sessions/${data.session_id}`);
      } catch (err) {
        toast.error(err instanceof Error ? err.message : t("startTaskFailed"));
        router.replace("/knowledge");
      }
    })();
  }, [knowledgeBaseId, router, t]);

  return (
    <div className="text-muted-foreground flex h-full flex-col items-center justify-center gap-2 text-sm">
      <IconLoading className="size-5 animate-spin" />
      {t("openingKnowledgeTask")}
    </div>
  );
}
