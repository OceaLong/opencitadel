"use client";

import { useRef, useState } from "react";
import { Languages, Loader2, Upload } from "lucide-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

import { fileApi } from "@/lib/api/file";
import { marketplaceApi } from "@/lib/api/marketplace";
import type { TranslationData, TranslationParams } from "@/lib/api/types";
import { useRequireAuth } from "@/hooks/use-require-auth";

const MAX_SIZE = 8 * 1024 * 1024;

type TranslationStyle = TranslationParams["style"];

export function SmartTranslationApp({
  initialText = "",
  initialTargetLanguage,
  initialStyle = "plain",
}: {
  initialText?: string;
  initialTargetLanguage?: string;
  initialStyle?: TranslationStyle;
}) {
  const t = useTranslations("marketplaceApps.smartTranslation");
  const tShared = useTranslations("marketplaceApps.shared");
  const tAuth = useTranslations("auth");
  const tCommon = useTranslations("common");
  const { requireAuth } = useRequireAuth();
  const fileRef = useRef<HTMLInputElement>(null);
  const [text, setText] = useState(initialText);
  const [targetLanguage, setTargetLanguage] = useState(
    initialTargetLanguage ?? t("defaultTargetLanguage"),
  );
  const [style, setStyle] = useState<TranslationStyle>(initialStyle);
  const [fileId, setFileId] = useState("");
  const [fileName, setFileName] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<TranslationData | null>(null);

  const handleFile = async (file: File | undefined) => {
    if (!file) return;
    if (!requireAuth(tAuth("loginToTranslate"))) return;
    if (file.size > MAX_SIZE) {
      toast.error(tShared("fileTooLarge8mb"));
      return;
    }
    setLoading(true);
    try {
      const uploaded = await fileApi.uploadFile({ file });
      setFileId(uploaded.id);
      setFileName(file.name);
      toast.message(t("fileUploadedToast"));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : tCommon("uploadFailed"));
    } finally {
      setLoading(false);
    }
  };

  const translate = async () => {
    if (!text.trim() && !fileId) {
      toast.error(t("textOrFileRequired"));
      return;
    }
    if (!requireAuth(tAuth("loginToTranslate"))) return;
    setLoading(true);
    setResult(null);
    try {
      const data = await marketplaceApi.translate({
        text: text.trim() || undefined,
        file_id: fileId || undefined,
        target_language: targetLanguage,
        style,
      });
      setResult(data);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t("translateFailed"));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-foreground text-lg font-semibold tracking-tight">{t("title")}</h2>
        <p className="text-muted-foreground mt-1 text-sm">{t("subtitle")}</p>
      </div>

      <Card>
        <CardContent className="space-y-4 py-5">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="target-language">{t("targetLanguageLabel")}</Label>
              <Input
                id="target-language"
                value={targetLanguage}
                onChange={(e) => setTargetLanguage(e.target.value)}
                placeholder={t("targetLanguagePlaceholder")}
              />
            </div>
            <div className="space-y-2">
              <Label>{t("styleLabel")}</Label>
              <Select value={style} onValueChange={(value) => setStyle(value as TranslationStyle)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="plain">{t("stylePlain")}</SelectItem>
                  <SelectItem value="formal">{t("styleFormal")}</SelectItem>
                  <SelectItem value="casual">{t("styleCasual")}</SelectItem>
                  <SelectItem value="technical">{t("styleTechnical")}</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="space-y-2">
            <Label htmlFor="source-text">{t("sourceTextLabel")}</Label>
            <Textarea
              id="source-text"
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder={t("sourceTextPlaceholder")}
              className="min-h-36"
            />
          </div>

          <input
            ref={fileRef}
            type="file"
            className="hidden"
            accept="image/*,.txt,.md,.csv,.json,.log"
            onChange={(e) => handleFile(e.target.files?.[0])}
          />
          <div className="flex flex-col gap-2 sm:flex-row">
            <Button variant="outline" onClick={() => fileRef.current?.click()} disabled={loading}>
              <Upload className="size-4" />
              {fileName || t("uploadButton")}
            </Button>
            <Button onClick={translate} disabled={loading}>
              {loading ? <Loader2 className="size-4 animate-spin" /> : <Languages className="size-4" />}
              {t("startTranslate")}
            </Button>
          </div>
        </CardContent>
      </Card>

      {result && (
        <Card className="border-primary/20 bg-primary/5">
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-base">
              <Languages className="size-4" />
              {t("resultTitle")}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="text-muted-foreground flex flex-wrap gap-2 text-xs">
              <span>{t("detectedLanguage", { lang: result.detected_language })}</span>
              <span>{t("targetLanguageResult", { lang: result.target_language })}</span>
            </div>
            <div className="border-border/70 bg-background/80 text-foreground rounded-xl border p-4 text-sm leading-relaxed whitespace-pre-wrap">
              {result.translated_text}
            </div>
            {result.notes.length > 0 && (
              <ul className="text-muted-foreground list-disc space-y-1 pl-5 text-xs">
                {result.notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
