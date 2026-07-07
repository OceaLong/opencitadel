const DEFAULT_REDIRECT = "/";

export function resolveSafeRedirectPath(redirect: string | null | undefined): string {
  const candidate = (redirect ?? "").trim();
  if (!candidate) {
    return DEFAULT_REDIRECT;
  }
  if (!candidate.startsWith("/") || candidate.startsWith("//")) {
    return DEFAULT_REDIRECT;
  }
  try {
    const parsed = new URL(candidate, "http://localhost");
    if (parsed.origin !== "http://localhost") {
      return DEFAULT_REDIRECT;
    }
    return `${parsed.pathname}${parsed.search}${parsed.hash}`;
  } catch {
    return DEFAULT_REDIRECT;
  }
}
