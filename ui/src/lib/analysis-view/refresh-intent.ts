export function refreshIntent(search: Pick<URLSearchParams, "get">) {
  const id = search.get("refresh_comparison");
  const revision = Number(search.get("expected_revision"));
  return id &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id) &&
    Number.isSafeInteger(revision) &&
    revision >= 1
    ? { id, revision }
    : null;
}
