import { ApiError } from "@/lib/api/fetch";
export function analysisFailure(error: unknown) {
  if (!(error instanceof ApiError)) return null;
  if (error.code === 410) return "expired" as const;
  if (error.code === 413) return "capacity" as const;
  if (error.code === 429) return "quota" as const;
  const code =
    error.data && typeof error.data === "object" && "code" in error.data
      ? String(error.data.code)
      : "";
  if (
    [401, 403].includes(error.code) ||
    /(?:authorization_changed|authorization_revoked|source_unavailable|resource_unavailable|coverage_changed|refresh_required|export_invalidated)$/.test(
      code,
    )
  )
    return "sourceChanged" as const;
  return null;
}
