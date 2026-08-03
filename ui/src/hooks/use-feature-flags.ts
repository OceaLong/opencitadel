"use client";

import { useEffect, useState } from "react";

import { configApi } from "@/lib/api/config";

type FeatureFlags = { enable_ops_patrol?: boolean } & Record<string, unknown>;

export function useFeatureFlags() {
  const [flags, setFlags] = useState<FeatureFlags | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let mounted = true;
    configApi
      .getSection<FeatureFlags>("feature_flags")
      .then((value) => mounted && setFlags(value))
      .catch(() => mounted && setFlags(null))
      .finally(() => mounted && setLoading(false));
    return () => {
      mounted = false;
    };
  }, []);
  return { flags, loading, opsPatrolEnabled: flags?.enable_ops_patrol === true };
}
