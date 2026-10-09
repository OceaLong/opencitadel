"use client";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";

import { analysisReturn } from "@/lib/analysis-view/selection";
export function AnalysisReturnLink() {
  const search = useSearchParams();
  const t = useTranslations("analysis");
  const href = analysisReturn(search.get("analysis_return"));
  return href ? (
    <a className="underline" href={href}>
      {t("backToAnalysis")}
    </a>
  ) : null;
}
