"use client";

import { useRef, useState } from "react";
import { Languages, Loader2, Upload } from "lucide-react";
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

const MAX_SIZE = 8 * 1024 * 1024;

type TranslationStyle = TranslationParams["style"];

export function SmartTranslationApp({
  initialText = "",
  initialTargetLanguage = "中文",
  initialStyle = "plain",
}: {
  initialText?: string;
  initialTargetLanguage?: string;
  initialStyle?: TranslationStyle;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [text, setText] = useState(initialText);
  const [targetLanguage, setTargetLanguage] = useState(initialTargetLanguage);
  const [style, setStyle] = useState<TranslationStyle>(initialStyle);
  const [fileId, setFileId] = useState("");
  const [fileName, setFileName] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<TranslationData | null>(null);

  const handleFile = async (file: File | undefined) => {
    if (!file) return;
    if (file.size > MAX_SIZE) {
      toast.error("文件不能超过 8MB");
      return;
    }
    setLoading(true);
    try {
      const uploaded = await fileApi.uploadFile({ file });
      setFileId(uploaded.id);
      setFileName(file.name);
      toast.message("文件已上传，可结合文本一起翻译");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "上传失败");
    } finally {
      setLoading(false);
    }
  };

  const translate = async () => {
    if (!text.trim() && !fileId) {
      toast.error("请输入文本或上传图片/文本文件");
      return;
    }
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
      toast.error(e instanceof Error ? e.message : "翻译失败");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-foreground text-lg font-semibold tracking-tight">智能翻译</h2>
        <p className="text-muted-foreground mt-1 text-sm">
          自动识别语种，并按场景风格输出更自然的译文
        </p>
      </div>

      <Card>
        <CardContent className="space-y-4 py-5">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="target-language">目标语言</Label>
              <Input
                id="target-language"
                value={targetLanguage}
                onChange={(e) => setTargetLanguage(e.target.value)}
                placeholder="中文 / English / 日本語"
              />
            </div>
            <div className="space-y-2">
              <Label>翻译风格</Label>
              <Select value={style} onValueChange={(value) => setStyle(value as TranslationStyle)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="plain">自然准确</SelectItem>
                  <SelectItem value="formal">正式商务</SelectItem>
                  <SelectItem value="casual">口语轻松</SelectItem>
                  <SelectItem value="technical">技术文档</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="space-y-2">
            <Label htmlFor="source-text">原文</Label>
            <Textarea
              id="source-text"
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="粘贴需要翻译的文本，也可以上传截图或文本文件"
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
              {fileName || "上传图片/文本"}
            </Button>
            <Button onClick={translate} disabled={loading}>
              {loading ? <Loader2 className="size-4 animate-spin" /> : <Languages className="size-4" />}
              开始翻译
            </Button>
          </div>
        </CardContent>
      </Card>

      {result && (
        <Card className="border-primary/20 bg-primary/5">
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-base">
              <Languages className="size-4" />
              翻译结果
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="text-muted-foreground flex flex-wrap gap-2 text-xs">
              <span>识别语言：{result.detected_language}</span>
              <span>目标语言：{result.target_language}</span>
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
