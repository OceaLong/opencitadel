"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { ArrowLeft, Loader2 } from "lucide-react";

import { MarkdownContent } from "@/components/markdown-content";
import { Button } from "@/components/ui/button";

import { artifactsApi } from "@/lib/api/artifacts";

export function ShareArtifactPageClient({ token }: { token: string }) {
  const t = useTranslations("shareArtifact");
  const tCommon = useTranslations("common");
  const [content, setContent] = useState("");
  const [contentType, setContentType] = useState("text/markdown");
  const [contentIncomplete, setContentIncomplete] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    void artifactsApi
      .getPublicContent(token)
      .then((data) => {
        if (cancelled) return;
        setContent(data.content);
        setContentType(data.content_type);
        setContentIncomplete(data.incomplete === true);
        setError(null);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : tCommon("loadFailed"));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token, tCommon]);

  const isHtml = contentType.includes("html");

  return (
    <div className="from-background via-background to-muted/30 flex min-h-screen flex-col bg-gradient-to-br">
      <header className="border-border/70 bg-background/70 flex shrink-0 items-center gap-4 border-b px-4 py-3 backdrop-blur">
        <Button variant="ghost" size="sm" asChild>
          <Link href="/">
            <ArrowLeft className="mr-1 size-4" />
            {t("home")}
          </Link>
        </Button>
        <span className="text-muted-foreground text-sm">{t("title")}</span>
      </header>
      <main className="mx-auto flex w-full max-w-4xl flex-1 flex-col p-6">
        {loading ? (
          <div className="text-muted-foreground flex flex-1 items-center justify-center gap-2">
            <Loader2 className="size-5 animate-spin" />
            {tCommon("loading")}
          </div>
        ) : error ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-4">
            <p className="text-muted-foreground text-sm">{error}</p>
            <Button asChild variant="outline">
              <Link href="/">{tCommon("backHome")}</Link>
            </Button>
          </div>
        ) : (
          <>
            {contentIncomplete && (
              <div className="border-warning-subtle bg-warning-subtle text-warning mb-4 rounded-xl border px-4 py-2 text-sm">
                {t("incompleteContentWarning")}
              </div>
            )}
            {isHtml ? (
              <iframe
                title={t("artifactTitle")}
                srcDoc={content}
                className="bg-background shadow-panel h-[calc(100vh-120px)] w-full rounded-xl border"
                sandbox="allow-scripts"
              />
            ) : (
              <div className="bg-card border-border/70 shadow-panel rounded-xl border p-6">
                <MarkdownContent content={content} />
              </div>
            )}
          </>
        )}
      </main>
    </div>
  );
}
