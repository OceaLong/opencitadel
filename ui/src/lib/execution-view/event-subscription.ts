import { executionViewApi } from "@/lib/api/execution-view";
import { ApiError } from "@/lib/api/fetch";

/** Feed cursors only resume this subscription. They never become playback locations. */
export function subscribeExecutionInvalidations(
  run: string,
  workspaceId: string,
  invalidate: () => void,
  forbidden: () => void,
): () => void {
  const lifetime = new AbortController();
  let request: AbortController | null = null;
  let cursor: string | undefined;
  let anchor = true;
  let failures = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const seen = new Set<string>();
  const deny = () => {
    if (lifetime.signal.aborted) return;
    lifetime.abort();
    request?.abort();
    if (timer) clearTimeout(timer);
    forbidden();
  };
  const connect = async () => {
    if (lifetime.signal.aborted) return;
    request = new AbortController();
    const own = request;
    const current = () => !lifetime.signal.aborted && request === own && !own.signal.aborted;
    let retired = false;
    try {
      if (anchor) {
        const page = await executionViewApi.getEvents(
          run,
          { latest: true, limit: 1 },
          { workspaceId, signal: own.signal },
        );
        if (!current()) return;
        cursor = page.events.at(-1)?.cursor;
        anchor = false;
        invalidate(); // closes the view-read / feed-anchor race, including an empty feed
      }
      await executionViewApi.streamEvents(
        run,
        (event) => {
          if (!current() || retired) return;
          cursor = event.cursor;
          failures = 0;
          if (seen.has(event.event_id)) return;
          seen.add(event.event_id);
          if (seen.size > 10000) seen.delete(seen.values().next().value!);
          invalidate();
        },
        {
          workspaceId,
          signal: own.signal,
          lastEventId: cursor,
          onRefresh: (reason) => {
            if (!current()) return;
            if (reason.code === "permission_denied") {
              deny();
              return;
            }
            retired = true;
            anchor = true;
            cursor = undefined;
            invalidate();
            own.abort();
          },
        },
      );
    } catch (error) {
      if (lifetime.signal.aborted) return;
      if (error instanceof ApiError && (error.code === 403 || error.code === 401)) {
        deny();
        return;
      }
      if (error instanceof ApiError && error.code === 409) {
        anchor = true;
        cursor = undefined;
        invalidate();
      }
    }
    if (!lifetime.signal.aborted) {
      const delay = Math.min(1000 * 2 ** Math.min(failures++, 5), 30000);
      timer = setTimeout(() => {
        void connect();
      }, delay);
    }
  };
  void connect();
  return () => {
    lifetime.abort();
    request?.abort();
    if (timer) clearTimeout(timer);
  };
}
