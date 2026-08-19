"use client";

// Thin re-export: the actual data/state lives in `PatrolPacksProvider`
// (src/providers/patrol-packs-provider.tsx), shared between
// `PatrolContextPanel` and the `/patrols` page so a pack created via the
// wizard (or actioned from a detail page) shows up in both places without
// each side running its own independent fetch. Kept as a hook re-export
// (rather than updating both call sites) to minimize import churn.
export { usePatrolPacksContext as usePatrolPacks } from "@/providers/patrol-packs-provider";
