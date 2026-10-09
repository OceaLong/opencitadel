/** Compare only public fixed-source facts, never paging or transient UI state. */
export function sourceIdentity(value: {
  metrics: unknown;
  coverage_changed?: boolean;
  member_count?: number;
}) {
  return JSON.stringify([
    value.metrics,
    value.coverage_changed ?? false,
    value.member_count ?? null,
  ]);
}
