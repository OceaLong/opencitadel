"use client";

import { useEffect, useState } from "react";

import { authApi } from "@/lib/api/auth";

/**
 * Enabled OAuth providers, resolved from the backend. SSO buttons must be
 * hidden when the provider has no configured client id/secret -- otherwise the
 * button navigates straight to a raw 400 "OAuth 提供商未启用" JSON page.
 */
export function useEnabledOAuthProviders(): Set<string> {
  const [providers, setProviders] = useState<Set<string>>(new Set());

  useEffect(() => {
    let active = true;
    authApi
      .oauthProviders()
      .then((data) => {
        if (active && Array.isArray(data)) {
          setProviders(new Set(data));
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
