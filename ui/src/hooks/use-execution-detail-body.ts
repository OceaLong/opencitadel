"use client";
import { useLayoutEffect, useMemo, useRef, useState } from "react";

import { ApiError, type RequestOptions } from "@/lib/api/fetch";
import type { ContentPage } from "@/lib/api/types/execution-view";
/** Supply only the workbench's currently exposed authority; never retain its previous snapshot. */
export type DetailBodyOwner = { key: string; workspaceId: string; at: string };
export type DetailBodyTarget = {
  key: string;
  filename: string;
  localUnavailableReasons?: readonly string[];
  read: (cursor: string | undefined, options: RequestOptions) => Promise<ContentPage>;
  download: (options: RequestOptions) => Promise<Blob>;
};
export type DetailBodyState = {
  pages: ContentPage[];
  loading?: boolean;
  downloading?: boolean;
  error?: boolean;
  locatorUnavailable?: boolean;
};
export function useExecutionDetailBody(owner: DetailBodyOwner | null, onRevoked: () => void) {
  const [snapshot, setSnapshot] = useState<{
    key: string;
    denied: boolean;
    items: Record<string, DetailBodyState>;
  } | null>(null);
  const generation = useRef(0);
  const controllers = useRef(new Set<AbortController>());
  const urls = useRef(new Set<string>());
  const busy = useRef(new Set<string>());
  const denied = useRef(false);
  const callback = useRef(onRevoked);
  useLayoutEffect(() => {
    callback.current = onRevoked;
  });
  const key = owner?.key ?? null;
  const lifetime = useMemo(() => ({ key, active: false }), [key]);
  useLayoutEffect(() => {
    lifetime.active = true;
    setSnapshot(null);
    generation.current += 1;
    const ownedControllers = controllers.current,
      ownedUrls = urls.current,
      ownedBusy = busy.current;
    denied.current = false;
    return () => {
      lifetime.active = false;
      generation.current += 1;
      for (const controller of ownedControllers) controller.abort();
      ownedControllers.clear();
      ownedBusy.clear();
      for (const url of ownedUrls) URL.revokeObjectURL(url);
      ownedUrls.clear();
    };
  }, [lifetime]);
  const items = snapshot?.key === key ? snapshot.items : {};
  const revoke = () => {
    if (denied.current) return;
    denied.current = true;
    generation.current += 1;
    for (const controller of controllers.current) controller.abort();
    controllers.current.clear();
    busy.current.clear();
    for (const url of urls.current) URL.revokeObjectURL(url);
    urls.current.clear();
    if (key) setSnapshot({ key, denied: true, items: {} });
    callback.current();
  };
  async function perform(target: DetailBodyTarget, download: boolean) {
    if (!owner || denied.current || !lifetime.active) return;
    const operation = `${target.key}:${download}`;
    if (busy.current.has(operation)) return;
    busy.current.add(operation);
    const captured = generation.current,
      controller = new AbortController();
    controllers.current.add(controller);
    const current = () =>
      captured === generation.current && !controller.signal.aborted && !denied.current;
    const patch = (value: Partial<DetailBodyState>) => {
      if (!current()) return;
      setSnapshot((previous) => ({
        key: owner.key,
        denied: false,
        items: {
          ...(previous?.key === owner.key ? previous.items : {}),
          [target.key]: {
            pages: [],
            ...(previous?.key === owner.key ? previous.items[target.key] : {}),
            ...value,
          },
        },
      }));
    };
    const options = {
      workspaceId: owner.workspaceId,
      signal: controller.signal,
      skipErrorHandler: true,
    };
    patch(download ? { downloading: true, error: false } : { loading: true, error: false });
    try {
      if (download) {
        const blob = await target.download(options);
        if (!current()) return;
        const url = URL.createObjectURL(blob);
        urls.current.add(url);
        if (!current()) {
          URL.revokeObjectURL(url);
          urls.current.delete(url);
          return;
        }
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = target.filename;
        anchor.click();
        URL.revokeObjectURL(url);
        urls.current.delete(url);
      } else {
        const previous = items[target.key]?.pages ?? [];
        const cursor = previous.at(-1)?.next_cursor ?? undefined;
        if (previous.length && !cursor) return;
        const page = await target.read(cursor, options);
        if (!current()) return;
        if (page.availability !== "available") {
          if (page.reason && target.localUnavailableReasons?.includes(page.reason)) {
            patch({ pages: [], locatorUnavailable: true, error: false });
            return;
          }
          if (page.reason === "source_locator_unavailable" && target.key.startsWith("source:")) {
            patch({ pages: [], locatorUnavailable: true, error: false });
            return;
          }
          revoke();
          return;
        }
        if (
          page.content == null ||
          (page.at != null && page.at !== owner.at) ||
          page.truncated !== Boolean(page.next_cursor) ||
          (page.next_cursor &&
            (page.next_cursor === cursor ||
              previous.some((p) => p.next_cursor === page.next_cursor)))
        )
          throw new ApiError(409, "revision_conflict");
        patch({ pages: [...previous, page] });
      }
    } catch (error) {
      if (!current()) return;
      if (
        target.key.startsWith("source:") &&
        error instanceof ApiError &&
        error.code === 409 &&
        error.data &&
        typeof error.data === "object" &&
        "code" in error.data &&
        error.data.code === "resource_unavailable" &&
        "reason" in error.data &&
        error.data.reason === "source_locator_unavailable"
      ) {
        patch({ pages: [], locatorUnavailable: true, error: false });
        return;
      }
      if (
        error instanceof ApiError &&
        ([401, 403, 404].includes(error.code) ||
          (error.data &&
            typeof error.data === "object" &&
            "code" in error.data &&
            error.data.code === "resource_unavailable"))
      )
        revoke();
      else patch({ error: true });
    } finally {
      controllers.current.delete(controller);
      if (current()) {
        busy.current.delete(operation);
        patch(download ? { downloading: false } : { loading: false });
      }
    }
  }
  return {
    items,
    denied: snapshot?.key === key && snapshot.denied,
    load: (target: DetailBodyTarget) => perform(target, false),
    download: (target: DetailBodyTarget) => perform(target, true),
    revoke,
  };
}
