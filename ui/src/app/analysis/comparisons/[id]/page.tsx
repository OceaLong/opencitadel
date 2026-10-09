"use client";
import { useParams, useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";

import { AnalysisBoundary } from "@/components/analysis/analysis-boundary";
import { ComparisonWorkspace } from "@/components/analysis/comparison-workspace";
export default function Page() {
  const { id } = useParams<{ id: string }>();
  const search = useSearchParams();
  const t = useTranslations("analysis");
  const revision = Number(search.get("revision"));
  return (
    <AnalysisBoundary>
      {(access) =>
        Number.isInteger(revision) && revision >= 1 ? (
          <ComparisonWorkspace
            key={`${id}:${revision}`}
            access={access}
            id={id}
            revision={revision}
          />
        ) : (
          <p role="alert">{t("revisionRequired")}</p>
        )
      }
    </AnalysisBoundary>
  );
}
