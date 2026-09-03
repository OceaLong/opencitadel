"use client";

import { type Dispatch, type SetStateAction, useCallback, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

/** 管理后台分页列表默认每页条数。 */
export const DEFAULT_PAGE_SIZE = 20;

export interface PageResult<T> {
  items: T[];
  total: number;
}

/**
 * 拉取某一页数据。返回 `null` 表示本次拉取失败且已被 fetcher 自行处理
 * （例如弹了 toast），此时 hook 会保留当前列表状态，仅结束 loading。
 */
export type PaginatedFetcher<T> = (params: {
  limit: number;
  offset: number;
}) => Promise<PageResult<T> | null>;

export interface UsePaginatedListResult<T> {
  items: T[];
  setItems: Dispatch<SetStateAction<T[]>>;
  total: number;
  offset: number;
  loading: boolean;
  /** 最近一次加载抛错时的错误文案；加载成功或进行中为 null。 */
  error: string | null;
  pageSize: number;
  totalPages: number;
  currentPage: number;
  canPrev: boolean;
  canNext: boolean;
  /** 加载指定 offset（默认第一页）。identity 稳定，可安全放进 effect 依赖。 */
  load: (nextOffset?: number) => Promise<void>;
  nextPage: () => Promise<void>;
  prevPage: () => Promise<void>;
}

/**
 * 后台列表分页的通用逻辑：owning items / total / offset / loading，
 * 并派生 totalPages / currentPage 与上下翻页动作。
 *
 * fetcher 通过 ref 读取最新引用，因此 `load` 的 identity 保持稳定；
 * 需要在筛选条件变化时自动重载的调用方，把 fetcher 放进 effect 依赖即可。
 *
 * fetcher 抛出的异常由 hook 兜底：toast 提示并暴露 `error`，列表状态保持不变；
 * fetcher 返回 `null` 仍视为“已自行处理的失败”，不会触发 hook 的错误提示。
 */
export function usePaginatedList<T>(
  fetcher: PaginatedFetcher<T>,
  options?: { pageSize?: number },
): UsePaginatedListResult<T> {
  const pageSize = options?.pageSize ?? DEFAULT_PAGE_SIZE;
  const [items, setItems] = useState<T[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const tCommon = useTranslations("common");

  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  // 与 fetcherRef 同理：通过 ref 读取最新翻译函数，保持 `load` identity 稳定。
  const tCommonRef = useRef(tCommon);
  tCommonRef.current = tCommon;

  const load = useCallback(
    async (nextOffset = 0) => {
      setLoading(true);
      setError(null);
      try {
        const data = await fetcherRef.current({ limit: pageSize, offset: nextOffset });
        if (data) {
          setItems(data.items);
          setTotal(data.total);
          setOffset(nextOffset);
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : tCommonRef.current("loadFailed");
        setError(message);
        toast.error(message);
      } finally {
        setLoading(false);
      }
    },
    [pageSize],
  );

  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const currentPage = Math.floor(offset / pageSize) + 1;

  const nextPage = useCallback(() => load(offset + pageSize), [load, offset, pageSize]);
  const prevPage = useCallback(
    () => load(Math.max(0, offset - pageSize)),
    [load, offset, pageSize],
  );

  return {
    items,
    setItems,
    total,
    offset,
    loading,
    error,
    pageSize,
    totalPages,
    currentPage,
    canPrev: offset > 0,
    canNext: offset + pageSize < total,
    load,
    nextPage,
    prevPage,
  };
}
