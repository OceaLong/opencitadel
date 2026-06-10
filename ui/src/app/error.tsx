"use client";

import { useEffect } from "react";
import { AlertCircle } from "lucide-react";

import { Button } from "@/components/ui/button";

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

  return (
    <div className="bg-background flex min-h-screen flex-col items-center justify-center gap-4 px-4">
      <AlertCircle className="text-destructive size-10" />
      <h2 className="text-lg font-semibold">页面加载出错</h2>
      <p className="text-muted-foreground max-w-md text-center text-sm">
        {error.message || "发生了未知错误，请重试或返回首页。"}
      </p>
      <div className="flex gap-2">
        <Button onClick={() => reset()}>重试</Button>
        <Button variant="outline" onClick={() => (window.location.href = "/")}>
          返回首页
        </Button>
      </div>
    </div>
  );
}
