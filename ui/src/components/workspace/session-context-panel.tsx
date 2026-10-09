"use client";

import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";

import { parseKbDocHref } from "@/components/knowledge/knowledge-utils";
import {
  KnowledgeContextPanel,
  type SelectedSource,
} from "@/components/workspace/knowledge-context-panel";
import { SessionResourceVersion } from "@/components/workspace/session-resource-version";

import type { SessionResourceBinding } from "@/lib/api/types";
import { cn } from "@/lib/utils";

const EMPTY_RESOURCE_BINDINGS: SessionResourceBinding[] = [];

type SessionContextPanelProps = {
  knowledgeBaseId?: string | null;
  sessionId?: string;
  resourceBindings?: SessionResourceBinding[];
  kbSourceRef?: React.MutableRefObject<((value: string) => void) | null>;
  className?: string;
  fixedSource?: SelectedSource | null;
};

export function SessionContextPanel({
  knowledgeBaseId,
  sessionId,
  resourceBindings,
  kbSourceRef,
  className,
  fixedSource,
}: SessionContextPanelProps) {
  const t = useTranslations("workspaceContext");
  const suppliedBindings = resourceBindings ?? EMPTY_RESOURCE_BINDINGS;
  const [currentBindings, setCurrentBindings] = useState(suppliedBindings);
  useEffect(() => {
    setCurrentBindings(suppliedBindings);
  }, [suppliedBindings]);
  const [localSource, setLocalSource] = useState<SelectedSource | null>(null);
  const source = fixedSource ?? localSource;
  useEffect(() => {
    if (!kbSourceRef) return;
    const open = (value: string) => {
      const ref = parseKbDocHref(value);
      if (ref?.versionId && ref.revisionId)
        setLocalSource({
          versionId: ref.versionId,
          revisionId: ref.revisionId,
          documentId: ref.docId,
          chunkId: ref.chunkId,
          page: ref.page,
        });
      else setLocalSource(null);
    };
    kbSourceRef.current = open;
    return () => {
      if (kbSourceRef.current === open) kbSourceRef.current = null;
    };
  }, [kbSourceRef]);
  const hasKb = Boolean(knowledgeBaseId);
  const boundKnowledgeVersionId = currentBindings.find(
    (binding) =>
      binding.is_current &&
      binding.resource_kind === "knowledge_base" &&
      binding.resource_id === knowledgeBaseId,
  )?.version_id;
  if (!hasKb) return null;

  const selectedVersionId = source?.versionId ?? boundKnowledgeVersionId;
  const knowledgePanel = selectedVersionId ? (
    <KnowledgeContextPanel
      knowledgeBaseId={knowledgeBaseId!}
      versionId={selectedVersionId}
      fixedSource={source}
    />
  ) : (
    <p role="alert" className="text-muted-foreground p-4 text-sm">
      {t("knowledgeVersionUnavailable")}
    </p>
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
      {knowledgePanel}
    </div>
  );
}

export function useSessionContextRefs() {
  const kbSourceRef = useRef<((value: string) => void) | null>(null);

  const handleTimelineSourceClick = (path: string) => {
    kbSourceRef.current?.(path);
  };

  return { kbSourceRef, handleTimelineSourceClick };
}
