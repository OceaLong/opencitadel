"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";
import { Check, ShieldAlert, X } from "lucide-react";
import { toast } from "sonner";

import { AsyncBoundary } from "@/components/async-boundary";
import { EmptyState } from "@/components/empty-state";
import { LoadingSpinner } from "@/components/loading-spinner";
import { PageHeader } from "@/components/page-header";
import { ScrollablePageContent } from "@/components/scrollable-page-content";
import { StatusBadge, type StatusBadgeVariant } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

import { type PaginatedFetcher, usePaginatedList } from "@/hooks/use-paginated-list";
import { formatDateTime } from "@/lib/admin-utils";
import {
  type ApprovalInboxItem,
  type ApprovalInboxStatus,
  approvalsApi,
} from "@/lib/api/approvals";
import { sessionApi } from "@/lib/api/session";

function approvalStatusVariant(status: string): StatusBadgeVariant {
  if (status === "pending") return "warning";
  if (status === "approved") return "success";
  if (status === "rejected") return "destructive";
  return "secondary";
}

function ApprovalInboxRow({
  item,
  statusLabels,
  onDecided,
}: {
  item: ApprovalInboxItem;
  statusLabels: Record<string, string>;
  onDecided: () => void;
}) {
  const t = useTranslations("approvals");
  const tActions = useTranslations("approvalActions");
  const tCommon = useTranslations("common");
  const locale = useLocale();
  const [rejectOpen, setRejectOpen] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const decide = async (decision: "approved" | "rejected", reason = "") => {
    setSubmitting(true);
    try {
      await sessionApi.decideApproval(item.approval_id, decision, reason);
      toast.success(decision === "approved" ? t("approveSuccess") : t("rejectSuccess"));
      onDecided();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : tActions("sendFailed"));
    } finally {
      setSubmitting(false);
    }
  };

  const reject = async () => {
    const reason = feedback.trim();
    if (!reason) {
      toast.error(tActions("rejectReasonRequired"));
      return;
    }
    await decide("rejected", reason);
    setRejectOpen(false);
    setFeedback("");
  };

  const sessionHref =
    item.source_entity_type === "session" ? `/sessions/${item.source_entity_id}` : null;

  return (
    <div className="grid gap-3 rounded-lg border p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge variant={approvalStatusVariant(item.status)}>
              {statusLabels[item.status] ?? item.status}
            </StatusBadge>
            <Badge variant="outline" translate="no">
              {item.approval_kind}
            </Badge>
          </div>
          <h3 className="mt-2 text-sm font-medium break-all">{item.subject_label}</h3>
          <p className="text-muted-foreground mt-1 text-xs break-all">{item.risk_summary}</p>
          <p className="text-muted-foreground mt-1 text-xs">
            {t("requestedAt", { time: formatDateTime(item.requested_at, locale) })}
            {item.decided_at
              ? ` · ${t("decidedAt", { time: formatDateTime(item.decided_at, locale) })}`
              : ""}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {sessionHref ? (
            <Button variant="ghost" size="sm" asChild>
              <Link href={sessionHref}>{t("viewSession")}</Link>
            </Button>
          ) : null}
          {item.status === "pending" && !rejectOpen ? (
            <>
              <Button size="sm" disabled={submitting} onClick={() => void decide("approved")}>
                <Check className="size-3.5" />
                {tActions("approve")}
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={submitting}
                onClick={() => setRejectOpen(true)}
              >
                <X className="size-3.5" />
                {tActions("reject")}
              </Button>
            </>
          ) : null}
        </div>
      </div>
      {rejectOpen ? (
        <div className="space-y-2">
          <Textarea
            value={feedback}
            onChange={(event) => setFeedback(event.target.value)}
            placeholder={tActions("rejectPlaceholder")}
            rows={2}
          />
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="destructive"
              disabled={submitting}
              onClick={() => void reject()}
            >
              {tActions("confirmReject")}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setRejectOpen(false)}>
              {tCommon("cancel")}
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

export default function ApprovalsPage() {
  const t = useTranslations("approvals");
  const tCommon = useTranslations("common");
  const [status, setStatus] = useState<ApprovalInboxStatus | "all">("pending");

  const statusLabels: Record<string, string> = {
    pending: t("status.pending"),
    approved: t("status.approved"),
    rejected: t("status.rejected"),
    cancelled: t("status.cancelled"),
    expired: t("status.expired"),
  };

  // /api/approvals 不返回 total：多取一条判断是否还有下一页，total 只用于翻页判定。
  const fetchApprovals = useCallback<PaginatedFetcher<ApprovalInboxItem>>(
    async ({ limit, offset }) => {
      const data = await approvalsApi.list({
        status: status === "all" ? undefined : status,
        limit: limit + 1,
        offset,
      });
      const hasMore = data.items.length > limit;
      const items = hasMore ? data.items.slice(0, limit) : data.items;
      return { items, total: offset + items.length + (hasMore ? 1 : 0) };
    },
    [status],
  );

  const { items, loading, error, offset, canPrev, canNext, load, nextPage, prevPage } =
    usePaginatedList<ApprovalInboxItem>(fetchApprovals);

  useEffect(() => {
    // status 筛选变化时回到第一页重新加载。
    void load(0);
  }, [fetchApprovals, load]);

  return (
    <ScrollablePageContent>
      <div className="grid gap-5">
        <PageHeader
          title={t("title")}
          description={t("description")}
          actions={
            <Select
              value={status}
              onValueChange={(value) => setStatus(value as ApprovalInboxStatus | "all")}
            >
              <SelectTrigger className="w-full sm:w-[180px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="pending">{statusLabels.pending}</SelectItem>
                <SelectItem value="approved">{statusLabels.approved}</SelectItem>
                <SelectItem value="rejected">{statusLabels.rejected}</SelectItem>
                <SelectItem value="cancelled">{statusLabels.cancelled}</SelectItem>
                <SelectItem value="expired">{statusLabels.expired}</SelectItem>
                <SelectItem value="all">{t("filterAll")}</SelectItem>
              </SelectContent>
            </Select>
          }
        />
        <AsyncBoundary
          loading={loading}
          error={error}
          onRetry={() => void load(offset)}
          loadingFallback={
            <div className="flex justify-center py-12">
              <LoadingSpinner />
            </div>
          }
        >
          {items.length === 0 ? (
            <EmptyState icon={ShieldAlert} title={t("empty")} className="py-12" />
          ) : (
            <div className="grid gap-3">
              {items.map((item) => (
                <ApprovalInboxRow
                  key={item.approval_id}
                  item={item}
                  statusLabels={statusLabels}
                  onDecided={() => void load(offset)}
                />
              ))}
            </div>
          )}
          {(canPrev || canNext) && (
            <div className="mt-4 flex justify-end gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={!canPrev}
                onClick={() => void prevPage()}
              >
                {tCommon("previousPage")}
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={!canNext}
                onClick={() => void nextPage()}
              >
                {tCommon("nextPage")}
              </Button>
            </div>
          )}
        </AsyncBoundary>
      </div>
    </ScrollablePageContent>
  );
}
