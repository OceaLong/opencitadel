"use client";

import { useMemo, useState } from "react";
import { Copy, Wrench } from "lucide-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";

type JsonMode = "format" | "minify" | "validate";

function processJson(
  input: string,
  mode: JsonMode,
  messages: { valid: string; parseFailed: string },
): { output: string; error?: string } {
  const trimmed = input.trim();
  if (!trimmed) return { output: "" };
  try {
    const parsed = JSON.parse(trimmed);
    if (mode === "validate") {
      return { output: messages.valid };
    }
    if (mode === "minify") {
      return { output: JSON.stringify(parsed) };
    }
    return { output: JSON.stringify(parsed, null, 2) };
  } catch (error) {
    return {
      output: "",
      error: error instanceof Error ? error.message : messages.parseFailed,
    };
  }
}

function encodeBase64(
  input: string,
  encodeFailed: string,
): { output: string; error?: string } {
  if (!input) return { output: "" };
  try {
    return { output: btoa(unescape(encodeURIComponent(input))) };
  } catch (error) {
    return {
      output: "",
      error: error instanceof Error ? error.message : encodeFailed,
    };
  }
}

function decodeBase64(
  input: string,
  decodeFailed: string,
): { output: string; error?: string } {
  const trimmed = input.trim();
  if (!trimmed) return { output: "" };
  try {
    return { output: decodeURIComponent(escape(atob(trimmed))) };
  } catch (error) {
    return {
      output: "",
      error: error instanceof Error ? error.message : decodeFailed,
    };
  }
}

function encodeUrl(input: string, encodeFailed: string): { output: string; error?: string } {
  if (!input) return { output: "" };
  try {
    return { output: encodeURIComponent(input) };
  } catch (error) {
    return {
      output: "",
      error: error instanceof Error ? error.message : encodeFailed,
    };
  }
}

function decodeUrl(input: string, decodeFailed: string): { output: string; error?: string } {
  const trimmed = input.trim();
  if (!trimmed) return { output: "" };
  try {
    return { output: decodeURIComponent(trimmed) };
  } catch (error) {
    return {
      output: "",
      error: error instanceof Error ? error.message : decodeFailed,
    };
  }
}

export function DevToolboxApp({ initialText = "" }: { initialText?: string }) {
  const t = useTranslations("marketplaceApps.devToolbox");
  const tShared = useTranslations("marketplaceApps.shared");
  const [jsonInput, setJsonInput] = useState(initialText);
  const [jsonMode, setJsonMode] = useState<JsonMode>("format");
  const [base64Input, setBase64Input] = useState("");
  const [base64Decode, setBase64Decode] = useState(false);
  const [urlInput, setUrlInput] = useState("");
  const [urlDecode, setUrlDecode] = useState(false);

  const jsonResult = useMemo(
    () =>
      processJson(jsonInput, jsonMode, {
        valid: t("jsonValid"),
        parseFailed: t("jsonParseFailed"),
      }),
    [jsonInput, jsonMode, t],
  );
  const base64Result = useMemo(
    () =>
      base64Decode
        ? decodeBase64(base64Input, t("base64DecodeFailed"))
        : encodeBase64(base64Input, t("base64EncodeFailed")),
    [base64Decode, base64Input, t],
  );
  const urlResult = useMemo(
    () =>
      urlDecode ? decodeUrl(urlInput, t("urlDecodeFailed")) : encodeUrl(urlInput, t("urlEncodeFailed")),
    [urlDecode, urlInput, t],
  );

  const copy = async (value: string) => {
    if (!value) return;
    await navigator.clipboard.writeText(value);
    toast.message(tShared("copiedToClipboard"));
  };

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-foreground text-lg font-semibold tracking-tight">{t("title")}</h2>
        <p className="text-muted-foreground mt-1 text-sm">{t("subtitle")}</p>
      </div>

      <Tabs defaultValue="json">
        <TabsList className="grid w-full grid-cols-3">
          <TabsTrigger value="json">{t("tabJson")}</TabsTrigger>
          <TabsTrigger value="base64">{t("tabBase64")}</TabsTrigger>
          <TabsTrigger value="url">{t("tabUrl")}</TabsTrigger>
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
                  {t("format")}
                </Button>
                <Button
                  size="sm"
                  variant={jsonMode === "minify" ? "default" : "outline"}
                  onClick={() => setJsonMode("minify")}
                >
                  {t("minify")}
                </Button>
                <Button
                  size="sm"
                  variant={jsonMode === "validate" ? "default" : "outline"}
                  onClick={() => setJsonMode("validate")}
                >
                  {t("validate")}
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
            title={t("jsonResult")}
            value={jsonResult.output}
            error={jsonResult.error}
            emptyLabel={t("emptyResult")}
            copyLabel={tShared("copy")}
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
                  {tShared("encode")}
                </Button>
                <Button
                  size="sm"
                  variant={base64Decode ? "default" : "outline"}
                  onClick={() => setBase64Decode(true)}
                >
                  {tShared("decode")}
                </Button>
              </div>
              <Textarea
                value={base64Input}
                onChange={(e) => setBase64Input(e.target.value)}
                placeholder={base64Decode ? t("base64DecodePlaceholder") : t("base64EncodePlaceholder")}
                className="min-h-36 font-mono text-sm"
              />
            </CardContent>
          </Card>
          <ResultCard
            title={t("base64Result")}
            value={base64Result.output}
            error={base64Result.error}
            emptyLabel={t("emptyResult")}
            copyLabel={tShared("copy")}
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
                  {tShared("encode")}
                </Button>
                <Button
                  size="sm"
                  variant={urlDecode ? "default" : "outline"}
                  onClick={() => setUrlDecode(true)}
                >
                  {tShared("decode")}
                </Button>
              </div>
              <Textarea
                value={urlInput}
                onChange={(e) => setUrlInput(e.target.value)}
                placeholder={urlDecode ? t("urlDecodePlaceholder") : t("urlEncodePlaceholder")}
                className="min-h-36 font-mono text-sm"
              />
            </CardContent>
          </Card>
          <ResultCard
            title={t("urlResult")}
            value={urlResult.output}
            error={urlResult.error}
            emptyLabel={t("emptyResult")}
            copyLabel={tShared("copy")}
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
  emptyLabel,
  copyLabel,
  onCopy,
}: {
  title: string;
  value: string;
  error?: string;
  emptyLabel: string;
  copyLabel: string;
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
          {copyLabel}
        </Button>
      </CardHeader>
      <CardContent>
        {error ? (
          <p className="text-destructive text-sm">{error}</p>
        ) : (
          <pre className="border-border/70 bg-background/80 text-foreground overflow-auto rounded-xl border p-4 text-sm leading-relaxed whitespace-pre-wrap">
            {value || emptyLabel}
          </pre>
        )}
      </CardContent>
    </Card>
  );
}
