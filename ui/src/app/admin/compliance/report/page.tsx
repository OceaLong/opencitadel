"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { Download, Loader2 } from "lucide-react";

import { AdminTimeRangePicker } from "@/components/admin/time-range-picker";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

import { useCapabilities } from "@/hooks/use-capabilities";
import { type AdminTimeRange, getAdminDateRange } from "@/lib/admin-utils";
import { isCapabilityAvailable } from "@/lib/api/capabilities";
import { complianceApi, type ComplianceReport } from "@/lib/api/compliance";

import { controlStatusVariant } from "./compliance-status";

export default function AdminComplianceReportPage() {
  const t = useTranslations("compliance");
  const tCap = useTranslations("capabilities");
  const { capability } = useCapabilities();
  const pdfState = capability("report_pdf");
  const pdfAvailable = isCapabilityAvailable(pdfState);
  // report_pdf 不可用时后端在本页作用域内回传的 reason_key 恒为 pdfRendererUnavailable,
  // 直接映射到对应 i18n 文案作为禁用态 tooltip。
  const pdfDisabledReason = tCap("reason.pdfRendererUnavailable");
  const [range, setRange] = useState<AdminTimeRange>("30d");
  const [framework, setFramework] = useState<string>("all");
  const [report, setReport] = useState<ComplianceReport | null>(null);
  const [loading, setLoading] = useState(false);

  const generate = async () => {
    setLoading(true);
    try {
      const dateParams = getAdminDateRange(range);
      const res = await complianceApi.getComplianceReport({
        framework: framework === "all" ? undefined : framework,
        start: dateParams.start_at,
        end: dateParams.end_at,
      });
      setReport(res.report);
    } finally {
      setLoading(false);
    }
  };

  const dateParams = getAdminDateRange(range);

  return (
    <div className="space-y-6">
      <PageHeader title={t("reportTitle")} description={t("reportDesc")} />

      <Card>
        <CardContent className="flex flex-wrap items-center gap-3 pt-6">
          <AdminTimeRangePicker value={range} onChange={setRange} />
          <Select value={framework} onValueChange={setFramework}>
            <SelectTrigger className="w-full sm:w-[180px]">
              <SelectValue placeholder={t("frameworkAll")} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t("frameworkAll")}</SelectItem>
              <SelectItem value="djbh2.0">{t("frameworkDjbh")}</SelectItem>
              <SelectItem value="iso27001">{t("frameworkIso")}</SelectItem>
            </SelectContent>
          </Select>
          <Button onClick={() => void generate()} disabled={loading}>
            {loading && <Loader2 className="mr-1 size-4 animate-spin" />}
            {t("generateReport")}
          </Button>
          <Button variant="outline" asChild>
            <a
              href={complianceApi.complianceReportUrl({
                framework: framework === "all" ? undefined : framework,
                start: dateParams.start_at,
                end: dateParams.end_at,
                format: "md",
              })}
            >
              {t("exportMd")}
            </a>
          </Button>
          {pdfAvailable ? (
            <Button variant="outline" asChild>
              <a
                href={complianceApi.complianceReportUrl({
                  framework: framework === "all" ? undefined : framework,
                  start: dateParams.start_at,
                  end: dateParams.end_at,
                  format: "pdf",
                })}
              >
                <Download className="mr-1 size-4" />
                {t("exportPdf")}
              </a>
            </Button>
          ) : (
            <Tooltip>
              <TooltipTrigger asChild>
                {/* 禁用态用 span 包裹,保证 disabled 按钮仍能触发 tooltip */}
                <span tabIndex={0}>
                  <Button variant="outline" disabled>
                    <Download className="mr-1 size-4" />
                    {t("exportPdf")}
                  </Button>
                </span>
              </TooltipTrigger>
              <TooltipContent side="top">{pdfDisabledReason}</TooltipContent>
            </Tooltip>
          )}
        </CardContent>
      </Card>

      {report && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">{t("reportSummary")}</CardTitle>
            <CardDescription>
              {t("generatedAt")}: {report.generated_at}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap gap-2">
              <StatusBadge variant="success">
                {t("passCount")}: {report.summary.pass}
              </StatusBadge>
              <StatusBadge variant="destructive">
                {t("gapCount")}: {report.summary.gap}
              </StatusBadge>
              <StatusBadge variant="warning">
                {t("attentionCount")}: {report.summary.attention}
              </StatusBadge>
              <StatusBadge variant="secondary">
                {t("notVerifiedCount")}: {report.summary.not_verified}
              </StatusBadge>
              <StatusBadge variant="secondary">
                {t("naCount")}: {report.summary.na}
              </StatusBadge>
            </div>
            <div className="max-h-[480px] space-y-3 overflow-auto">
              {report.controls.map((c) => (
                <div
                  key={`${c.framework}-${c.control_id}`}
                  className="rounded-lg border p-3 text-sm"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium">
                      [{c.framework}] {c.control_id} {c.title}
                    </span>
                    <StatusBadge variant={controlStatusVariant(c.status)}>{c.status}</StatusBadge>
                  </div>
                  <p className="text-muted-foreground mt-1 text-xs">{c.requirement}</p>
                  <p className="mt-1 text-xs">{c.evidence.join(" · ")}</p>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {!report && !loading && <EmptyState title={t("reportHint")} className="py-10" />}
    </div>
  );
}
