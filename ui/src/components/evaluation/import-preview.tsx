"use client";
import { useEffect, useId, useState } from "react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";

import type { ImportPreview as Preview } from "@/lib/api/types/evaluations";
import { canApplyImport } from "@/lib/evaluation-view/validation";
export function ImportPreview({
  preview,
  revision,
  pending,
  canManage,
  onApply,
}: {
  preview: Preview;
  revision: number;
  pending: boolean;
  canManage: boolean;
  onApply: () => void;
}) {
  const t = useTranslations("evaluations");
  const id = useId();
  const [confirmedId, setConfirmedId] = useState<string | null>(null);
  const confirmed = confirmedId === preview.import_id;
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [preview.import_id]);
  const expired = Date.parse(preview.expires_at) <= now;
  const valid = canApplyImport(preview.errors.length, preview.revision === revision) && !expired;
  return (
    <section aria-labelledby={`${id}-title`} className="min-w-0 space-y-3 rounded-md border p-3">
      <h2 id={`${id}-title`} className="font-semibold">
        {t("importPreview")}
      </h2>
      <p className="text-muted-foreground text-sm">{t("replaceWarning")}</p>
      {!valid && (
        <p role="status">
          {t(expired ? "expired" : preview.revision !== revision ? "conflict" : "importErrors")}
        </p>
      )}
      <div className="grid gap-2 sm:grid-cols-3">
        {(["added", "replaced", "removed"] as const).map((kind) => (
          <div key={kind}>
            <h3 className="text-sm font-medium">
              {t(kind)} ({preview[kind]?.length ?? 0})
            </h3>
            <ul className="max-h-32 overflow-auto text-sm break-words">
              {preview[kind]?.map((key) => (
                <li key={key}>{key}</li>
              ))}
            </ul>
          </div>
        ))}
      </div>
      {preview.errors.length > 0 && (
        <div className="max-h-64 overflow-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr>
                <th>{t("row")}</th>
                <th>{t("field")}</th>
                <th>{t("error")}</th>
              </tr>
            </thead>
            <tbody>
              {preview.errors.map((error, index) => (
                <tr key={index}>
                  <td>{error.row}</td>
                  <td>{error.field}</td>
                  <td className="break-all">
                    {error.code === "duplicate_case_key"
                      ? t("importDuplicate")
                      : error.code === "byte_limit_exceeded" || error.code === "case_limit_exceeded"
                        ? t("importLimit")
                        : error.code === "schema_version" ||
                            error.code === "import_shape" ||
                            error.code === "csv_columns"
                          ? t("importShape")
                          : t("importInvalidField")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <label className="flex min-h-9 items-start gap-2">
        <input
          type="checkbox"
          checked={confirmed}
          disabled={!valid || pending || !canManage}
          onChange={(e) => setConfirmedId(e.target.checked ? preview.import_id : null)}
        />
        <span>{t("confirmReplace")}</span>
      </label>
      <Button disabled={!confirmed || !valid || pending || !canManage} onClick={onApply}>
        {t("applyImport")}
      </Button>
    </section>
  );
}
