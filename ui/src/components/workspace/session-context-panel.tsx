"use client";

import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { CodebaseContextPanel } from "@/components/workspace/codebase-context-panel";
import { KnowledgeContextPanel } from "@/components/workspace/knowledge-context-panel";
import { SessionResourceVersion } from "@/components/workspace/session-resource-version";

import type { SessionResourceBinding } from "@/lib/api/types";
import { cn } from "@/lib/utils";

const EMPTY_RESOURCE_BINDINGS: SessionResourceBinding[] = [];

type SessionContextPanelProps = {
  codebaseId?: string | null;
  knowledgeBaseId?: string | null;
  sessionId?: string;
  resourceBindings?: SessionResourceBinding[];
  codeSourceRef?: React.MutableRefObject<((path: string, line?: number) => void) | null>;
  kbSourceRef?: React.MutableRefObject<((value: string) => void) | null>;
  className?: string;
};

export function SessionContextPanel({
  codebaseId,
  knowledgeBaseId,
  sessionId,
  resourceBindings,
  codeSourceRef,
  kbSourceRef,
  className,
}: SessionContextPanelProps) {
  const t = useTranslations("workspaceContext");
  const suppliedBindings = resourceBindings ?? EMPTY_RESOURCE_BINDINGS;
  const [currentBindings, setCurrentBindings] = useState(suppliedBindings);
  useEffect(() => {
    setCurrentBindings(suppliedBindings);
  }, [suppliedBindings]);
  const hasCode = Boolean(codebaseId);
  const hasKb = Boolean(knowledgeBaseId);
  const boundKnowledgeVersionId = currentBindings.find(
    (binding) =>
      binding.is_current &&
      binding.resource_kind === "knowledge_base" &&
      binding.resource_id === knowledgeBaseId,
  )?.version_id;
  const boundCodebaseVersionId = currentBindings.find(
    (binding) =>
      binding.is_current &&
      binding.resource_kind === "codebase" &&
      binding.resource_id === codebaseId,
  )?.version_id;

  if (!hasCode && !hasKb) return null;

  const knowledgePanel = boundKnowledgeVersionId ? (
    <KnowledgeContextPanel
      knowledgeBaseId={knowledgeBaseId!}
      versionId={boundKnowledgeVersionId}
      onSourceClickRef={kbSourceRef}
    />
  ) : (
    <p role="alert" className="text-muted-foreground p-4 text-sm">
      {t("knowledgeVersionUnavailable")}
    </p>
  );

  const panel =
    hasCode && hasKb ? (
      <Tabs defaultValue="code" className="flex min-h-0 flex-1 flex-col">
        <TabsList className="mx-2 mt-2 grid w-auto grid-cols-2">
          <TabsTrigger value="code">{t("codebaseTab")}</TabsTrigger>
          <TabsTrigger value="kb">{t("knowledgeTab")}</TabsTrigger>
        </TabsList>
        <TabsContent value="code" className="min-h-0 flex-1 overflow-hidden">
          <CodebaseContextPanel
            codebaseId={codebaseId!}
            codebaseVersionId={boundCodebaseVersionId}
            onSourceClickRef={codeSourceRef}
          />
        </TabsContent>
        <TabsContent value="kb" className="min-h-0 flex-1 overflow-hidden">
          {knowledgePanel}
        </TabsContent>
      </Tabs>
    ) : hasCode ? (
      <CodebaseContextPanel
        codebaseId={codebaseId!}
        codebaseVersionId={boundCodebaseVersionId}
        onSourceClickRef={codeSourceRef}
      />
    ) : (
      knowledgePanel
    );

  return (
    <div className={cn("border-border flex h-full w-96 shrink-0 flex-col border-l", className)}>
      {sessionId && (
        <SessionResourceVersion
          sessionId={sessionId}
          bindings={currentBindings}
          onBindingsChanged={setCurrentBindings}
        />
      )}
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
