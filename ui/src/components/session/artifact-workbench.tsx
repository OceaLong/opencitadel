"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { Download, FileText, Globe, Link2, Link2Off, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { EmptyState } from "@/components/empty-state";
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

import { formatDateTime } from "@/lib/admin-utils";
import { artifactsApi } from "@/lib/api/artifacts";
import type { ArtifactEventSummary } from "@/lib/api/types";
import { cn } from "@/lib/utils";

type ShareInfo = {
  isShared: boolean;
  expiresAt: string | null;
  tokenPreview: string | null;
};

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
  const locale = useLocale();
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
  const [contentIncomplete, setContentIncomplete] = useState(false);
  const [loading, setLoading] = useState(false);
  const [sharing, setSharing] = useState(false);
  const [revoking, setRevoking] = useState(false);
  // 常驻分享状态:从后端 artifact 详情读取,刷新后仍可见/可撤销,不依赖内存中的一次性 id。
  const [shareInfo, setShareInfo] = useState<ShareInfo | null>(null);

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
  }, [active]);

  useEffect(() => {
    if (!selectedId) {
      setShareInfo(null);
      return;
    }
    let cancelled = false;
    setShareInfo(null);
    void artifactsApi
      .get(selectedId)
      .then((artifact) => {
        if (cancelled) return;
        setShareInfo({
          isShared: artifact.is_shared,
          expiresAt: artifact.share_expires_at,
          tokenPreview: artifact.share_token_preview,
        });
      })
      .catch(() => {
        if (!cancelled) setShareInfo(null);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  useEffect(() => {
    if (!selectedId || selectedVersion == null) {
      setContent("");
      setContentIncomplete(false);
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
        setContentIncomplete(data.incomplete === true);
      })
      .catch((error) => {
        if (cancelled) return;
        toast.error(error instanceof Error ? error.message : t("loadFailed"));
        setContent("");
        setContentIncomplete(false);
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
      // 用一次性返回的完整 share_token 拼链接复制;常驻状态仅保留后 4 位辅助辨认。
      setShareInfo({
        isShared: true,
        expiresAt: result.share_expires_at,
        tokenPreview: result.share_token.slice(-4),
      });
      toast.success(t("shareLinkCopied"));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("shareLinkFailed"));
    } finally {
      setSharing(false);
    }
  }, [selectedId, t]);

  const handleRevoke = useCallback(async () => {
    if (!selectedId) return;
    setRevoking(true);
    try {
      await artifactsApi.revokeShare(selectedId);
      setShareInfo({ isShared: false, expiresAt: null, tokenPreview: null });
      toast.success(t("shareRevoked"));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("shareRevokeFailed"));
    } finally {
      setRevoking(false);
    }
  }, [selectedId, t]);

  if (sortedArtifacts.length === 0) {
    return <EmptyState title={t("empty")} className={cn("h-full justify-center", className)} />;
  }

  const sharedLabelParts = shareInfo?.isShared
    ? [
        t("sharedActive"),
        shareInfo.expiresAt
          ? t("shareExpiresAt", { date: formatDateTime(shareInfo.expiresAt, locale) })
          : null,
        shareInfo.tokenPreview
          ? t("shareTokenSuffix", { suffix: shareInfo.tokenPreview })
          : null,
      ].filter((part): part is string => Boolean(part))
    : [];

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
                <SelectItem key={version} value={String(version)} translate="no">
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

        {shareInfo?.isShared && (
          <Badge variant="outline" className="border-primary/40 text-primary gap-1">
            <Link2 className="size-3" />
            {sharedLabelParts.join(" · ")}
          </Badge>
        )}

        <div className="ml-auto flex items-center gap-1">
          <Button variant="outline" size="sm" onClick={handleExport} disabled={!content || loading}>
            <Download className="size-3.5" />
            {t("export")}
          </Button>
          <Button variant="outline" size="sm" onClick={() => void handleShare()} disabled={sharing}>
            <Link2 className="size-3.5" />
            {sharing ? t("generating") : shareInfo?.isShared ? t("reshare") : t("share")}
          </Button>
          {shareInfo?.isShared && selectedId !== null && (
            <Button
              variant="outline"
              size="sm"
              className="text-destructive hover:text-destructive"
              onClick={() => void handleRevoke()}
              disabled={revoking}
            >
              <Link2Off className="size-3.5" />
              {revoking ? t("generating") : t("revokeShare")}
            </Button>
          )}
        </div>
      </div>

      <div className="relative min-h-0 flex-1 overflow-hidden">
        {loading && (
          <div className="bg-background/60 absolute inset-0 z-10 flex items-center justify-center">
            <Loader2 className="text-muted-foreground size-5 animate-spin" />
          </div>
        )}
        {contentIncomplete && !loading && (
          <div className="border-warning/40 text-warning border-b px-4 py-2 text-sm">
            {t("incompleteContentWarning")}
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
