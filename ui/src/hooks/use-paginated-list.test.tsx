// @vitest-environment jsdom

import { act, useEffect } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderComponent } from "@/test-utils/render";

import { type PaginatedFetcher, usePaginatedList, type UsePaginatedListResult } from "./use-paginated-list";

type Item = { id: string };

/**
 * Renders the hook and mirrors its latest result into `sinkRef`, plus fires
 * `load(0)` once on mount (matching how the admin pages bootstrap).
 */
function Harness({
  fetcher,
  pageSize,
  sinkRef,
}: {
  fetcher: PaginatedFetcher<Item>;
  pageSize?: number;
  sinkRef: { current: UsePaginatedListResult<Item> | null };
}) {
  const result = usePaginatedList<Item>(fetcher, pageSize ? { pageSize } : undefined);
  const { load } = result;
  // Mirror the latest hook result out of the component after each commit.
  useEffect(() => {
    sinkRef.current = result;
  });
  useEffect(() => {
    void load(0);
  }, [load]);
  return null;
}

describe("usePaginatedList", () => {
  afterEach(() => {
    vi.clearAllMocks();
    document.body.replaceChildren();
  });

  it("loads the first page and derives paging metadata", async () => {
    const fetcher = vi.fn(async ({ limit, offset }: { limit: number; offset: number }) => ({
      items: [{ id: `${offset}` }],
      total: 45,
      _seen: { limit, offset },
    }));
    const sinkRef: { current: UsePaginatedListResult<Item> | null } = { current: null };

    const { unmount } = await renderComponent(<Harness fetcher={fetcher} sinkRef={sinkRef} />);

    expect(fetcher).toHaveBeenCalledWith({ limit: 20, offset: 0 });
    expect(sinkRef.current?.items).toEqual([{ id: "0" }]);
    expect(sinkRef.current?.total).toBe(45);
    expect(sinkRef.current?.totalPages).toBe(3);
    expect(sinkRef.current?.currentPage).toBe(1);
    expect(sinkRef.current?.canPrev).toBe(false);
    expect(sinkRef.current?.canNext).toBe(true);
    expect(sinkRef.current?.loading).toBe(false);

    await unmount();
  });

  it("advances and rewinds pages by pageSize", async () => {
    const fetcher = vi.fn(async ({ offset }: { limit: number; offset: number }) => ({
      items: [{ id: `${offset}` }],
      total: 45,
    }));
    const sinkRef: { current: UsePaginatedListResult<Item> | null } = { current: null };

    const { unmount } = await renderComponent(<Harness fetcher={fetcher} sinkRef={sinkRef} />);

    await act(async () => {
      await sinkRef.current?.nextPage();
    });
    expect(fetcher).toHaveBeenLastCalledWith({ limit: 20, offset: 20 });
    expect(sinkRef.current?.offset).toBe(20);
    expect(sinkRef.current?.currentPage).toBe(2);
    expect(sinkRef.current?.canPrev).toBe(true);

    await act(async () => {
      await sinkRef.current?.prevPage();
    });
    expect(fetcher).toHaveBeenLastCalledWith({ limit: 20, offset: 0 });
    expect(sinkRef.current?.offset).toBe(0);

    await unmount();
  });

  it("keeps prior state when the fetcher returns null (handled error)", async () => {
    let call = 0;
    const fetcher = vi.fn(async () => {
      call += 1;
      return call === 1 ? { items: [{ id: "a" }], total: 10 } : null;
    });
    const sinkRef: { current: UsePaginatedListResult<Item> | null } = { current: null };

    const { unmount } = await renderComponent(<Harness fetcher={fetcher} sinkRef={sinkRef} />);
    expect(sinkRef.current?.items).toEqual([{ id: "a" }]);

    await act(async () => {
      await sinkRef.current?.load(20);
    });
    // Failed fetch: list + offset unchanged, loading settled back to false.
    expect(sinkRef.current?.items).toEqual([{ id: "a" }]);
    expect(sinkRef.current?.offset).toBe(0);
    expect(sinkRef.current?.loading).toBe(false);

    await unmount();
  });

  it("honours a custom pageSize", async () => {
    const fetcher = vi.fn(async () => ({ items: [], total: 25 }));
    const sinkRef: { current: UsePaginatedListResult<Item> | null } = { current: null };

    const { unmount } = await renderComponent(
      <Harness fetcher={fetcher} pageSize={10} sinkRef={sinkRef} />,
    );

    expect(fetcher).toHaveBeenCalledWith({ limit: 10, offset: 0 });
    expect(sinkRef.current?.totalPages).toBe(3);

    await unmount();
  });
});
