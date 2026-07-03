"use client";

import { useMemo, useRef, useState } from "react";
import { Download, FileText, Loader2, Printer } from "lucide-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import { fileApi } from "@/lib/api/file";
import { marketplaceApi } from "@/lib/api/marketplace";
import { useRequireAuth } from "@/hooks/use-require-auth";

const MAX_SIZE = 20 * 1024 * 1024;

const CONVERSION_MATRIX: Record<string, string[]> = {
  md: ["pdf", "docx"],
  txt: ["pdf"],
  pdf: ["docx", "md", "txt"],
  docx: ["md", "txt"],
};

const ACCEPT =
  ".md,.txt,.pdf,.docx,text/markdown,text/plain,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document";

function getExtension(filename: string): string {
  const parts = filename.toLowerCase().split(".");
  return parts.length > 1 ? parts[parts.length - 1] : "";
}

function isLocalConversion(source: string, target: string): boolean {
  return (source === "md" || source === "txt") && target === "pdf";
}

function printTextAsPdf(content: string, title: string, printHint: string) {
  const printWindow = window.open("", "_blank", "noopener,noreferrer,width=900,height=700");
  if (!printWindow) {
    return false;
  }
  printWindow.document.write(`<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>${title}</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; line-height: 1.6; padding: 40px; color: #111; }
  pre { white-space: pre-wrap; word-break: break-word; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 14px; }
  h1 { font-size: 20px; margin-bottom: 16px; }
</style></head><body>
<h1>${title}</h1>
<pre>${content.replace(/</g, "&lt;").replace(/>/g, "&gt;")}</pre>
<script>window.onload = () => { window.print(); };</script>
</body></html>`);
  printWindow.document.close();
  toast.message(printHint);
  return true;
}

export function DocumentConverterApp({
  initialTargetFormat,
}: {
  initialTargetFormat?: "pdf" | "docx" | "md" | "txt";
}) {
  const t = useTranslations("marketplaceApps.documentConverter");
  const tShared = useTranslations("marketplaceApps.shared");
  const { requireAuth } = useRequireAuth();
  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [sourceExt, setSourceExt] = useState("");
  const [targetFormat, setTargetFormat] = useState(initialTargetFormat ?? "");
  const [loading, setLoading] = useState(false);
  const [resultFileId, setResultFileId] = useState<string | null>(null);
  const [resultFilename, setResultFilename] = useState("");

  const formatLabels: Record<string, string> = useMemo(
    () => ({
      pdf: t("formatPdf"),
      docx: t("formatDocx"),
      md: t("formatMd"),
      txt: t("formatTxt"),
    }),
    [t],
  );

  const targetOptions = useMemo(() => {
    if (!sourceExt) return [];
    return CONVERSION_MATRIX[sourceExt] ?? [];
  }, [sourceExt]);

  const handleFile = (picked: File | undefined) => {
    if (!picked) return;
    if (picked.size > MAX_SIZE) {
      toast.error(tShared("fileTooLarge20mb"));
      return;
    }
    const ext = getExtension(picked.name);
    if (!CONVERSION_MATRIX[ext]) {
      toast.error(t("unsupportedFormat"));
      return;
    }
    setFile(picked);
    setSourceExt(ext);
    setResultFileId(null);
    const options = CONVERSION_MATRIX[ext];
    const preferred = initialTargetFormat && options.includes(initialTargetFormat)
      ? initialTargetFormat
      : options[0];
    setTargetFormat(preferred);
  };

  const convert = async () => {
    if (!file || !sourceExt || !targetFormat) {
      toast.error(t("selectFileAndFormat"));
      return;
    }

    if (isLocalConversion(sourceExt, targetFormat)) {
      const text = await file.text();
      const opened = printTextAsPdf(text, file.name, t("printHint"));
      if (!opened) {
        toast.error(t("printWindowFailed"));
      }
      return;
    }

    if (!requireAuth(t("loginRequired"))) return;

    setLoading(true);
    setResultFileId(null);
    try {
      const uploaded = await fileApi.uploadFile({ file });
      const data = await marketplaceApi.convertDocument({
        file_id: uploaded.id,
        target_format: targetFormat as "pdf" | "docx" | "md" | "txt",
      });
      setResultFileId(data.result_file_id);
      setResultFilename(data.result_filename);
      toast.message(t("convertCompleteToast"));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("convertFailed"));
    } finally {
      setLoading(false);
    }
  };

  const download = async () => {
    if (!resultFileId) return;
    const blob = await fileApi.downloadFile(resultFileId);
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = resultFilename || "converted";
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-foreground text-lg font-semibold tracking-tight">{t("title")}</h2>
        <p className="text-muted-foreground mt-1 text-sm">{t("subtitle")}</p>
      </div>

      <Card>
        <CardContent className="space-y-4 py-5">
          <div className="space-y-2">
            <Label>{t("sourceFileLabel")}</Label>
            <input
              ref={fileRef}
              type="file"
              accept={ACCEPT}
              className="hidden"
              onChange={(e) => handleFile(e.target.files?.[0])}
            />
            <Button variant="outline" onClick={() => fileRef.current?.click()}>
              <FileText className="size-4" />
              {t("selectDocument")}
            </Button>
            {file && (
              <p className="text-muted-foreground text-xs">
                {t("selectedFile", {
                  name: file.name,
                  format: formatLabels[sourceExt] ?? sourceExt,
                })}
              </p>
            )}
          </div>

          <div className="space-y-2">
            <Label>{t("targetFormatLabel")}</Label>
            <Select
              value={targetFormat}
              onValueChange={setTargetFormat}
              disabled={!sourceExt}
            >
              <SelectTrigger>
                <SelectValue placeholder={t("selectTargetFormatPlaceholder")} />
              </SelectTrigger>
              <SelectContent>
                {targetOptions.map((fmt) => (
                  <SelectItem key={fmt} value={fmt}>
                    {formatLabels[fmt] ?? fmt}
                    {isLocalConversion(sourceExt, fmt) ? t("localPrintSuffix") : ""}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <Button onClick={convert} disabled={!file || !targetFormat || loading}>
            {loading ? <Loader2 className="size-4 animate-spin" /> : isLocalConversion(sourceExt, targetFormat) ? <Printer className="size-4" /> : <FileText className="size-4" />}
            {isLocalConversion(sourceExt, targetFormat) ? t("printAsPdf") : t("startConvert")}
          </Button>
        </CardContent>
      </Card>

      {resultFileId && (
        <Card className="border-primary/20 bg-primary/5">
          <CardContent className="flex flex-col gap-3 py-5 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-foreground text-sm font-medium">{t("convertComplete")}</p>
              <p className="text-muted-foreground text-xs">{resultFilename}</p>
            </div>
            <Button variant="outline" onClick={download}>
              <Download className="size-4" />
              {t("downloadFile")}
            </Button>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
