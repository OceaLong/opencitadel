"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

import { useAuth } from "@/providers/auth-provider";
import { codebaseApi } from "@/lib/api/codebase";
import { knowledgeApi } from "@/lib/api/knowledge";
import type { Codebase, KnowledgeBase } from "@/lib/api/types";
import { IconCodebase, IconKnowledge } from "@/lib/icons";
import { cn } from "@/lib/utils";

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

export function ContextSelector({ value, onChange, disabled, className }: ContextSelectorProps) {
  const t = useTranslations("contextSelector");
  const { user } = useAuth();
  const [codebases, setCodebases] = useState<Codebase[]>([]);
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);

  const load = useCallback(async () => {
    if (!user) {
      setCodebases([]);
      setKnowledgeBases([]);
      return;
    }
    try {
      const [cb, kb] = await Promise.all([codebaseApi.list(), knowledgeApi.list()]);
      setCodebases(cb.codebases.filter((item) => item.status === "ready"));
      setKnowledgeBases(kb.knowledge_bases.filter((item) => item.status === "ready"));
    } catch {
      setCodebases([]);
      setKnowledgeBases([]);
    }
  }, [user]);

  useEffect(() => {
    void load();
  }, [load]);

  const label = (() => {
    const parts: string[] = [];
    if (value.codebaseId) {
      parts.push(codebases.find((c) => c.id === value.codebaseId)?.name ?? t("codebase"));
    }
    if (value.knowledgeBaseId) {
      parts.push(knowledgeBases.find((k) => k.id === value.knowledgeBaseId)?.name ?? t("knowledge"));
    }
    return parts.length ? parts.join(" + ") : t("none");
  })();

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={disabled}
          className={cn("max-w-[180px] truncate text-xs", className)}
        >
          {label}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-56">
        <DropdownMenuLabel>{t("title")}</DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuLabel className="text-muted-foreground text-xs font-normal">
          {t("codebaseSection")}
        </DropdownMenuLabel>
        {codebases.length === 0 ? (
          <DropdownMenuCheckboxItem checked={false} disabled>
            {t("noCodebase")}
          </DropdownMenuCheckboxItem>
        ) : (
          codebases.map((cb) => (
            <DropdownMenuCheckboxItem
              key={cb.id}
              checked={value.codebaseId === cb.id}
              onCheckedChange={(checked) =>
                onChange({
                  ...value,
                  codebaseId: checked ? cb.id : undefined,
                })
              }
            >
              <IconCodebase className="mr-2 size-3.5" />
              <span className="truncate">{cb.name}</span>
            </DropdownMenuCheckboxItem>
          ))
        )}
        <DropdownMenuSeparator />
        <DropdownMenuLabel className="text-muted-foreground text-xs font-normal">
          {t("knowledgeSection")}
        </DropdownMenuLabel>
        {knowledgeBases.length === 0 ? (
          <DropdownMenuCheckboxItem checked={false} disabled>
            {t("noKnowledge")}
          </DropdownMenuCheckboxItem>
        ) : (
          knowledgeBases.map((kb) => (
            <DropdownMenuCheckboxItem
              key={kb.id}
              checked={value.knowledgeBaseId === kb.id}
              onCheckedChange={(checked) =>
                onChange({
                  ...value,
                  knowledgeBaseId: checked ? kb.id : undefined,
                })
              }
            >
              <IconKnowledge className="mr-2 size-3.5" />
              <span className="truncate">{kb.name}</span>
            </DropdownMenuCheckboxItem>
          ))
        )}
        {(value.codebaseId || value.knowledgeBaseId) && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuCheckboxItem
              checked={false}
              onSelect={() => onChange({})}
            >
              {t("clear")}
            </DropdownMenuCheckboxItem>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
