"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Download, FileText, Globe, Link2, Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { MarkdownContent } from "@/components/markdown-content";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import { artifactsApi } from "@/lib/api/artifacts";
import type { ArtifactEventSummary } from "@/lib/api/types";
import { cn } from "@/lib/utils";

export type ArtifactWorkbenchProps = {
  sessionId: string;
  artifacts: ArtifactEventSummary[];
  focusedArtifactId?: string | null;
  className?: string;
};

export function ArtifactWorkbench({
  sessionId,
  artifacts,
  focusedArtifactId,
  className,
}: ArtifactWorkbenchProps) {
  const t = useTranslations("artifactWorkbench");
  const sortedArtifacts = useMemo(
    () => [...artifacts].sort((a, b) => a.title.localeCompare(b.title, "zh-CN")),
    [artifacts],
  );

  const statusLabel = useCallback(
    (status: ArtifactEventSummary["status"]) => {
      if (status === "draft") return t("statusDraft");
      if (status === "updated") return t("statusUpdated");
      return t("statusFinal");
    },
    [t],
  );

  const [selectedId, setSelectedId] = useState<string | null>(
    focusedArtifactId ?? sortedArtifacts[0]?.artifact_id ?? null,
  );
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null);
  const [content, setContent] = useState<string>("");
  const [contentType, setContentType] = useState<string>("text/markdown");
  const [loading, setLoading] = useState(false);
  const [sharing, setSharing] = useState(false);

  const active = sortedArtifacts.find((item) => item.artifact_id === selectedId) ?? null;

  useEffect(() => {
    if (focusedArtifactId) {
      setSelectedId(focusedArtifactId);
    }
  }, [focusedArtifactId]);

  useEffect(() => {
    if (!selectedId && sortedArtifacts[0]) {
      setSelectedId(sortedArtifacts[0].artifact_id);
    }
  }, [selectedId, sortedArtifacts]);

  useEffect(() => {
    if (active) {
      setSelectedVersion(active.version);
    }
  }, [active?.artifact_id, active?.version]);

  useEffect(() => {
    if (!selectedId || selectedVersion == null) {
      setContent("");
      return;
    }
    let cancelled = false;
    setLoading(true);
    void artifactsApi
      .getContent(selectedId, selectedVersion)
      .then((data) => {
        if (cancelled) return;
        setContent(data.content);
        setContentType(data.content_type);
      })
      .catch((error) => {
        if (cancelled) return;
        toast.error(error instanceof Error ? error.message : t("loadFailed"));
        setContent("");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId, selectedVersion, t]);

  const versionOptions = useMemo(() => {
    if (!active) return [];
    return Array.from({ length: active.version }, (_, index) => index + 1);
  }, [active]);

  const handleExport = useCallback(() => {
    if (!content || !active) return;
    const ext = active.kind === "doc" ? "md" : "html";
    const blob = new Blob([content], { type: contentType });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${active.title || "artifact"}.${ext}`;
    anchor.click();
    URL.revokeObjectURL(url);
    toast.success(t("exportSuccess"));
  }, [active, content, contentType, t]);

  const handleShare = useCallback(async () => {
    if (!selectedId) return;
    setSharing(true);
    try {
      const result = await artifactsApi.share(selectedId);
      const url = result.share_url.startsWith("http")
        ? result.share_url
        : `${window.location.origin}${result.share_url}`;
      await navigator.clipboard.writeText(url);
      toast.success(t("shareLinkCopied"));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("shareLinkFailed"));
    } finally {
      setSharing(false);
    }
  }, [selectedId, t]);

  if (sortedArtifacts.length === 0) {
    return (
      <div className={cn("text-muted-foreground flex h-full items-center justify-center text-sm", className)}>
        {t("empty")}
      </div>
    );
  }

  return (
    <div className={cn("flex h-full flex-col overflow-hidden", className)}>
      <div className="border-border/70 flex flex-shrink-0 flex-wrap items-center gap-2 border-b px-4 py-3">
        <Select
          value={selectedId ?? undefined}
          onValueChange={(value) => {
            setSelectedId(value);
            const next = sortedArtifacts.find((item) => item.artifact_id === value);
            setSelectedVersion(next?.version ?? null);
          }}
        >
          <SelectTrigger size="sm" className="max-w-[220px]">
            <SelectValue placeholder={t("selectArtifact")} />
          </SelectTrigger>
          <SelectContent>
            {sortedArtifacts.map((item) => (
              <SelectItem key={item.artifact_id} value={item.artifact_id}>
                {item.title}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {versionOptions.length > 0 && (
          <Select
            value={selectedVersion != null ? String(selectedVersion) : undefined}
            onValueChange={(value) => setSelectedVersion(Number(value))}
          >
            <SelectTrigger size="sm" className="w-[100px]">
              <SelectValue placeholder={t("version")} />
            </SelectTrigger>
            <SelectContent>
              {versionOptions.map((version) => (
                <SelectItem key={version} value={String(version)}>
                  v{version}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}

        {active && (
          <Badge variant="secondary" className="gap-1">
            {active.kind === "doc" ? <FileText className="size-3" /> : <Globe className="size-3" />}
            {statusLabel(active.status)}
          </Badge>
        )}

        <div className="ml-auto flex items-center gap-1">
          <Button variant="outline" size="sm" onClick={handleExport} disabled={!content || loading}>
            <Download className="size-3.5" />
            {t("export")}
          </Button>
          <Button variant="outline" size="sm" onClick={() => void handleShare()} disabled={sharing}>
            <Link2 className="size-3.5" />
            {sharing ? t("generating") : t("share")}
          </Button>
        </div>
      </div>

      <div className="relative flex-1 overflow-hidden">
        {loading && (
          <div className="bg-background/60 absolute inset-0 z-10 flex items-center justify-center">
            <Loader2 className="text-muted-foreground size-5 animate-spin" />
          </div>
        )}
        {active?.kind === "web" ? (
          <iframe
            title={active.title}
            srcDoc={content}
            className="h-full w-full border-0 bg-white"
            sandbox="allow-scripts"
          />
        ) : (
          <div className="h-full overflow-y-auto px-4 py-4">
            <MarkdownContent content={content || t("emptyContent")} />
          </div>
        )}
      </div>
      <p className="text-muted-foreground border-border/70 border-t px-4 py-2 text-xs">
        {t("sessionLabel", { id: sessionId.slice(0, 8) })}
      </p>
    </div>
  );
}
