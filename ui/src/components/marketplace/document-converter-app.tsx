"use client";

import { useMemo, useRef, useState } from "react";
import { Download, FileText, Loader2, Printer } from "lucide-react";
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

const MAX_SIZE = 20 * 1024 * 1024;

const CONVERSION_MATRIX: Record<string, string[]> = {
  md: ["pdf", "docx"],
  txt: ["pdf"],
  pdf: ["docx", "md", "txt"],
  docx: ["md", "txt"],
};

const FORMAT_LABELS: Record<string, string> = {
  pdf: "PDF",
  docx: "Word (DOCX)",
  md: "Markdown",
  txt: "纯文本",
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

function printTextAsPdf(content: string, title: string) {
  const printWindow = window.open("", "_blank", "noopener,noreferrer,width=900,height=700");
  if (!printWindow) {
    toast.error("无法打开打印窗口，请允许弹窗后重试");
    return;
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
  toast.message("请在打印对话框中选择「另存为 PDF」");
}

export function DocumentConverterApp({
  initialTargetFormat,
}: {
  initialTargetFormat?: "pdf" | "docx" | "md" | "txt";
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [sourceExt, setSourceExt] = useState("");
  const [targetFormat, setTargetFormat] = useState(initialTargetFormat ?? "");
  const [loading, setLoading] = useState(false);
  const [resultFileId, setResultFileId] = useState<string | null>(null);
  const [resultFilename, setResultFilename] = useState("");

  const targetOptions = useMemo(() => {
    if (!sourceExt) return [];
    return CONVERSION_MATRIX[sourceExt] ?? [];
  }, [sourceExt]);

  const handleFile = (picked: File | undefined) => {
    if (!picked) return;
    if (picked.size > MAX_SIZE) {
      toast.error("文件不能超过 20MB");
      return;
    }
    const ext = getExtension(picked.name);
    if (!CONVERSION_MATRIX[ext]) {
      toast.error("仅支持 md、txt、pdf、docx 文件");
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
      toast.error("请选择文件和目标格式");
      return;
    }

    if (isLocalConversion(sourceExt, targetFormat)) {
      const text = await file.text();
      printTextAsPdf(text, file.name);
      return;
    }

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
      toast.message("转换完成，可下载结果文件");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "转换失败");
    } finally {
      setLoading(false);
    }
  };

  const download = () => {
    if (!resultFileId) return;
    const link = document.createElement("a");
    link.href = fileApi.getFileDownloadUrl(resultFileId);
    link.download = resultFilename || "converted";
    link.click();
  };

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-foreground text-lg font-semibold tracking-tight">文档格式转换</h2>
        <p className="text-muted-foreground mt-1 text-sm">
          支持 md/txt 本地转 PDF，PDF 转 Word、常用文档格式互转（docx→pdf 暂不支持）
        </p>
      </div>

      <Card>
        <CardContent className="space-y-4 py-5">
          <div className="space-y-2">
            <Label>源文件</Label>
            <input
              ref={fileRef}
              type="file"
              accept={ACCEPT}
              className="hidden"
              onChange={(e) => handleFile(e.target.files?.[0])}
            />
            <Button variant="outline" onClick={() => fileRef.current?.click()}>
              <FileText className="size-4" />
              选择文档
            </Button>
            {file && (
              <p className="text-muted-foreground text-xs">
                已选择：{file.name}（{FORMAT_LABELS[sourceExt] ?? sourceExt}）
              </p>
            )}
          </div>

          <div className="space-y-2">
            <Label>目标格式</Label>
            <Select
              value={targetFormat}
              onValueChange={setTargetFormat}
              disabled={!sourceExt}
            >
              <SelectTrigger>
                <SelectValue placeholder="选择目标格式" />
              </SelectTrigger>
              <SelectContent>
                {targetOptions.map((fmt) => (
                  <SelectItem key={fmt} value={fmt}>
                    {FORMAT_LABELS[fmt] ?? fmt}
                    {isLocalConversion(sourceExt, fmt) ? "（本地打印）" : ""}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <Button onClick={convert} disabled={!file || !targetFormat || loading}>
            {loading ? <Loader2 className="size-4 animate-spin" /> : isLocalConversion(sourceExt, targetFormat) ? <Printer className="size-4" /> : <FileText className="size-4" />}
            {isLocalConversion(sourceExt, targetFormat) ? "打印为 PDF" : "开始转换"}
          </Button>
        </CardContent>
      </Card>

      {resultFileId && (
        <Card className="border-primary/20 bg-primary/5">
          <CardContent className="flex flex-col gap-3 py-5 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-foreground text-sm font-medium">转换完成</p>
              <p className="text-muted-foreground text-xs">{resultFilename}</p>
            </div>
            <Button variant="outline" onClick={download}>
              <Download className="size-4" />
              下载文件
            </Button>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
