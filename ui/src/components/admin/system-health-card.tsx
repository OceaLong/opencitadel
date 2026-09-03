"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { Activity } from "lucide-react";

import { StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

import { inferenceApi, type InferenceStatus } from "@/lib/api/inference";
import { statusApi, type SystemHealthStatus } from "@/lib/api/status";

/**
 * 管理台"系统健康"卡片：消费 GET /api/status（postgres / redis / fastapi 等
 * 组件健康）与 GET /api/inference/status（推理服务能力状态）。
 * 两个来源相互独立：任一失败不影响另一侧的展示。
 */
export function SystemHealthCard() {
  const t = useTranslations("admin");
  const tCommon = useTranslations("common");
  const [components, setComponents] = useState<SystemHealthStatus[] | null>(null);
  const [componentsError, setComponentsError] = useState<string | null>(null);
  const [inference, setInference] = useState<InferenceStatus | null>(null);
  const [inferenceError, setInferenceError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  // 重试计数：错误态点"重试"时递增以重跑加载 effect。
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      const [statusResult, inferenceResult] = await Promise.allSettled([
        statusApi.get(),
        inferenceApi.getStatus(),
      ]);
      if (cancelled) return;
      if (statusResult.status === "fulfilled") {
        setComponents(statusResult.value);
        setComponentsError(null);
      } else {
        setComponents(null);
        setComponentsError(
          statusResult.reason instanceof Error
            ? statusResult.reason.message
            : String(statusResult.reason),
        );
      }
      if (inferenceResult.status === "fulfilled") {
        setInference(inferenceResult.value);
        setInferenceError(null);
      } else {
        setInference(null);
        setInferenceError(
          inferenceResult.reason instanceof Error
            ? inferenceResult.reason.message
            : String(inferenceResult.reason),
        );
      }
      setLoading(false);
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  const inferenceCapabilities = inference ? Object.keys(inference.capabilities) : [];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Activity className="size-4" />
          {t("systemHealthTitle")}
        </CardTitle>
        <CardDescription>{t("systemHealthDesc")}</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-2">
        {loading ? (
          <>
            <Skeleton className="h-10" />
            <Skeleton className="h-10" />
            <Skeleton className="h-10" />
          </>
        ) : (
          <>
            {componentsError ? (
              <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border p-3 text-sm">
                <span className="text-destructive text-xs">{componentsError}</span>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setLoading(true);
                    setReloadKey((key) => key + 1);
                  }}
                >
                  {tCommon("retry")}
                </Button>
              </div>
            ) : (
              (components ?? []).map((component) => (
                <div
                  key={component.service}
                  className="flex flex-wrap items-center justify-between gap-2 rounded-lg border p-3 text-sm"
                >
                  <span className="font-medium" translate="no">
                    {component.service}
                  </span>
                  <span className="flex items-center gap-2">
                    {component.details ? (
                      <span className="text-muted-foreground max-w-72 truncate text-xs">
                        {component.details}
                      </span>
                    ) : null}
                    <StatusBadge variant={component.status === "ok" ? "success" : "destructive"}>
                      {component.status === "ok" ? t("healthOk") : t("healthError")}
                    </StatusBadge>
                  </span>
                </div>
              ))
            )}
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border p-3 text-sm">
              <span className="font-medium">{t("inferenceServiceLabel")}</span>
              <span className="flex items-center gap-2">
                {inference ? (
                  <span className="text-muted-foreground max-w-72 truncate text-xs" translate="no">
                    {inferenceCapabilities.join(", ")}
                  </span>
                ) : (
                  <span className="text-destructive max-w-72 truncate text-xs">
                    {inferenceError}
                  </span>
                )}
                <StatusBadge variant={inference ? "success" : "destructive"}>
                  {inference ? t("healthOk") : t("healthError")}
                </StatusBadge>
              </span>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}
