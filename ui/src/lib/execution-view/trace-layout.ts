import type { StepView } from "@/lib/api/types/execution-view";
export type TraceFilters = {
  collapsed?: Set<string>;
  kind?: string;
  status?: string;
  tool?: string;
  retry?: boolean;
  approval?: boolean;
  exhausted?: boolean;
  complete?: boolean;
};
export type TraceRow = {
  key: string;
  step: StepView;
  depth: number;
  parentMissing: boolean;
  parentState: "none" | "pending" | "incomplete" | "missing";
  cycle: boolean;
  filteredDescendantCount: number;
  context: boolean;
  hasChildren: boolean;
  expanded: boolean;
  attemptCount?: number;
  logicalAttemptCount?: number;
  summary?: boolean;
};
export function spanWidth(start: number | null, end: number | null, range: number): number | null {
  if (
    start === null ||
    end === null ||
    !Number.isFinite(start) ||
    !Number.isFinite(end) ||
    !Number.isFinite(range) ||
    end < start ||
    range <= 0
  )
    return null;
  return ((end - start) / range) * 100;
}
const date = (value: string | null | undefined) =>
  value && Number.isFinite(Date.parse(value)) ? Date.parse(value) : null;
export function stepInterval(
  step: StepView,
  asOf: string | null,
): { start: number; end: number; open: boolean } | null {
  const start = date(step.started_at),
    boundary = date(asOf),
    recorded = date(step.ended_at);
  if (start === null || boundary === null || start > boundary) return null;
  const open =
    !step.ended_at && ["new", "queued", "running", "waiting", "deferred"].includes(step.status);
  const end = recorded === null ? (open ? boundary : null) : Math.min(recorded, boundary);
  if (end === null || end < start || (step.ended_at && recorded === null)) return null;
  return { start, end, open };
}
/** Iterative forest projection: never infer absence while pages or history are incomplete. */
export function buildTraceRows(
  steps: StepView[],
  expanded: Set<string>,
  filters: TraceFilters,
): TraceRow[] {
  const byId = new Map(steps.map((s) => [s.step_id, s]));
  const parent = new Map<string, string | null>();
  const cycles = new Set<string>();
  const done = new Set<string>();
  for (const s of byId.values())
    parent.set(s.step_id, s.parent_step_id && byId.has(s.parent_step_id) ? s.parent_step_id : null);
  for (const id of byId.keys()) {
    if (done.has(id)) continue;
    const path: string[] = [];
    const positions = new Map<string, number>();
    let at: string | null = id;
    while (at !== null && !done.has(at) && !positions.has(at)) {
      positions.set(at, path.length);
      path.push(at);
      at = parent.get(at) ?? null;
    }
    if (at !== null && positions.has(at)) {
      cycles.add(at);
      parent.set(at, null);
    }
    for (const item of path) done.add(item);
  }
  const children = new Map<string | null, string[]>();
  for (const id of byId.keys()) {
    const p = parent.get(id) ?? null;
    const list = children.get(p) ?? [];
    list.push(id);
    children.set(p, list);
  }
  const logical = new Map<string, number>();
  for (const s of byId.values())
    if (s.logical_step_id)
      logical.set(s.logical_step_id, (logical.get(s.logical_step_id) ?? 0) + 1);
  const active = Boolean(
    filters.kind || filters.status || filters.tool || filters.retry || filters.approval,
  );
  const match = new Map<string, boolean>();
  for (const s of byId.values())
    match.set(
      s.step_id,
      (!filters.kind || s.kind === filters.kind) &&
        (!filters.status || s.status === filters.status) &&
        (!filters.tool || s.tool_name === filters.tool) &&
        (!filters.retry ||
          Boolean(s.logical_step_id && (logical.get(s.logical_step_id) ?? 0) > 1)) &&
        (!filters.approval || s.kind === "approval"),
    );
  // Postorder computes context and counts in O(n), including a 10k-deep chain.
  const order: string[] = [];
  const stack = [...(children.get(null) ?? [])];
  while (stack.length) {
    const id = stack.pop()!;
    order.push(id);
    stack.push(...(children.get(id) ?? []));
  }
  const keep = new Map<string, boolean>(),
    hidden = new Map<string, number>();
  for (let i = order.length - 1; i >= 0; i--) {
    const id = order[i];
    let k = match.get(id) ?? false,
      h = 0;
    for (const c of children.get(id) ?? []) {
      k = k || Boolean(keep.get(c));
      h += (hidden.get(c) ?? 0) + (keep.get(c) ? 0 : 1);
    }
    keep.set(id, k);
    hidden.set(id, h);
  }
  const rows: TraceRow[] = [];
  type Work = {
    id: string;
    depth: number;
    summary?: boolean;
    members?: string[];
    branchCount?: number;
  };
  const pending: Work[] = [];
  function enqueue(ids: string[], depth: number) {
    const groups = new Map<string, string[]>();
    for (const id of ids) {
      const s = byId.get(id)!;
      if (s.logical_step_id && !cycles.has(id)) {
        const groupId = JSON.stringify([s.logical_step_id, s.parent_step_id ?? null]);
        const g = groups.get(groupId) ?? [];
        g.push(id);
        groups.set(groupId, g);
      }
    }
    const emitted = new Set<string>();
    const work: Work[] = [];
    for (const id of ids) {
      if (!keep.get(id)) continue;
      const l = byId.get(id)!.logical_step_id;
      const groupId = JSON.stringify([l, byId.get(id)!.parent_step_id ?? null]);
      const group = l ? groups.get(groupId) : undefined;
      if (l && group && !cycles.has(id) && (logical.get(l) ?? 0) > 1) {
        if (emitted.has(groupId)) continue;
        emitted.add(groupId);
        work.push({ id, depth, summary: true, members: group, branchCount: group.length });
      } else work.push({ id, depth });
    }
    pending.push(...work.reverse());
  }
  enqueue(children.get(null) ?? [], 0);
  while (pending.length) {
    const w = pending.pop()!,
      s = byId.get(w.id)!;
    const unresolved = Boolean(s.parent_step_id && !byId.has(s.parent_step_id));
    const parentState = unresolved
      ? !filters.exhausted
        ? "pending"
        : filters.complete
          ? "missing"
          : "incomplete"
      : "none";
    const key = w.summary
      ? `logical:${s.logical_step_id}${s.parent_step_id ? `:${s.parent_step_id}` : ""}`
      : w.id;
    const isExpanded = (expanded.has(key) || active) && !filters.collapsed?.has(key);
    const row: TraceRow = {
      expanded: isExpanded,
      key,
      step: s,
      depth: w.depth,
      parentMissing: parentState === "missing",
      parentState,
      cycle: !w.summary && cycles.has(w.id),
      filteredDescendantCount: w.summary
        ? w.members!.reduce((sum, id) => sum + (hidden.get(id) ?? 0) + (keep.get(id) ? 0 : 1), 0)
        : (hidden.get(w.id) ?? 0),
      context: !match.get(w.id),
      hasChildren: w.summary || Boolean(children.get(w.id)?.length),
      ...(w.summary
        ? {
            summary: true,
            attemptCount: w.branchCount,
            logicalAttemptCount: logical.get(s.logical_step_id!),
          }
        : {}),
    };
    rows.push(row);
    if (isExpanded) {
      if (w.summary)
        pending.push(
          ...w
            .members!.filter((id) => keep.get(id))
            .map((id) => ({ id, depth: w.depth + 1 }))
            .reverse(),
        );
      else enqueue(children.get(w.id) ?? [], w.depth + 1);
    }
  }
  return rows;
}
