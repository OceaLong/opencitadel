type HealthInput = { capabilities: unknown; circuit_breakers?: unknown[] };

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

export function inferenceHealth(status: HealthInput | null) {
  const items = record(record(status?.capabilities).items);
  const breakers = (status?.circuit_breakers ?? []).map(record);
  return {
    healthy:
      record(items.chat).state === "available" &&
      breakers.every((breaker) => breaker.state === "closed"),
    capabilities: Object.entries(items).map(
      ([name, value]) => `${name}: ${String(record(value).state ?? "unknown")}`,
    ),
    breakers: breakers.map(
      (breaker) => `${String(breaker.model_id ?? "model")}: ${String(breaker.state ?? "unknown")}`,
    ),
  };
}
