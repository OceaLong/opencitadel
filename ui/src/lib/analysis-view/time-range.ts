export function recentRange(days: number, now = new Date()) {
  return { start: new Date(now.getTime() - days * 86400000).toISOString(), end: now.toISOString() };
}
export function utcInput(value: string | undefined) {
  const date = new Date(value ?? "");
  return Number.isFinite(date.getTime()) ? date.toISOString().slice(0, 16) : "";
}
export function fromUtcInput(value: string) {
  const date = new Date(`${value}Z`);
  return value && Number.isFinite(date.getTime()) ? date.toISOString() : undefined;
}
