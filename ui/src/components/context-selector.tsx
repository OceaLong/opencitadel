"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { Check, ChevronDown } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";

import { useIsMobile } from "@/hooks/use-mobile";
import { codebaseApi } from "@/lib/api/codebase";
import { knowledgeApi } from "@/lib/api/knowledge";
import type { Codebase, KnowledgeBase } from "@/lib/api/types";
import {
  getSessionContextKind,
  IconAsk,
  IconCodebase,
  IconKnowledge,
  IconSearch,
  IconTask,
  type SessionContextKind,
} from "@/lib/icons";
import { cn } from "@/lib/utils";
import { useAuth } from "@/providers/auth-provider";

export type SessionContextSelection = {
  codebaseId?: string;
  knowledgeBaseId?: string;
};

type ContextSelectorProps = {
  value: SessionContextSelection;
  onChange: (value: SessionContextSelection) => void;
  disabled?: boolean;
  className?: string;
};

function matchesSearch(query: string, name: string, description?: string) {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return (
    name.toLowerCase().includes(q) || (description?.toLowerCase().includes(q) ?? false)
  );
}

type ContextOptionRowProps = {
  icon: React.ReactNode;
  title: string;
  description?: string;
  selected: boolean;
  onSelect: () => void;
};

function ContextOptionRow({ icon, title, description, selected, onSelect }: ContextOptionRowProps) {
  return (
    <button
      type="button"
      className={cn(
        "hover:bg-muted flex w-full items-start gap-3 rounded-lg px-3 py-2.5 text-left transition-colors",
        selected && "bg-muted/60",
      )}
      onClick={onSelect}
    >
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          {icon}
          <span className="text-foreground truncate text-sm font-medium">{title}</span>
        </div>
        {description && (
          <p className="text-muted-foreground mt-0.5 line-clamp-2 text-xs">{description}</p>
        )}
      </div>
      {selected && <Check className="text-primary mt-0.5 size-4 shrink-0" />}
    </button>
  );
}

function SessionContextTriggerIcon({
  kind,
  className,
}: {
  kind: SessionContextKind;
  className?: string;
}) {
  switch (kind) {
    case "codebase":
      return <IconCodebase className={className} />;
    case "knowledge":
      return <IconKnowledge className={className} />;
    case "hybrid":
      return <IconAsk className={className} />;
    default:
      return <IconTask className={className} />;
  }
}

