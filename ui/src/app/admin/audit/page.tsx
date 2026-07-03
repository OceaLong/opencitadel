"use client";

import { useCallback, useEffect, useState } from "react";
import { Download, Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";

import { AdminTimeRangePicker } from "@/components/admin/time-range-picker";
import { AuditActivityChart } from "@/components/admin/usage-charts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

import { type AdminTimeRange, formatDateTime, getAdminDateRange } from "@/lib/admin-utils";
import { adminApi, type AuditLog } from "@/lib/api/admin";

const PAGE_SIZE = 20;

export default function AdminAuditPage() {
  const t = useTranslations("admin");
  const tCommon = useTranslations("common");
  const [range, setRange] = useState<AdminTimeRange>("30d");
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [byDay, setByDay] = useState<Array<{ date: string; count: number }>>([]);
  const [byAction, setByAction] = useState<Array<{ action: string; count: number }>>([]);

  const loadAudit = useCallback(async (nextOffset: number) => {
    setLoading(true);
    const dateParams = getAdminDateRange(range);
    try {
      const [auditData, summary] = await Promise.all([
        adminApi.audit({ ...dateParams, limit: PAGE_SIZE, offset: nextOffset }),
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
  }, [range]);

  useEffect(() => {
    void loadAudit(0);
  }, [loadAudit]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-2xl font-semibold tracking-tight">{t("auditLog")}</h2>
          <p className="text-muted-foreground mt-1 text-sm">{t("auditSubtitle")}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <AdminTimeRangePicker value={range} onChange={setRange} />
          <Button variant="outline" asChild>
            <a href={adminApi.exportAuditCsvUrl()}>
              <Download className="mr-1 size-4" />
              {t("exportCsv")}
            </a>
          </Button>
        </div>
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
              <div className="text-muted-foreground py-10 text-center text-sm">{t("noAuditData")}</div>
            ) : (
              byAction.slice(0, 10).map((item) => (
                <div key={item.action} className="flex items-center justify-between rounded-lg border px-3 py-2 text-sm">
                  <span className="truncate">{item.action}</span>
                  <Badge variant="secondary">{item.count}</Badge>
                </div>
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
              <Loader2 className="size-6 animate-spin" />
            </div>
          ) : logs.length === 0 ? (
            <div className="text-muted-foreground py-10 text-center text-sm">{t("noAuditRecords")}</div>
          ) : (
            <div className="space-y-2">
              {logs.map((item) => (
                <div key={item.id} className="rounded-lg border px-3 py-3 text-sm">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant="outline">{item.action}</Badge>
                      <span className="text-muted-foreground text-xs">{formatDateTime(item.created_at)}</span>
                    </div>
                    <span className="text-muted-foreground text-xs">{item.actor_user_id || t("system")}</span>
                  </div>
                  <div className="text-muted-foreground mt-2 text-xs">
                    {item.resource_type}:{item.resource_id}
                  </div>
                </div>
              ))}
            </div>
          )}

          <div className="mt-4 flex items-center justify-between">
            <span className="text-muted-foreground text-sm">
              {tCommon("pageOf", { current: currentPage, total: totalPages })}
            </span>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => void loadAudit(Math.max(0, offset - PAGE_SIZE))}>
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
    </div>
  );
}
