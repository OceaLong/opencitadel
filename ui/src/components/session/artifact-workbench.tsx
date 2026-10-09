"use client";

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { Download, FileText, Globe, Link2, Link2Off, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { EmptyState } from "@/components/empty-state";
import { MarkdownContent } from "@/components/markdown-content";
import { SafeArtifactPreview } from "@/components/session/safe-artifact-preview";
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
import { executionViewApi } from "@/lib/api/execution-view";
import { ApiError, type RequestOptions } from "@/lib/api/fetch";
import type { ArtifactEventSummary } from "@/lib/api/types";
import { cn } from "@/lib/utils";
import { useAuth } from "@/providers/auth-provider";
import { useClientDataScope } from "@/providers/client-data-provider";

function unavailable(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    ([401, 403, 404].includes(error.code) ||
      Boolean(
        error.data &&
        typeof error.data === "object" &&
        "code" in error.data &&
        error.data.code === "resource_unavailable",
      ))
  );
}

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
  version?: number | null;
  onVersionChange?: (version: number) => void;
  visibleVersions?: number[];
  onArtifactChange?: (artifactId: string) => void;
  /** Execution callers supply the U05-owned reader body; legacy loaders stay off. */
  body?: React.ReactNode;
};

export function ArtifactWorkbench(props: ArtifactWorkbenchProps) {
  return props.body !== undefined ? (
    <ArtifactWorkbenchContent {...props} />
  ) : (
    <LegacyArtifactWorkbench {...props} />
  );
}
function LegacyArtifactWorkbench(props: ArtifactWorkbenchProps) {
  const { user, loading } = useAuth();
  const { scope, scopeRevision } = useClientDataScope();
  const t = useTranslations("artifactWorkbench");
  const requestOptions = useMemo(
    () => ({ workspaceId: scope?.workspaceId, skipErrorHandler: true }),
    [scope?.workspaceId],
  );
  if (loading || !user || scope?.userId !== user.id) return <p role="status">{t("loadFailed")}</p>;
  return (
    <ArtifactWorkbenchContent
      key={JSON.stringify([user.id, scope.workspaceId, scopeRevision])}
      {...props}
      requestOptions={requestOptions}
    />
  );
}
function ArtifactWorkbenchContent({
  sessionId,
  artifacts,
  focusedArtifactId,
  className,
  version: controlledVersion,
  onVersionChange,
  visibleVersions,
  onArtifactChange,
  body,
  requestOptions,
}: ArtifactWorkbenchProps & { requestOptions?: RequestOptions }) {
  const t = useTranslations("artifactWorkbench");
  const locale = useLocale();
  const generation = useRef(0);
  const requests = useRef(new Set<AbortController>());
  const revoked = useRef(false);
  const [denied, setDenied] = useState(false);

  const sortedArtifacts = useMemo(
    () =>
      body !== undefined
        ? artifacts
        : [...artifacts].sort((a, b) => a.title.localeCompare(b.title, "zh-CN")),
    [artifacts, body],
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
  const active = sortedArtifacts.find((item) => item.artifact_id === selectedId) ?? null;
  const [localVersion, setLocalVersion] = useState<{
    artifactId: string | null;
    version: number | null;
  }>({ artifactId: null, version: null });
  const selectedVersion =
    controlledVersion !== undefined
      ? controlledVersion
      : localVersion.artifactId === selectedId
        ? localVersion.version
        : (active?.version ?? null);
  const setSelectedVersion = (value: number | null) => {
    if (controlledVersion === undefined)
      setLocalVersion({ artifactId: selectedId, version: value });
    if (value != null) onVersionChange?.(value);
  };
  const [storedContent, setContent] = useState<string>("");
  const [contentKey, setContentKey] = useState("");
  const content = contentKey === `${selectedId}:${selectedVersion}` ? storedContent : "";
  const [contentIncomplete, setContentIncomplete] = useState(false);
  const [loading, setLoading] = useState(false);
  const selectionKey = `${selectedId}:${selectedVersion}`;
  const [sharingKey, setSharing] = useState<string | null>(null);
  const [revokingKey, setRevoking] = useState<string | null>(null);
  const sharing = sharingKey === selectionKey;
  const revoking = revokingKey === selectionKey;
  // 常驻分享状态:从后端 artifact 详情读取,刷新后仍可见/可撤销,不依赖内存中的一次性 id。
  const [shareInfo, setShareInfo] = useState<ShareInfo | null>(null);

  const invalidate = useCallback(() => {
    generation.current++;
    for (const controller of requests.current) controller.abort();
    requests.current.clear();
  }, []);
  const revoke = useCallback(() => {
    revoked.current = true;
    invalidate();
    setDenied(true);
    setContent("");
    setContentKey("");
    setContentIncomplete(false);
    setShareInfo(null);
    setSharing(null);
    setRevoking(null);
    setLoading(false);
  }, [invalidate]);

  useLayoutEffect(() => {
    generation.current++;
    return invalidate;
  }, [selectedId, selectedVersion, invalidate]);

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
    if (revoked.current || body !== undefined || !selectedId) {
      setShareInfo(null);
      return;
    }
    let cancelled = false;
    const captured = generation.current;
    const current = () => !cancelled && !revoked.current && captured === generation.current;
    const ownedRequests = requests.current;
    const controller = new AbortController();
    ownedRequests.add(controller);
    setShareInfo(null);
    void artifactsApi
      .get(selectedId, { ...requestOptions, signal: controller.signal })
      .then((artifact) => {
        if (!current()) return;
        setShareInfo({
          isShared: artifact.is_shared,
          expiresAt: artifact.share_expires_at,
          tokenPreview: artifact.share_token_preview,
        });
      })
      .catch((error) => {
        if (current()) {
          setShareInfo(null);
          if (unavailable(error)) revoke();
        }
      })
      .finally(() => ownedRequests.delete(controller));
    return () => {
      cancelled = true;
      controller.abort();
      ownedRequests.delete(controller);
    };
  }, [selectedId, selectedVersion, body, requestOptions, revoke]);

  useEffect(() => {
    if (revoked.current || body !== undefined || !selectedId || selectedVersion == null) {
      setContent("");
      setContentIncomplete(false);
      return;
    }
    let cancelled = false;
    const captured = generation.current;
    const current = () => !cancelled && !revoked.current && captured === generation.current;
    const ownedRequests = requests.current;
    const controller = new AbortController();
    ownedRequests.add(controller);
    setContent("");
    setLoading(true);
    void artifactsApi
      .getContent(selectedId, selectedVersion, { ...requestOptions, signal: controller.signal })
      .then((data) => {
        if (!current()) return;
        setContentKey(`${selectedId}:${selectedVersion}`);
        setContent(data.content);
        setContentIncomplete(data.incomplete === true);
      })
      .catch((error) => {
        if (!current()) return;
        if (unavailable(error)) revoke();
        toast.error(error instanceof Error ? error.message : t("loadFailed"));
        setContent("");
        setContentIncomplete(false);
      })
      .finally(() => {
        ownedRequests.delete(controller);
        if (current()) setLoading(false);
      });
    return () => {
      cancelled = true;
      controller.abort();
      ownedRequests.delete(controller);
    };
  }, [selectedId, selectedVersion, body, t, requestOptions, revoke]);

  const versionOptions = useMemo(() => {
    if (visibleVersions) return [...visibleVersions];
    if (!active) return [];
    return Array.from({ length: active.version }, (_, index) => index + 1);
  }, [active, visibleVersions]);

  const handleExport = useCallback(async () => {
    if (revoked.current || !active || !selectedId || selectedVersion == null) return;
    const captured = generation.current;
    const controller = new AbortController();
    requests.current.add(controller);
    try {
      const blob = await executionViewApi.downloadArtifact(
        selectedId,
        { version: selectedVersion, presentation: false },
        { ...requestOptions, signal: controller.signal },
      );
      if (captured !== generation.current) return;
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${active.title || "artifact"}.${active.kind === "doc" ? "md" : "html"}`;
      anchor.click();
      URL.revokeObjectURL(url);
      toast.success(t("exportSuccess"));
    } catch (error) {
      if (captured !== generation.current) return;
      if (unavailable(error)) revoke();
      toast.error(error instanceof Error ? error.message : t("loadFailed"));
    } finally {
      requests.current.delete(controller);
    }
  }, [active, selectedId, selectedVersion, requestOptions, t, revoke]);

  const handleShare = useCallback(async () => {
    if (revoked.current || !selectedId) return;
    const captured = generation.current;
    const controller = new AbortController();
    requests.current.add(controller);
    setSharing(selectionKey);
    try {
      const result = await artifactsApi.share(selectedId, {
        ...requestOptions,
        signal: controller.signal,
      });
      if (captured !== generation.current) return;
      const url = result.share_url.startsWith("http")
        ? result.share_url
        : `${window.location.origin}${result.share_url}`;
      await navigator.clipboard.writeText(url);
      if (captured !== generation.current) return;
      // 用一次性返回的完整 share_token 拼链接复制;常驻状态仅保留后 4 位辅助辨认。
      setShareInfo({
        isShared: true,
        expiresAt: result.share_expires_at,
        tokenPreview: result.share_token.slice(-4),
      });
      toast.success(t("shareLinkCopied"));
    } catch (error) {
      if (captured !== generation.current) return;
      if (unavailable(error)) revoke();
      toast.error(error instanceof Error ? error.message : t("shareLinkFailed"));
    } finally {
      requests.current.delete(controller);
      if (captured === generation.current) setSharing(null);
    }
  }, [selectedId, t, requestOptions, selectionKey, revoke]);

  const handleRevoke = useCallback(async () => {
    if (revoked.current || !selectedId) return;
    const captured = generation.current;
    const controller = new AbortController();
    requests.current.add(controller);
    setRevoking(selectionKey);
    try {
      await artifactsApi.revokeShare(selectedId, { ...requestOptions, signal: controller.signal });
      if (captured !== generation.current) return;
      setShareInfo({ isShared: false, expiresAt: null, tokenPreview: null });
      toast.success(t("shareRevoked"));
    } catch (error) {
      if (captured !== generation.current) return;
      if (unavailable(error)) revoke();
      toast.error(error instanceof Error ? error.message : t("shareRevokeFailed"));
    } finally {
      requests.current.delete(controller);
      if (captured === generation.current) setRevoking(null);
    }
  }, [selectedId, t, requestOptions, selectionKey, revoke]);

  if (denied) return <p role="status">{t("loadFailed")}</p>;
  if (sortedArtifacts.length === 0) {
    return <EmptyState title={t("empty")} className={cn("h-full justify-center", className)} />;
  }

  const sharedLabelParts = shareInfo?.isShared
    ? [
        t("sharedActive"),
        shareInfo.expiresAt
          ? t("shareExpiresAt", { date: formatDateTime(shareInfo.expiresAt, locale) })
          : null,
        shareInfo.tokenPreview ? t("shareTokenSuffix", { suffix: shareInfo.tokenPreview }) : null,
      ].filter((part): part is string => Boolean(part))
    : [];

  return (
    <div className={cn("flex h-full flex-col overflow-hidden", className)}>
      <div className="border-border/70 flex flex-shrink-0 flex-wrap items-center gap-2 border-b px-4 py-3">
        <Select
          value={selectedId ?? undefined}
          onValueChange={(value) => {
            if (onArtifactChange) {
              onArtifactChange(value);
              return;
            }
            setSelectedId(value);
            const next = sortedArtifacts.find((item) => item.artifact_id === value);
            setLocalVersion({ artifactId: value, version: next?.version ?? null });
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
            value={selectedVersion != null ? String(selectedVersion) : ""}
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

        {active && body === undefined && (
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

        {body === undefined && (
          <div className="ml-auto flex items-center gap-1">
            <Button
              variant="outline"
              size="sm"
              onClick={handleExport}
              disabled={!content || loading}
            >
              <Download className="size-3.5" />
              {t("export")}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void handleShare()}
              disabled={sharing}
            >
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
        )}
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
        {body !== undefined ? (
          body
        ) : active?.kind === "web" ? (
          <SafeArtifactPreview
            notice={t("safePreview")}
            title={active.title}
            content={content}
            className="h-full w-full border-0 bg-white"
          />
        ) : (
          <div className="h-full overflow-y-auto px-4 py-4">
            <MarkdownContent content={content || t("emptyContent")} />
          </div>
        )}
      </div>
      {body === undefined && (
        <p className="text-muted-foreground border-border/70 border-t px-4 py-2 text-xs">
          {t("sessionLabel", { id: sessionId.slice(0, 8) })}
        </p>
      )}
    </div>
  );
}
