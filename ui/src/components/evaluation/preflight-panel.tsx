"use client";
import { useState } from "react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";

import type { PreflightResult } from "@/lib/api/types/evaluations";

import { Check } from "./form-fields";
export function PreflightPanel({
  result,
  pending,
  canRun,
  onCheck,
  onStart,
}: {
  result: PreflightResult | null;
  pending: boolean;
  canRun: boolean;
  onCheck: () => void;
  onStart: () => void;
}) {
  const t = useTranslations("evaluations");
  const issue = (code: string) => {
    if (code.includes("price")) return t("issuePrice");
    if (code.includes("budget") || code.includes("limit")) return t("issueBudget");
    if (code.includes("judge")) return t("issueJudge");
    if (code.includes("configuration") || code.includes("credential"))
      return t("issueConfiguration");
    if (code.includes("dataset") || code.includes("resource")) return t("issueResource");
    if (code === "version_unpinned") return t("versionUnpinned");
    return t("issueDependency");
  };
  const [confirmedRevision, setConfirmedRevision] = useState<number | null>(null);
  const confirmed = !!result && confirmedRevision === result.revision;
  return (
    <section className="space-y-3 rounded-md border p-3">
      <h2 className="font-semibold">{t("preflight")}</h2>
      <Button
        type="button"
        variant="outline"
        disabled={pending}
        onClick={() => {
          setConfirmedRevision(null);
          onCheck();
        }}
      >
        {t("checkPreflight")}
      </Button>
      {result && (
        <>
          <dl className="grid grid-cols-[1fr_auto] gap-2 text-sm">
            <dt>{t("results")}</dt>
            <dd>{result.quantity}</dd>
            <dt>{t("physicalCalls")}</dt>
            <dd>{result.physical_call_upper_bound ?? t("unknown")}</dd>
            <dt>{t("priceCoverage")}</dt>
            <dd>{t(result.price_coverage)}</dd>
            <dt>{t("tokenBudget")}</dt>
            <dd>{result.token_budget}</dd>
            <dt>{t("environmentReady")}</dt>
            <dd>{t(result.environment_ready ? "ready" : "unavailable")}</dd>
          </dl>
          <ul className="space-y-1 text-sm">
            {result.errors.map((error, i) => (
              <li key={i}>
                {t("blocked")}: {issue(error)}
              </li>
            ))}
            {result.warnings?.map((warning, i) => (
              <li key={i}>
                {t("warning")}: {issue(warning)}
              </li>
            ))}
          </ul>
          <p className="text-sm">{t("startCosts")}</p>
          <Check
            label={t("confirmStart")}
            checked={confirmed}
            onChange={(checked) => setConfirmedRevision(checked ? result.revision : null)}
            disabled={!result.allowed || !canRun || pending}
          />
          <Button
            type="button"
            disabled={!result.allowed || !canRun || pending || !confirmed}
            onClick={onStart}
          >
            {t("start")}
          </Button>
        </>
      )}
    </section>
  );
}
