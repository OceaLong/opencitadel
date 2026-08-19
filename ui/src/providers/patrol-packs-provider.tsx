"use client";

import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { usePathname, useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { useFeatureFlags } from "@/hooks/use-feature-flags";
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
 * 挂载条件：见 `src/components/app-shell.tsx`，仅当 patrol 模块激活
 * （activeModule?.key === "patrol"）时挂载，且挂载位置覆盖 ContextPanel 与
 * 主内容区（children）两者，使 `PatrolContextPanel` 与 `/patrols` 页面共享
 * 同一份数据 —— 避免各自独立 fetch 导致状态不同步（例如创建 pack 后侧栏
 * 不刷新）。
 *
 * 除了首次挂载后的加载，还通过 `usePathname` 监听路由变化，在 enabled 时
 * 重新拉取数据，覆盖“创建 pack -> 向导 push 到新页面 -> 面板需要展示新
 * pack”以及详情页操作（激活/暂停/触发运行）后返回列表页的场景。
 */
export function PatrolPacksProvider({ children }: { children: ReactNode }) {
  const t = useTranslations("patrol");
  const router = useRouter();
  const pathname = usePathname();
  const { loading: flagLoading, opsPatrolEnabled } = useFeatureFlags();
  const enabled = opsPatrolEnabled && !flagLoading;
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

  useEffect(() => {
    if (enabled) void load();
  }, [load, enabled]);

  // 路由变化时刷新（首次挂载已由上面的 effect 处理，这里只处理后续变化）。
  const isFirstPathnameRef = useRef(true);
  useEffect(() => {
    if (isFirstPathnameRef.current) {
      isFirstPathnameRef.current = false;
      return;
    }
    if (enabled) void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only re-run on pathname change
  }, [pathname]);

  const latestRuns = useMemo(
    () => Object.fromEntries(runs.map((run) => [run.pack_id, run]).reverse()),
    [runs],
  );

  const trigger = useCallback(
    async (packId: string) => {
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
    [router, t],
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

  return (
    <PatrolPacksContext.Provider value={contextValue}>{children}</PatrolPacksContext.Provider>
  );
}

/**
 * 读取共享的 Ops Patrol pack 列表数据。
 *
 * 必须在 `<PatrolPacksProvider>` 内使用（由 AppShell 在 patrol 模块激活时
 * 挂载）。消费方（`PatrolContextPanel`、`/patrols` 页面）应先做 feature
 * flag 判断（`opsPatrolEnabled` 为 false 时直接 return null），保证不会在
 * Provider 缺失的其它模块下调用本 hook。
 */
export function usePatrolPacksContext(): PatrolPacksContextValue {
  const t = useTranslations("patrol");
  const ctx = useContext(PatrolPacksContext);
  if (!ctx) {
    throw new Error(t("errors.contextMissing"));
  }
  return ctx;
}
