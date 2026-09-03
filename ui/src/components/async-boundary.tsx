"use client";

import { useTranslations } from "next-intl";
import { Loader2 } from "lucide-react";
import type { ReactNode } from "react";

import { EmptyState } from "@/components/empty-state";
import { Button } from "@/components/ui/button";

export type AsyncBoundaryProps = {
  /** 首屏加载中（数据尚未就绪）。 */
  loading: boolean;
  /** 加载失败时的错误文案；为 null 表示无错误。 */
  error: string | null;
  /** 错误态下点击"重试"的回调；与 errorAction 二选一。 */
  onRetry?: () => void;
  /** 错误态下自定义操作区（覆盖默认的重试按钮，例如"返回首页"）。 */
  errorAction?: ReactNode;
  /** 加载态自定义占位（例如骨架屏）；缺省为居中 spinner。 */
  loadingFallback?: ReactNode;
  children: ReactNode;
};

/**
 * loading / error / content 三态的共享封装（以 share-artifact 页的三态渲染为模板）。
 *
 * 解决"加载失败 = 永久骨架屏"的伪装问题：error 非空时渲染明确的错误态
 * （错误文案 + 重试/自定义操作），而不是让调用方停在 loading 占位上。
 */
export function AsyncBoundary({
  loading,
  error,
  onRetry,
  errorAction,
  loadingFallback,
  children,
}: AsyncBoundaryProps) {
  const tCommon = useTranslations("common");

  if (loading) {
    return (
      <>
        {loadingFallback ?? (
          <div className="text-muted-foreground flex flex-1 items-center justify-center gap-2 py-12">
            <Loader2 className="size-5 animate-spin" />
            {tCommon("loading")}
          </div>
        )}
      </>
    );
  }

  if (error) {
    return (
      <EmptyState
        title={error}
        className="py-12"
        action={
          errorAction ??
          (onRetry ? (
            <Button variant="outline" onClick={onRetry}>
              {tCommon("retry")}
            </Button>
          ) : undefined)
        }
      />
    );
  }

  return <>{children}</>;
}
