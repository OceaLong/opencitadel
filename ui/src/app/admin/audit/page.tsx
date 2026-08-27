"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { ShieldCheck, ShieldX } from "lucide-react";
import { toast } from "sonner";

import { AdminTimeRangePicker } from "@/components/admin/time-range-picker";
import { AuditActivityChart } from "@/components/admin/usage-charts";
import { EmptyState } from "@/components/empty-state";
import { LoadingSpinner } from "@/components/loading-spinner";
import { PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

import { type AdminTimeRange, formatDateTime, getAdminDateRange } from "@/lib/admin-utils";
import { adminApi, type AuditLog, type AuditLogDetail } from "@/lib/api/admin";
import { type ChainVerifyResult, complianceApi } from "@/lib/api/compliance";
import { IconCopy, IconDownload } from "@/lib/icons";

const PAGE_SIZE = 20;

function actionBadgeVariant(action: string): "secondary" | "destructive" | "warning" | "success" {
  const lower = action.toLowerCase();
  if (lower.includes("delete") || lower.includes("reject") || lower.includes("fail")) {
    return "destructive";
  }
  if (lower.includes("create") || lower.includes("approve") || lower.includes("accept")) {
    return "success";
  }
  if (lower.includes("update") || lower.includes("patch") || lower.includes("edit")) {
    return "warning";
  }
  return "secondary";
}

export default function AdminAuditPage() {
  const t = useTranslations("admin");
  const tCommon = useTranslations("common");
  const [range, setRange] = useState<AdminTimeRange>("30d");
  const [actionFilter, setActionFilter] = useState<string>("all");
  const [actorFilter, setActorFilter] = useState("");
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [byDay, setByDay] = useState<Array<{ date: string; count: number }>>([]);
  const [byAction, setByAction] = useState<Array<{ action: string; count: number }>>([]);
  const [chain, setChain] = useState<ChainVerifyResult | null>(null);
  const [chainLoading, setChainLoading] = useState(false);
  const [detail, setDetail] = useState<AuditLogDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const dateParams = useMemo(() => getAdminDateRange(range), [range]);

  const listParams = useMemo(
    () => ({
      ...dateParams,
      limit: PAGE_SIZE,
      action: actionFilter === "all" ? undefined : actionFilter,
      actor_user_id: actorFilter.trim() || undefined,
    }),
    [actionFilter, actorFilter, dateParams],
  );

  const exportUrl = useMemo(
    () =>
      adminApi.exportAuditCsvUrl({
        ...dateParams,
        action: actionFilter === "all" ? undefined : actionFilter,
        actor_user_id: actorFilter.trim() || undefined,
      }),
    [actionFilter, actorFilter, dateParams],
  );

  const verifyChain = useCallback(async () => {
    setChainLoading(true);
    try {
      const result = await complianceApi.verifyChain();
      setChain(result);
    } finally {
      setChainLoading(false);
    }
  }, []);

  const loadAudit = useCallback(
    async (nextOffset: number) => {
      setLoading(true);
      try {
        const [auditData, summary] = await Promise.all([
          adminApi.audit({ ...listParams, offset: nextOffset }),
          adminApi.auditSummary(dateParams),
        ]);
        setLogs(auditData.logs);
        setTotal(auditData.total);
        setOffset(nextOffset);
        setByDay(summary.by_day);
        setByAction(summary.by_action);
      } finally {
        setLoading(false);
      }
    },
    [dateParams, listParams],
  );

  const openDetail = useCallback(async (logId: string) => {
    setDetailLoading(true);
    try {
      const data = await adminApi.auditDetail(logId);
      setDetail(data);
    } finally {
      setDetailLoading(false);
    }
  }, []);

  const copyToClipboard = useCallback(
    async (value: string) => {
      try {
        await navigator.clipboard.writeText(value);
        toast.success(t("copied"));
      } catch {
        toast.error(tCommon("copyFailed"));
      }
    },
    [t, tCommon],
  );

  useEffect(() => {
    void loadAudit(0);
  }, [loadAudit]);

  useEffect(() => {
    void verifyChain();
  }, [verifyChain]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1;
  const chainOk = chain?.ok ?? null;

  const chainStatusLabel =
    chainOk == null
      ? null
      : chainOk
        ? t("chainIntact")
        : `${t("chainBroken")}${
            chain && !chain.ok && chain.first_broken_seq != null
              ? ` · ${t("chainBrokenAt", { seq: chain.first_broken_seq })}`
              : ""
          }${
            chain?.checked_at
              ? ` · ${t("chainCheckedAt", { time: formatDateTime(chain.checked_at) })}`
              : ""
          }`;

  return (
    <div className="space-y-6">
      <PageHeader
        title={t("auditLog")}
        description={t("auditSubtitle")}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <AdminTimeRangePicker value={range} onChange={setRange} />
            {chainOk != null && chainStatusLabel && (
              <StatusBadge variant={chainOk ? "success" : "destructive"} className="gap-1">
                {chainOk ? <ShieldCheck className="size-3.5" /> : <ShieldX className="size-3.5" />}
                {chainStatusLabel}
              </StatusBadge>
            )}
            <Button
              variant="outline"
              size="sm"
              disabled={chainLoading}
              onClick={() => void verifyChain()}
            >
              {chainLoading ? <LoadingSpinner /> : t("verifyChain")}
            </Button>
            <Button variant="outline" asChild>
              <a href={exportUrl}>
                <IconDownload className="mr-1 size-4" />
                {t("exportCsv")}
              </a>
            </Button>
          </div>
        }
      />

      <div className="flex flex-wrap items-end gap-3">
        <div className="w-full sm:w-48">
          <Select value={actionFilter} onValueChange={setActionFilter}>
            <SelectTrigger>
              <SelectValue placeholder={t("filterAction")} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t("filterActionAll")}</SelectItem>
              {byAction.map((item) => (
                <SelectItem key={item.action} value={item.action}>
                  {item.action} ({item.count})
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Input
          className="max-w-xs"
          placeholder={t("filterActor")}
          value={actorFilter}
          onChange={(event) => setActorFilter(event.target.value)}
        />
        <Button variant="secondary" size="sm" onClick={() => void loadAudit(0)}>
          {tCommon("retry")}
        </Button>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <AuditActivityChart byDay={byDay} />
        <Card>
          <CardHeader>
            <CardTitle className="text-base">{t("actionDistributionTitle")}</CardTitle>
            <CardDescription>{t("actionDistributionDesc")}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {byAction.length === 0 ? (
              <EmptyState title={t("noAuditData")} className="py-10" />
            ) : (
              byAction.slice(0, 10).map((item) => (
                <button
                  key={item.action}
                  type="button"
                  className="hover:bg-muted/60 flex w-full items-center justify-between rounded-lg border px-3 py-2 text-left text-sm transition-colors"
                  onClick={() => setActionFilter(item.action)}
                >
                  <span className="truncate">{item.action}</span>
                  <Badge variant="secondary">{item.count}</Badge>
                </button>
              ))
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("logListTitle")}</CardTitle>
          <CardDescription>{t("totalRecords", { count: total })}</CardDescription>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="flex justify-center py-12">
              <LoadingSpinner />
            </div>
          ) : logs.length === 0 ? (
            <EmptyState title={t("noAuditRecords")} className="py-10" />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("columnAction")}</TableHead>
                  <TableHead>{t("columnTime")}</TableHead>
                  <TableHead>{t("columnActor")}</TableHead>
                  <TableHead>{t("columnResource")}</TableHead>
                  <TableHead>{t("columnRequestId")}</TableHead>
                  <TableHead className="text-right">{t("columnActions")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {logs.map((item) => (
                  <TableRow
                    key={item.id}
                    tabIndex={0}
                    className="cursor-pointer"
                    onClick={() => void openDetail(item.id)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        void openDetail(item.id);
                      }
                    }}
                  >
                    <TableCell>
                      <StatusBadge variant={actionBadgeVariant(item.action)}>
                        {item.action}
                      </StatusBadge>
                    </TableCell>
                    <TableCell className="text-muted-foreground text-dense font-mono">
                      {formatDateTime(item.created_at)}
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {item.actor_user_id || t("system")}
                    </TableCell>
                    <TableCell className="text-muted-foreground font-mono text-xs">
                      {item.resource_type}:{item.resource_id}
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      <span className="inline-flex items-center gap-1">
                        <span className="text-dense font-mono">{item.request_id.slice(0, 8)}…</span>
                        <Button
                          variant="ghost"
                          size="icon-xs"
                          aria-label={tCommon("copy")}
                          onClick={(event) => {
                            event.stopPropagation();
                            void copyToClipboard(item.request_id);
                          }}
                        >
                          <IconCopy />
                        </Button>
                      </span>
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={(event) => {
                          event.stopPropagation();
                          void openDetail(item.id);
                        }}
                      >
                        {t("viewDetail")}
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}

          <div className="mt-4 flex items-center justify-between">
            <span className="text-muted-foreground text-sm">
              {tCommon("pageOf", { current: currentPage, total: totalPages })}
            </span>
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={offset === 0}
                onClick={() => void loadAudit(Math.max(0, offset - PAGE_SIZE))}
              >
                {tCommon("previousPage")}
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={offset + PAGE_SIZE >= total}
                onClick={() => void loadAudit(offset + PAGE_SIZE)}
              >
                {tCommon("nextPage")}
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      <Sheet
        open={detail != null || detailLoading}
        onOpenChange={(open) => !open && setDetail(null)}
      >
        <SheetContent className="overflow-y-auto sm:max-w-lg">
          <SheetHeader>
            <SheetTitle>{t("auditDetailTitle")}</SheetTitle>
            <SheetDescription>{detail?.action}</SheetDescription>
          </SheetHeader>
          {detailLoading ? (
            <div className="flex justify-center py-12">
              <LoadingSpinner />
            </div>
          ) : detail ? (
            <dl className="mt-4 grid gap-3 text-sm">
              <div>
                <dt className="text-muted-foreground text-xs">{t("system")}</dt>
                <dd className="mt-0.5">{detail.actor_user_id || t("system")}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground text-xs">{t("actorIpLabel")}</dt>
                <dd className="mt-0.5">{detail.actor_ip || "—"}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground text-xs" translate="no">
                  request_id
                </dt>
                <dd className="mt-0.5 flex items-center gap-1">
                  <span className="font-mono text-xs">{detail.request_id || "—"}</span>
                  {detail.request_id ? (
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      aria-label={tCommon("copy")}
                      onClick={() => void copyToClipboard(detail.request_id ?? "")}
                    >
                      <IconCopy />
                    </Button>
                  ) : null}
                </dd>
              </div>
              <div>
                <dt className="text-muted-foreground text-xs">{t("chainSeqLabel")}</dt>
                <dd className="mt-0.5 flex items-center gap-1">
                  <span className="font-mono text-xs">{detail.chain_seq ?? "—"}</span>
                  {detail.chain_seq != null ? (
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      aria-label={tCommon("copy")}
                      onClick={() => void copyToClipboard(String(detail.chain_seq))}
                    >
                      <IconCopy />
                    </Button>
                  ) : null}
                </dd>
              </div>
              <div>
                <dt className="text-muted-foreground text-xs">{t("metadataLabel")}</dt>
                <dd className="mt-1">
                  <pre className="bg-muted max-h-64 overflow-auto rounded-lg p-3 text-xs">
                    {JSON.stringify(detail.metadata ?? {}, null, 2)}
                  </pre>
                </dd>
              </div>
              <div>
                <dt className="text-muted-foreground text-xs">
                  {formatDateTime(detail.created_at)}
                </dt>
              </div>
            </dl>
          ) : null}
        </SheetContent>
      </Sheet>
    </div>
  );
}
