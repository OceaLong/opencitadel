"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, Loader2 } from "lucide-react";

import { MarkdownContent } from "@/components/markdown-content";
import { Button } from "@/components/ui/button";

import { artifactsApi } from "@/lib/api/artifacts";

function ShareArtifactContent() {
  const params = useParams<{ token: string }>();
  const token = params.token;
  const [content, setContent] = useState("");
  const [contentType, setContentType] = useState("text/markdown");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    setLoading(true);
    void artifactsApi
      .getPublicContent(token)
      .then((data) => {
        if (cancelled) return;
        setContent(data.content);
        setContentType(data.content_type);
        setError(null);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "加载失败");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const isHtml = contentType.includes("html");

  return (
    <div className="from-background via-background to-muted/30 flex min-h-screen flex-col bg-gradient-to-br">
      <header className="border-border/70 bg-background/70 flex shrink-0 items-center gap-4 border-b px-4 py-3 backdrop-blur">
        <Button variant="ghost" size="sm" asChild>
          <Link href="/">
            <ArrowLeft className="mr-1 size-4" />
            首页
          </Link>
        </Button>
        <span className="text-muted-foreground text-sm">交付物分享</span>
      </header>
      <main className="mx-auto flex w-full max-w-4xl flex-1 flex-col p-6">
        {loading ? (
          <div className="text-muted-foreground flex flex-1 items-center justify-center gap-2">
            <Loader2 className="size-5 animate-spin" />
            加载中…
          </div>
        ) : error ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-4">
            <p className="text-muted-foreground text-sm">{error}</p>
            <Button asChild variant="outline">
              <Link href="/">返回首页</Link>
            </Button>
          </div>
        ) : isHtml ? (
          <iframe
            title="交付物"
            srcDoc={content}
            className="bg-background h-[calc(100vh-120px)] w-full rounded-xl border shadow-[var(--shadow-panel)]"
            sandbox="allow-scripts"
          />
        ) : (
          <div className="bg-card border-border/70 rounded-xl border p-6 shadow-[var(--shadow-panel)]">
            <MarkdownContent content={content} />
          </div>
        )}
      </main>
    </div>
  );
}

export default function ShareArtifactPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center">
          <Loader2 className="size-5 animate-spin" />
        </div>
      }
    >
      <ShareArtifactContent />
    </Suspense>
  );
}
