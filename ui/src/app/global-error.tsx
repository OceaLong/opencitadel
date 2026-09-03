"use client";

import { useEffect } from "react";
import { AlertCircle } from "lucide-react";

import { Button } from "@/components/ui/button";

import { getClientLocale } from "@/i18n/detect-locale";
import { translate } from "@/i18n/translate";

import "./globals.css";

/**
 * 根级错误兜底：root layout 渲染失败时由 Next 渲染本组件（替换整个 layout，
 * 因此必须自带 <html>/<body>）。此时 NextIntlClientProvider 不可用，文案改用
 * 客户端 translate() 直查 catalog。
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  const locale = getClientLocale();

  return (
    <html lang={locale === "zh" ? "zh-CN" : "en"}>
      <body className="bg-background text-foreground font-sans">
        <div className="flex min-h-screen flex-col items-center justify-center gap-4 px-4 text-center">
          <AlertCircle className="text-destructive size-10" />
          <h1 className="text-lg font-semibold">{translate("errors.appError")}</h1>
          <p className="text-muted-foreground max-w-md text-sm">
            {error.message || translate("errors.unknown")}
          </p>
          <div className="flex gap-2">
            <Button onClick={() => reset()}>{translate("common.retry")}</Button>
            <Button
              variant="outline"
              onClick={() => {
                window.location.href = "/";
              }}
            >
              {translate("common.backHome")}
            </Button>
          </div>
        </div>
      </body>
    </html>
  );
}
