"use client";
import { useLocale, useTranslations } from "next-intl";
export function RunStatus({ status }: { status: string | null | undefined }) {
  const t = useTranslations("executionDetail");
  const labels: Record<string, string> = {
    new: t("statusValue.new"),
    queued: t("statusValue.queued"),
    running: t("statusValue.running"),
    waiting: t("statusValue.waiting"),
    completed: t("statusValue.completed"),
    failed: t("statusValue.failed"),
    cancelled: t("statusValue.cancelled"),
    deferred: t("statusValue.deferred"),
    unknown: t("statusValue.unknown"),
  };
  return <span className="whitespace-nowrap">{labels[status ?? "unknown"] ?? labels.unknown}</span>;
}
export function RunDate({
  value,
  timezone,
}: {
  value: string | null | undefined;
  timezone: string;
}) {
  const locale = useLocale();
  const t = useTranslations("analysis");
  if (!value || !Number.isFinite(Date.parse(value))) return <span>{t("unknown")}</span>;
  const label = new Intl.DateTimeFormat(locale, {
    timeZone: timezone,
    dateStyle: "short",
    timeStyle: "short",
  }).format(new Date(value));
  return (
    <time
      dateTime={value}
      title={`${value} · ${timezone}`}
      aria-label={`${value} · ${timezone}`}
      className="whitespace-nowrap"
    >
      {label}
    </time>
  );
}
