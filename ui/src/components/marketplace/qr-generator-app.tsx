"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Download, QrCode } from "lucide-react";
import QRCode from "qrcode";
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
import { Textarea } from "@/components/ui/textarea";

type ErrorLevel = "L" | "M" | "Q" | "H";

export function QrGeneratorApp({ initialText = "" }: { initialText?: string }) {
  const [text, setText] = useState(initialText);
  const [errorLevel, setErrorLevel] = useState<ErrorLevel>("M");
  const [dataUrl, setDataUrl] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const generateQr = useCallback(async (value: string, level: ErrorLevel) => {
    const trimmed = value.trim();
    if (!trimmed) {
      setDataUrl(null);
      return;
    }

    setGenerating(true);
    try {
      const url = await QRCode.toDataURL(trimmed, {
        errorCorrectionLevel: level,
        margin: 2,
        width: 280,
      });
      setDataUrl(url);
    } catch {
      setDataUrl(null);
      toast.error("二维码生成失败，请检查输入内容");
    } finally {
      setGenerating(false);
    }
  }, []);

  const scheduleGenerate = useCallback(
    (value: string, level: ErrorLevel) => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => {
        void generateQr(value, level);
      }, 300);
    },
    [generateQr],
  );

  useEffect(() => {
    scheduleGenerate(text, errorLevel);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [text, errorLevel, scheduleGenerate]);

  const download = () => {
    if (!dataUrl) return;
    const link = document.createElement("a");
    link.href = dataUrl;
    link.download = "qrcode.png";
    link.click();
    toast.message("二维码已下载");
  };

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-foreground text-lg font-semibold tracking-tight">二维码生成器</h2>
        <p className="text-muted-foreground mt-1 text-sm">
          输入文本、链接或联系方式，实时生成可下载的二维码
        </p>
      </div>

      <Card>
        <CardContent className="space-y-4 py-5">
          <div className="space-y-2">
            <Label htmlFor="qr-text">内容</Label>
            <Textarea
              id="qr-text"
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="例如：https://example.com 或 微信号 / 联系方式"
              className="min-h-28"
            />
          </div>
          <div className="space-y-2">
            <Label>纠错级别</Label>
            <Select value={errorLevel} onValueChange={(value) => setErrorLevel(value as ErrorLevel)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="L">低 (7%)</SelectItem>
                <SelectItem value="M">中 (15%)</SelectItem>
                <SelectItem value="Q">较高 (25%)</SelectItem>
                <SelectItem value="H">高 (30%)</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </CardContent>
      </Card>

      <Card className="border-primary/20 bg-primary/5">
        <CardContent className="flex flex-col items-center gap-4 py-8">
          {dataUrl ? (
            <>
              <img src={dataUrl} alt="生成的二维码" className="size-56 rounded-xl bg-white p-3" />
              <Button variant="outline" onClick={download}>
                <Download className="size-4" />
                下载 PNG
              </Button>
            </>
          ) : (
            <div className="text-muted-foreground flex flex-col items-center gap-2 py-8 text-center">
              <QrCode className="size-10 opacity-40" />
              <p className="text-sm">{generating ? "生成中..." : "输入内容后自动生成二维码"}</p>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
