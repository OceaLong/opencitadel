"use client";

import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { usePathname, useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { useCapabilities } from "@/hooks/use-capabilities";
import { isCapabilityAvailable } from "@/lib/api/capabilities";
import { patrolsApi } from "@/lib/api/patrols";
import type { PatrolPack, PatrolRun } from "@/lib/api/types";

type PatrolPacksContextValue = {
  packs: PatrolPack[];
  latestRuns: Record<string, PatrolRun>;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  trigger: (packId: string) => Promise<void>;
  triggeringId: string | null;
  toggle: (pack: PatrolPack) => Promise<void>;
  actionId: string | null;
};

const PatrolPacksContext = createContext<PatrolPacksContextValue | null>(null);

/**
 * Ops Patrol pack 列表数据 Provider。
 *
 * 挂载位置：见 `src/components/app-shell.tsx`，**无条件**挂载在 AppShell 顶层，
 * 挂载位置与激活模块无关，因此进出 /patrols 不会因父元素类型切换而卸载重挂
 * shellBody（AppHeader / 当前页面）。挂载位置覆盖 ContextPanel 与主内容区
 * （children）两者，使 `PatrolContextPanel` 与 `/patrols` 页面共享同一份数据
 * —— 避免各自独立 fetch 导致状态不同步（例如创建 pack 后侧栏不刷新）。
 *
 * `enabled`（默认 true）控制是否**发起**请求：仅当 patrol 模块激活时为 true，
 * 惰性加载 pack 列表；非活跃模块下为 false，不发无谓请求。启用后除首次加载
 * 外，还通过 `usePathname` 监听路由变化重新拉取，覆盖“创建 pack -> 向导 push
 * 到新页面 -> 面板需要展示新 pack”以及详情页操作（激活/暂停/触发运行）后返回
 * 列表页的场景。
 */
export function PatrolPacksProvider({
  children,
  enabled = true,
}: {
  children: ReactNode;
  enabled?: boolean;
}) {
  const t = useTranslations("patrol");
  const router = useRouter();
  const pathname = usePathname();
  const { loading: capabilityLoading, capability } = useCapabilities();
  const runAdmissionAvailable = isCapabilityAvailable(capability("ops_patrol"));
  const [packs, setPacks] = useState<PatrolPack[]>([]);
  const [runs, setRuns] = useState<PatrolRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [triggeringId, setTriggeringId] = useState<string | null>(null);
  const [actionId, setActionId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [packData, runData] = await Promise.all([
        patrolsApi.listPacks(),
        patrolsApi.listRuns({ limit: 100 }),
      ]);
      setPacks(packData.items);
      setRuns(runData.items);
    } catch (err) {
      const message = err instanceof Error ? err.message : t("errors.load");
      setError(message);
      toast.error(message);
    } finally {
      setLoading(false);
    }
  }, [t]);

  // 仅在 enabled（patrol 模块激活）时加载，惰性拉取；
  // 同时依赖 pathname，在 patrol 内部导航（创建/详情操作后返回列表）时刷新。
  useEffect(() => {
    if (!enabled) return;
    void load();
  }, [enabled, pathname, load]);

  const latestRuns = useMemo(
    () => Object.fromEntries(runs.map((run) => [run.pack_id, run]).reverse()),
    [runs],
  );

  const trigger = useCallback(
    async (packId: string) => {
      if (capabilityLoading || !runAdmissionAvailable) {
        toast.error(t("disabled.description"));
        return;
      }
      setTriggeringId(packId);
      try {
        const run = await patrolsApi.triggerPack(
          packId,
          globalThis.crypto?.randomUUID?.() ?? `${packId}-${Date.now()}`,
        );
        toast.success(t("toast.started"));
        router.push(`/patrol-runs/${run.id}`);
      } catch (error) {
        toast.error(error instanceof Error ? error.message : t("errors.trigger"));
      } finally {
        setTriggeringId(null);
      }
    },
    [capabilityLoading, router, runAdmissionAvailable, t],
  );

  const toggle = useCallback(
    async (pack: PatrolPack) => {
      setActionId(pack.id);
      try {
        if (pack.status === "active") await patrolsApi.pausePack(pack.id);
        else await patrolsApi.activatePack(pack.id);
        toast.success(pack.status === "active" ? t("toast.paused") : t("toast.activated"));
        await load();
      } catch (error) {
        toast.error(error instanceof Error ? error.message : t("errors.action"));
      } finally {
        setActionId(null);
      }
    },
    [load, t],
  );

  const contextValue = useMemo(
    () => ({
      packs,
      latestRuns,
      loading,
      error,
      refresh: load,
      trigger,
      triggeringId,
      toggle,
      actionId,
    }),
    [packs, latestRuns, loading, error, load, trigger, triggeringId, toggle, actionId],
  );

  return <PatrolPacksContext.Provider value={contextValue}>{children}</PatrolPacksContext.Provider>;
}

/**
 * 读取共享的 Ops Patrol pack 列表数据。
 *
 * 必须在 `<PatrolPacksProvider>` 内使用（由 AppShell 无条件挂载，patrol
 * 模块激活时通过 enabled 拉取数据）。该 Provider 挂载稳定、不随模块切换
 * 卸载，不依赖运行准入策略；策略只影响 trigger 操作。
 */
export function usePatrolPacksContext(): PatrolPacksContextValue {
  const t = useTranslations("patrol");
  const ctx = useContext(PatrolPacksContext);
  if (!ctx) {
    throw new Error(t("errors.contextMissing"));
  }
  return ctx;
}
