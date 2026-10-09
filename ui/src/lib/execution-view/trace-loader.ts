import { executionViewApi } from "@/lib/api/execution-view";
import { ApiError, type RequestOptions } from "@/lib/api/fetch";
import type { StepDetail, StepView, ViewPage } from "@/lib/api/types/execution-view";
export type TraceData = { steps: StepView[]; exhausted: boolean; complete: boolean };
export async function loadTracePages(
  page: ViewPage,
  detail: StepDetail | null,
  options: RequestOptions,
  publish: (state: TraceData) => void,
  api = executionViewApi,
): Promise<void> {
  const conflict = () => new ApiError(409, "revision_conflict", { code: "revision_conflict" });
  const check = () => {
    if (options.signal?.aborted) throw new DOMException("Aborted", "AbortError");
  };
  const items = new Map<string, StepView>();
  const append = (steps: StepView[]) => {
    for (const s of steps) {
      if (s.run_id !== page.run.run_id || s.projection_revision !== page.revision) throw conflict();
      items.set(s.step_id, s);
    }
    if (items.size > 10000) throw conflict();
  };
  let complete = page.run.completeness.state === "complete",
    cursor = page.next_cursor;
  const emit = () => {
    check();
    publish({ steps: [...items.values()], exhausted: !cursor, complete });
  };
  check();
  append(page.steps);
  emit();
  if (detail) {
    if (detail.at !== page.at) throw conflict();
    append([detail]);
    emit();
    let parent = detail.parent_step_id;
    const visited = new Set<string>([detail.step_id]);
    while (parent && !visited.has(parent)) {
      check();
      visited.add(parent);
      if (visited.size > 10000) throw conflict();
      const known = items.get(parent);
      if (known) {
        parent = known.parent_step_id;
        continue;
      }
      if (!page.at) throw conflict();
      try {
        const ancestor = await api.getStep(page.run.run_id, parent, { at: page.at }, options);
        check();
        if (ancestor.at !== page.at || ancestor.step_id !== parent) throw conflict();
        append([ancestor]);
        emit();
        parent = ancestor.parent_step_id;
      } catch (error) {
        check();
        if (error instanceof ApiError && error.code === 404) break;
        throw error;
      }
    }
  }
  const seen = new Set<string>();
  while (cursor) {
    check();
    if (!page.at || seen.has(cursor) || seen.size >= 10000) throw conflict();
    seen.add(cursor);
    const next = await api.listSteps(
      page.run.run_id,
      { at: page.at, revision: page.revision, cursor },
      options,
    );
    check();
    if (
      next.at !== page.at ||
      next.revision !== page.revision ||
      (next.next_cursor && seen.has(next.next_cursor))
    )
      throw conflict();
    append(next.items);
    complete = complete && next.completeness.state === "complete";
    cursor = next.next_cursor;
    emit();
  }
}
