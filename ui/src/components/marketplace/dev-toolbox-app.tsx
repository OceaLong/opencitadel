"use client";

import { useMemo, useState } from "react";
import { Copy, Wrench } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";

type JsonMode = "format" | "minify" | "validate";

function processJson(input: string, mode: JsonMode): { output: string; error?: string } {
  const trimmed = input.trim();
  if (!trimmed) return { output: "" };
  try {
    const parsed = JSON.parse(trimmed);
    if (mode === "validate") {
      return { output: "JSON 格式有效" };
    }
    if (mode === "minify") {
      return { output: JSON.stringify(parsed) };
    }
    return { output: JSON.stringify(parsed, null, 2) };
  } catch (error) {
    return {
      output: "",
      error: error instanceof Error ? error.message : "JSON 解析失败",
    };
  }
}

function encodeBase64(input: string): { output: string; error?: string } {
  if (!input) return { output: "" };
  try {
    return { output: btoa(unescape(encodeURIComponent(input))) };
  } catch (error) {
    return {
      output: "",
      error: error instanceof Error ? error.message : "Base64 编码失败",
    };
  }
}

function decodeBase64(input: string): { output: string; error?: string } {
  const trimmed = input.trim();
  if (!trimmed) return { output: "" };
  try {
    return { output: decodeURIComponent(escape(atob(trimmed))) };
  } catch (error) {
    return {
      output: "",
      error: error instanceof Error ? error.message : "Base64 解码失败",
    };
  }
}

function encodeUrl(input: string): { output: string; error?: string } {
  if (!input) return { output: "" };
  try {
    return { output: encodeURIComponent(input) };
  } catch (error) {
    return {
      output: "",
      error: error instanceof Error ? error.message : "URL 编码失败",
    };
  }
}

function decodeUrl(input: string): { output: string; error?: string } {
  const trimmed = input.trim();
  if (!trimmed) return { output: "" };
  try {
    return { output: decodeURIComponent(trimmed) };
  } catch (error) {
    return {
      output: "",
      error: error instanceof Error ? error.message : "URL 解码失败",
    };
  }
}

export function DevToolboxApp({ initialText = "" }: { initialText?: string }) {
  const [jsonInput, setJsonInput] = useState(initialText);
  const [jsonMode, setJsonMode] = useState<JsonMode>("format");
  const [base64Input, setBase64Input] = useState("");
  const [base64Decode, setBase64Decode] = useState(false);
  const [urlInput, setUrlInput] = useState("");
  const [urlDecode, setUrlDecode] = useState(false);

  const jsonResult = useMemo(() => processJson(jsonInput, jsonMode), [jsonInput, jsonMode]);
  const base64Result = useMemo(
    () => (base64Decode ? decodeBase64(base64Input) : encodeBase64(base64Input)),
    [base64Decode, base64Input],
  );
  const urlResult = useMemo(
    () => (urlDecode ? decodeUrl(urlInput) : encodeUrl(urlInput)),
    [urlDecode, urlInput],
  );

  const copy = async (value: string) => {
    if (!value) return;
    await navigator.clipboard.writeText(value);
    toast.message("已复制到剪贴板");
  };

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-foreground text-lg font-semibold tracking-tight">开发者工具箱</h2>
        <p className="text-muted-foreground mt-1 text-sm">
          JSON 格式化、Base64 与 URL 编解码，本地处理不上传
        </p>
      </div>

      <Tabs defaultValue="json">
        <TabsList className="grid w-full grid-cols-3">
          <TabsTrigger value="json">JSON</TabsTrigger>
          <TabsTrigger value="base64">Base64</TabsTrigger>
          <TabsTrigger value="url">URL</TabsTrigger>
        </TabsList>

        <TabsContent value="json" className="space-y-4">
          <Card>
            <CardContent className="space-y-4 py-5">
              <div className="flex flex-wrap gap-2">
                <Button
                  size="sm"
                  variant={jsonMode === "format" ? "default" : "outline"}
                  onClick={() => setJsonMode("format")}
                >
                  格式化
                </Button>
                <Button
                  size="sm"
                  variant={jsonMode === "minify" ? "default" : "outline"}
                  onClick={() => setJsonMode("minify")}
                >
                  压缩
                </Button>
                <Button
                  size="sm"
                  variant={jsonMode === "validate" ? "default" : "outline"}
                  onClick={() => setJsonMode("validate")}
                >
                  校验
                </Button>
              </div>
              <Textarea
                value={jsonInput}
                onChange={(e) => setJsonInput(e.target.value)}
                placeholder='{"name": "example", "items": [1, 2, 3]}'
                className="min-h-36 font-mono text-sm"
              />
            </CardContent>
          </Card>
          <ResultCard
            title="JSON 结果"
            value={jsonResult.output}
            error={jsonResult.error}
            onCopy={() => copy(jsonResult.output)}
          />
        </TabsContent>

        <TabsContent value="base64" className="space-y-4">
          <Card>
            <CardContent className="space-y-4 py-5">
              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant={!base64Decode ? "default" : "outline"}
                  onClick={() => setBase64Decode(false)}
                >
                  编码
                </Button>
                <Button
                  size="sm"
                  variant={base64Decode ? "default" : "outline"}
                  onClick={() => setBase64Decode(true)}
                >
                  解码
                </Button>
              </div>
              <Textarea
                value={base64Input}
                onChange={(e) => setBase64Input(e.target.value)}
                placeholder={base64Decode ? "粘贴 Base64 字符串" : "输入要编码的文本"}
                className="min-h-36 font-mono text-sm"
              />
            </CardContent>
          </Card>
          <ResultCard
            title="Base64 结果"
            value={base64Result.output}
            error={base64Result.error}
            onCopy={() => copy(base64Result.output)}
          />
        </TabsContent>

        <TabsContent value="url" className="space-y-4">
          <Card>
            <CardContent className="space-y-4 py-5">
              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant={!urlDecode ? "default" : "outline"}
                  onClick={() => setUrlDecode(false)}
                >
                  编码
                </Button>
                <Button
                  size="sm"
                  variant={urlDecode ? "default" : "outline"}
                  onClick={() => setUrlDecode(true)}
                >
                  解码
                </Button>
              </div>
              <Textarea
                value={urlInput}
                onChange={(e) => setUrlInput(e.target.value)}
                placeholder={urlDecode ? "粘贴 URL 编码字符串" : "输入要编码的文本或参数"}
                className="min-h-36 font-mono text-sm"
              />
            </CardContent>
          </Card>
          <ResultCard
            title="URL 结果"
            value={urlResult.output}
            error={urlResult.error}
            onCopy={() => copy(urlResult.output)}
          />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function ResultCard({
  title,
  value,
  error,
  onCopy,
}: {
  title: string;
  value: string;
  error?: string;
  onCopy: () => void;
}) {
  return (
    <Card className="border-primary/20 bg-primary/5">
      <CardHeader className="flex flex-row items-center justify-between gap-3 pb-2">
        <CardTitle className="flex items-center gap-2 text-base">
          <Wrench className="size-4" />
          {title}
        </CardTitle>
        <Button variant="outline" size="sm" onClick={onCopy} disabled={!value}>
          <Copy className="size-4" />
          复制
        </Button>
      </CardHeader>
      <CardContent>
        {error ? (
          <p className="text-destructive text-sm">{error}</p>
        ) : (
          <pre className="border-border/70 bg-background/80 text-foreground overflow-auto rounded-xl border p-4 text-sm leading-relaxed whitespace-pre-wrap">
            {value || "处理结果将显示在这里"}
          </pre>
        )}
      </CardContent>
    </Card>
  );
}
