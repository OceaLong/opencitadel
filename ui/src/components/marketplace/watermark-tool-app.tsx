"use client";

import { useRef, useState } from "react";
import { Download, Droplets, Eraser, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { ImageUploadZone } from "@/components/marketplace/image-upload-zone";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import { fileApi } from "@/lib/api/file";
import { marketplaceApi } from "@/lib/api/marketplace";

const MAX_SIZE = 20 * 1024 * 1024;
const DOC_ACCEPT =
  ".pdf,.png,.jpg,.jpeg,application/pdf,image/jpeg,image/png";

type Mode = "add" | "remove";

function getFileKind(file: File): "image" | "pdf" | "unknown" {
  if (file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf")) return "pdf";
  if (file.type.startsWith("image/")) return "image";
  return "unknown";
}

function applyCanvasTextWatermark(
  imageUrl: string,
  text: string,
  opacity: number,
  rotation: number,
  tile: boolean,
): Promise<string> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      const canvas = document.createElement("canvas");
      canvas.width = img.width;
      canvas.height = img.height;
      const ctx = canvas.getContext("2d");
      if (!ctx) {
        reject(new Error("无法创建画布"));
        return;
      }
      ctx.drawImage(img, 0, 0);
      const fontSize = Math.max(20, Math.floor(Math.min(img.width, img.height) / 16));
      ctx.font = `600 ${fontSize}px sans-serif`;
      ctx.fillStyle = `rgba(128,128,128,${opacity})`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";

      if (tile) {
        const stepX = fontSize * Math.max(text.length, 4) * 0.7;
        const stepY = fontSize * 3;
        for (let y = -stepY; y < img.height + stepY; y += stepY) {
          for (let x = -stepX; x < img.width + stepX; x += stepX) {
            ctx.save();
            ctx.translate(x + stepX / 2, y + stepY / 2);
            ctx.rotate((rotation * Math.PI) / 180);
            ctx.fillText(text, 0, 0);
            ctx.restore();
          }
        }
      } else {
        ctx.save();
        ctx.translate(img.width / 2, img.height / 2);
        ctx.rotate((rotation * Math.PI) / 180);
        ctx.fillText(text, 0, 0);
        ctx.restore();
      }
      resolve(canvas.toDataURL("image/png"));
    };
    img.onerror = () => reject(new Error("图片加载失败"));
    img.src = imageUrl;
  });
}

