"use client";

import { useEffect } from "react";
import { AlertCircle } from "lucide-react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const t = useTranslations("errors");
  const tCommon = useTranslations("common");

  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="bg-background flex min-h-screen flex-col items-center justify-center gap-4 px-4">
      <AlertCircle className="text-destructive size-10" />
      <h2 className="text-lg font-semibold">{t("pageLoadError")}</h2>
      <p className="text-muted-foreground max-w-md text-center text-sm">
        {error.message || t("unknownError")}
      </p>
      <div className="flex gap-2">
        <Button onClick={() => reset()}>{tCommon("retry")}</Button>
        <Button variant="outline" onClick={() => (window.location.href = "/")}>
          {tCommon("backHome")}
        </Button>
      </div>
    </div>
  );
}