export function ContextSelector({ value, onChange, disabled, className }: ContextSelectorProps) {
  const t = useTranslations("contextSelector");
  const { user } = useAuth();
  const { isMobile } = useIsMobile();
  const [codebases, setCodebases] = useState<Codebase[]>([]);
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!user) return;

    let cancelled = false;

    void (async () => {
      try {
        const [cb, kb] = await Promise.all([codebaseApi.list(), knowledgeApi.list()]);
        if (cancelled) return;
        setCodebases(cb.codebases.filter((item) => item.status === "ready"));
        setKnowledgeBases(kb.knowledge_bases.filter((item) => item.status === "ready"));
      } catch {
        if (!cancelled) {
          setCodebases([]);
          setKnowledgeBases([]);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [user]);

  const readyCodebases = useMemo(() => (user ? codebases : []), [user, codebases]);
  const readyKnowledgeBases = useMemo(() => (user ? knowledgeBases : []), [user, knowledgeBases]);

  const getCodebaseDescription = useCallback(
    (cb: Codebase) => cb.source_ref ?? t("codebaseMeta", { count: cb.file_count ?? 0 }),
    [t],
  );

  const getKnowledgeDescription = useCallback(
    (kb: KnowledgeBase) => t("knowledgeMeta", { count: kb.doc_count }),
    [t],
  );

  const filteredCodebases = useMemo(
    () =>
      readyCodebases.filter((cb) =>
        matchesSearch(searchQuery, cb.name, getCodebaseDescription(cb)),
      ),
    [readyCodebases, searchQuery, getCodebaseDescription],
  );

  const filteredKnowledgeBases = useMemo(
    () =>
      readyKnowledgeBases.filter((kb) =>
        matchesSearch(searchQuery, kb.name, getKnowledgeDescription(kb)),
      ),
    [readyKnowledgeBases, searchQuery, getKnowledgeDescription],
  );

  const hasSelection = Boolean(value.codebaseId || value.knowledgeBaseId);

  const label = (() => {
    const parts: string[] = [];
    if (value.codebaseId) {
      parts.push(readyCodebases.find((c) => c.id === value.codebaseId)?.name ?? t("codebase"));
    }
    if (value.knowledgeBaseId) {
      parts.push(
        readyKnowledgeBases.find((k) => k.id === value.knowledgeBaseId)?.name ?? t("knowledge"),
      );
    }
    return parts.length ? parts.join(" + ") : t("none");
  })();

  const contextKind = getSessionContextKind({
    codebase_id: value.codebaseId,
    knowledge_base_id: value.knowledgeBaseId,
  });

  const handleOpenChange = (next: boolean) => {
    setOpen(next);
    if (!next) setSearchQuery("");
  };

  const toggleCodebase = (id: string) => {
    onChange({
      ...value,
      codebaseId: value.codebaseId === id ? undefined : id,
    });
  };

  const toggleKnowledgeBase = (id: string) => {
    onChange({
      ...value,
      knowledgeBaseId: value.knowledgeBaseId === id ? undefined : id,
    });
  };

  return (
    <DropdownMenu open={open} onOpenChange={handleOpenChange}>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={disabled}
          className={cn(
            "h-8 max-w-[160px] gap-1 px-2 text-xs font-normal sm:max-w-[160px]",
            hasSelection
              ? "text-foreground bg-accent/60 hover:bg-accent hover:text-foreground"
              : "text-muted-foreground hover:text-foreground",
            className,
          )}
        >
          <SessionContextTriggerIcon
            kind={contextKind}
            className={cn(
              "size-4 shrink-0",
              hasSelection ? "text-primary" : "text-muted-foreground",
            )}
          />
          <span className="truncate">{label}</span>
          <ChevronDown className="size-3 shrink-0 opacity-60" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-[min(100vw-2rem,280px)] p-1.5 sm:w-[280px]">
        <DropdownMenuLabel className="px-2 py-1.5">{t("title")}</DropdownMenuLabel>
        <div className="px-2 pb-2">
          <div className="relative">
            <IconSearch className="text-muted-foreground pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2" />
            <Input
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={(e) => e.stopPropagation()}
              placeholder={t("searchPlaceholder")}
              className="h-8 pl-8 text-xs"
              autoFocus={!isMobile}
            />
          </div>
        </div>

        <div className="px-2 pb-1">
          <p className="text-muted-foreground mb-1 text-xs font-medium">{t("codebaseSection")}</p>
          {readyCodebases.length === 0 ? (
            <p className="text-muted-foreground px-3 py-2 text-xs">{t("noCodebase")}</p>
          ) : filteredCodebases.length === 0 ? (
            <p className="text-muted-foreground px-3 py-2 text-xs">{t("noSearchResults")}</p>
          ) : (
            filteredCodebases.map((cb) => (
              <ContextOptionRow
                key={cb.id}
                icon={<IconCodebase className="size-3.5 shrink-0" />}
                title={cb.name}
                description={getCodebaseDescription(cb)}
                selected={value.codebaseId === cb.id}
                onSelect={() => toggleCodebase(cb.id)}
              />
            ))
          )}
        </div>

        <DropdownMenuSeparator />

        <div className="px-2 pb-1">
          <p className="text-muted-foreground mb-1 text-xs font-medium">{t("knowledgeSection")}</p>
          {readyKnowledgeBases.length === 0 ? (
            <p className="text-muted-foreground px-3 py-2 text-xs">{t("noKnowledge")}</p>
          ) : filteredKnowledgeBases.length === 0 ? (
            <p className="text-muted-foreground px-3 py-2 text-xs">{t("noSearchResults")}</p>
          ) : (
            filteredKnowledgeBases.map((kb) => (
              <ContextOptionRow
                key={kb.id}
                icon={<IconKnowledge className="size-3.5 shrink-0" />}
                title={kb.name}
                description={getKnowledgeDescription(kb)}
                selected={value.knowledgeBaseId === kb.id}
                onSelect={() => toggleKnowledgeBase(kb.id)}
              />
            ))
          )}
        </div>

        {hasSelection && (
          <>
            <DropdownMenuSeparator />
            <button
              type="button"
              className="text-muted-foreground hover:text-foreground hover:bg-muted w-full rounded-lg px-3 py-2 text-center text-xs transition-colors"
              onClick={() => onChange({})}
            >
              {t("clear")}
            </button>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