export function WatermarkToolApp({
  initialMode = "add",
  initialText = "",
}: {
  initialMode?: Mode;
  initialText?: string;
}) {
  const addDocRef = useRef<HTMLInputElement>(null);
  const removeDocRef = useRef<HTMLInputElement>(null);
  const [tab, setTab] = useState<Mode>(initialMode);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [fileKind, setFileKind] = useState<"image" | "pdf" | "unknown">("unknown");
  const [text, setText] = useState(initialText);
  const [opacity, setOpacity] = useState(0.35);
  const [rotation, setRotation] = useState(-30);
  const [tile, setTile] = useState(true);
  const [watermarkText, setWatermarkText] = useState("");
  const [loading, setLoading] = useState(false);
  const [resultUrl, setResultUrl] = useState<string | null>(null);
  const [resultFileId, setResultFileId] = useState<string | null>(null);
  const [resultFilename, setResultFilename] = useState("");
  const [method, setMethod] = useState("");

  const resetResult = () => {
    setResultUrl(null);
    setResultFileId(null);
    setResultFilename("");
    setMethod("");
  };

  const handleDocFile = (picked: File | undefined) => {
    if (!picked) return;
    if (picked.size > MAX_SIZE) {
      toast.error("文件不能超过 20MB");
      return;
    }
    const kind = getFileKind(picked);
    if (kind === "unknown") {
      toast.error("仅支持图片或 PDF");
      return;
    }
    setFile(picked);
    setFileKind(kind);
    resetResult();
    if (kind === "image") {
      setPreview(URL.createObjectURL(picked));
    } else {
      setPreview(null);
    }
  };

  const processAdd = async () => {
    if (!file) {
      toast.error("请先上传文件");
      return;
    }
    if (!text.trim()) {
      toast.error("请输入水印文字");
      return;
    }

    if (fileKind === "image") {
      if (!preview) return;
      setLoading(true);
      try {
        const dataUrl = await applyCanvasTextWatermark(preview, text, opacity, rotation, tile);
        setResultUrl(dataUrl);
        setResultFileId(null);
        toast.message("图片水印已生成，可下载");
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "加水印失败");
      } finally {
        setLoading(false);
      }
      return;
    }

    setLoading(true);
    try {
      const uploaded = await fileApi.uploadFile({ file });
      const data = await marketplaceApi.addWatermark({
        file_id: uploaded.id,
        watermark_type: "text",
        text,
        opacity,
        rotation,
        tile,
      });
      setResultFileId(data.result_file_id);
      setResultFilename(data.result_filename);
      toast.message("PDF 水印已添加");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "加水印失败");
    } finally {
      setLoading(false);
    }
  };

  const processRemove = async () => {
    if (!file) {
      toast.error("请先上传文件");
      return;
    }
    setLoading(true);
    resetResult();
    try {
      const uploaded = await fileApi.uploadFile({ file });
      const data = await marketplaceApi.removeWatermark({
        file_id: uploaded.id,
        watermark_text: watermarkText || undefined,
        mode: fileKind === "pdf" ? (watermarkText ? "text" : "auto") : "auto",
      });
      setResultFileId(data.result_file_id);
      setResultFilename(data.result_filename);
      setMethod(data.method ?? "");
      if (fileKind === "image") {
        const blob = await fileApi.downloadFile(data.result_file_id);
        setResultUrl(URL.createObjectURL(blob));
      }
      toast.message(
        data.method === "ai_inpaint"
          ? "AI 去水印完成"
          : "去水印完成（部分场景为最佳努力）",
      );
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "去水印失败");
    } finally {
      setLoading(false);
    }
  };

  const downloadResult = async () => {
    if (resultUrl) {
      const link = document.createElement("a");
      link.href = resultUrl;
      link.download = resultFilename || "watermarked.png";
      link.click();
      return;
    }
    if (resultFileId) {
      const blob = await fileApi.downloadFile(resultFileId);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = resultFilename || "result";
      link.click();
      URL.revokeObjectURL(url);
    }
  };

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-foreground text-lg font-semibold tracking-tight">水印工具</h2>
        <p className="text-muted-foreground mt-1 text-sm">
          图片文字水印本地处理；PDF 加/去水印与图片 AI 去水印走后端（best-effort）
        </p>
      </div>

      <Tabs value={tab} onValueChange={(v) => setTab(v as Mode)}>
        <TabsList className="grid w-full grid-cols-2">
          <TabsTrigger value="add">加水印</TabsTrigger>
          <TabsTrigger value="remove">去水印</TabsTrigger>
        </TabsList>

        <TabsContent value="add" className="space-y-4">
          <Card>
            <CardContent className="space-y-4 py-5">
              {fileKind === "image" ? (
                <ImageUploadZone
                  preview={preview}
                  loading={loading}
                  onFile={handleDocFile}
                  hint="上传 JPG/PNG 图片，本地添加文字水印"
                />
              ) : (
                <div className="space-y-2">
                  <Label>上传 PDF 或图片</Label>
                  <input
                    ref={addDocRef}
                    type="file"
                    accept={DOC_ACCEPT}
                    className="hidden"
                    onChange={(e) => handleDocFile(e.target.files?.[0])}
                  />
                  <Button variant="outline" onClick={() => addDocRef.current?.click()}>
                    选择文件
                  </Button>
                  {file && <p className="text-muted-foreground text-xs">已选择：{file.name}</p>}
                </div>
              )}

              <div className="space-y-2">
                <Label>水印文字</Label>
                <Input value={text} onChange={(e) => setText(e.target.value)} placeholder="例如：内部资料" />
              </div>
              <div className="space-y-2">
                <Label>透明度 {Math.round(opacity * 100)}%</Label>
                <input
                  type="range"
                  min={0.1}
                  max={0.8}
                  step={0.05}
                  value={opacity}
                  onChange={(e) => setOpacity(Number(e.target.value))}
                  className="w-full"
                />
              </div>
              <div className="space-y-2">
                <Label>旋转角度 {rotation}°</Label>
                <input
                  type="range"
                  min={-90}
                  max={90}
                  step={5}
                  value={rotation}
                  onChange={(e) => setRotation(Number(e.target.value))}
                  className="w-full"
                />
              </div>
              <div className="flex items-center justify-between">
                <Label>平铺水印</Label>
                <Switch checked={tile} onCheckedChange={setTile} />
              </div>
              <Button onClick={processAdd} disabled={loading || !file}>
                {loading ? <Loader2 className="size-4 animate-spin" /> : <Droplets className="size-4" />}
                添加水印
              </Button>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="remove" className="space-y-4">
          <Card>
            <CardContent className="space-y-4 py-5">
              <div className="space-y-2">
                <Label>上传图片或 PDF</Label>
                <input
                  ref={removeDocRef}
                  type="file"
                  accept={DOC_ACCEPT}
                  className="hidden"
                  onChange={(e) => handleDocFile(e.target.files?.[0])}
                />
                <Button variant="outline" onClick={() => removeDocRef.current?.click()}>
                  选择文件
                </Button>
                {file && <p className="text-muted-foreground text-xs">已选择：{file.name}</p>}
              </div>
              {fileKind === "pdf" && (
                <div className="space-y-2">
                  <Label>PDF 水印文字（可选，提高命中率）</Label>
                  <Input
                    value={watermarkText}
                    onChange={(e) => setWatermarkText(e.target.value)}
                    placeholder="若知道水印文字可填写"
                  />
                </div>
              )}
              <Button onClick={processRemove} disabled={loading || !file}>
                {loading ? <Loader2 className="size-4 animate-spin" /> : <Eraser className="size-4" />}
                去除水印
              </Button>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      {(resultUrl || resultFileId) && (
        <Card className="border-primary/20 bg-primary/5">
          <CardContent className="space-y-4 py-5">
            {resultUrl && (
              <img src={resultUrl} alt="处理结果" className="max-h-64 w-full rounded-xl object-contain" />
            )}
            {method && (
              <p className="text-muted-foreground text-xs">处理方式：{method}</p>
            )}
            <Button variant="outline" onClick={downloadResult}>
              <Download className="size-4" />
              下载结果
            </Button>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
