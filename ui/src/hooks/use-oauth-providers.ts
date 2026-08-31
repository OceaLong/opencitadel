"use client";

import { useEffect, useState } from "react";

/**
 * Enabled OAuth providers, resolved from the backend. SSO buttons must be
 * hidden when the provider has no configured client id/secret -- otherwise the
 * button navigates straight to a raw 400 "OAuth 提供商未启用" JSON page.
 */
export function useEnabledOAuthProviders(): Set<string> {
  const [providers, setProviders] = useState<Set<string>>(new Set());

  useEffect(() => {
    let active = true;
    fetch("/api/auth/oauth/providers", { credentials: "same-origin" })
      .then((response) => (response.ok ? response.json() : null))
      .then((body) => {
        const data = body?.data;
        if (active && Array.isArray(data)) {
          setProviders(new Set(data as string[]));
        }
      })
      .catch(() => {
        /* leave empty -- SSO buttons stay hidden on failure */
      });
    return () => {
      active = false;
    };
  }, []);

  return providers;
}
