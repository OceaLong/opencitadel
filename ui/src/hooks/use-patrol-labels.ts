"use client";

import { useTranslations } from "next-intl";

export function usePatrolLabels() {
  const t = useTranslations("patrol");

  return {
    status: {
      active: t("status.active"),
      cancelled: t("status.cancelled"),
      completed: t("status.completed"),
      completed_with_findings: t("status.completed_with_findings"),
      draft: t("status.draft"),
      error: t("status.error"),
      fail: t("status.fail"),
      failed: t("status.failed"),
      invalid: t("status.invalid"),
      pass: t("status.pass"),
      paused: t("status.paused"),
      queued: t("status.queued"),
      running: t("status.running"),
      skipped: t("status.skipped"),
      validating: t("status.validating"),
      warn: t("status.warn"),
    } as Record<string, string>,
    trigger: {
      manual: t("trigger.manual"),
      remediation: t("trigger.remediation"),
      replay: t("trigger.replay"),
      schedule: t("trigger.schedule"),
      webhook: t("trigger.webhook"),
    } as Record<string, string>,
    severity: {
      critical: t("severity.critical"),
      info: t("severity.info"),
      warning: t("severity.warning"),
    } as Record<string, string>,
    findingStatus: {
      acknowledged: t("findingStatus.acknowledged"),
      false_positive: t("findingStatus.false_positive"),
      open: t("findingStatus.open"),
      resolved: t("findingStatus.resolved"),
    } as Record<string, string>,
    remediationStatus: {
      cancelled: t("remediation.status.cancelled"),
      executed: t("remediation.status.executed"),
      executing: t("remediation.status.executing"),
      failed: t("remediation.status.failed"),
      proposed: t("remediation.status.proposed"),
      verified: t("remediation.status.verified"),
    } as Record<string, string>,
    remediationAction: {
      restart_workload: t("remediation.action.restart_workload"),
      rollback_workload: t("remediation.action.rollback_workload"),
      scale_workload: t("remediation.action.scale_workload"),
    } as Record<string, string>,
  };
}
