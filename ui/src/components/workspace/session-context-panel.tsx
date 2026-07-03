"use client";

import { useRef } from "react";
import { useTranslations } from "next-intl";

import { CodebaseContextPanel } from "@/components/workspace/codebase-context-panel";
import { KnowledgeContextPanel } from "@/components/workspace/knowledge-context-panel";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

type SessionContextPanelProps = {
  codebaseId?: string | null;
  knowledgeBaseId?: string | null;
  codeSourceRef?: React.MutableRefObject<((path: string, line?: number) => void) | null>;
  kbSourceRef?: React.MutableRefObject<((value: string) => void) | null>;
};

export function SessionContextPanel({
  codebaseId,
  knowledgeBaseId,
  codeSourceRef,
  kbSourceRef,
}: SessionContextPanelProps) {
  const t = useTranslations("workspaceContext");
  const hasCode = Boolean(codebaseId);
  const hasKb = Boolean(knowledgeBaseId);

  if (!hasCode && !hasKb) return null;

  const panel = hasCode && hasKb ? (
    <Tabs defaultValue="code" className="flex min-h-0 flex-1 flex-col">
      <TabsList className="mx-2 mt-2 grid w-auto grid-cols-2">
        <TabsTrigger value="code">{t("codebaseTab")}</TabsTrigger>
        <TabsTrigger value="kb">{t("knowledgeTab")}</TabsTrigger>
      </TabsList>
      <TabsContent value="code" className="min-h-0 flex-1 overflow-hidden">
        <CodebaseContextPanel codebaseId={codebaseId!} onSourceClickRef={codeSourceRef} />
      </TabsContent>
      <TabsContent value="kb" className="min-h-0 flex-1 overflow-hidden">
        <KnowledgeContextPanel knowledgeBaseId={knowledgeBaseId!} onSourceClickRef={kbSourceRef} />
      </TabsContent>
    </Tabs>
  ) : hasCode ? (
    <CodebaseContextPanel codebaseId={codebaseId!} onSourceClickRef={codeSourceRef} />
  ) : (
    <KnowledgeContextPanel knowledgeBaseId={knowledgeBaseId!} onSourceClickRef={kbSourceRef} />
  );

  return (
    <div className="border-border flex h-full w-96 shrink-0 flex-col border-l">
      {panel}
    </div>
  );
}

export function useSessionContextRefs() {
  const codeSourceRef = useRef<((path: string, line?: number) => void) | null>(null);
  const kbSourceRef = useRef<((value: string) => void) | null>(null);

  const handleTimelineSourceClick = (path: string, line?: number) => {
    if (path.startsWith("kbdoc://") || path.includes("kbdoc://")) {
      kbSourceRef.current?.(path);
      return;
    }
    codeSourceRef.current?.(path, line);
  };

  return { codeSourceRef, kbSourceRef, handleTimelineSourceClick };
}
