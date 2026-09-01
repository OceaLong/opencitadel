"use client";

import dynamic from "next/dynamic";

// recharts is ~9MB; keep it out of the route bundle by loading the chart
// implementations lazily on the client only.
export const UsageTimeseriesChart = dynamic(
  () => import("./usage-charts-impl").then((m) => m.UsageTimeseriesChart),
  { ssr: false },
);

export const UsageCallsChart = dynamic(
  () => import("./usage-charts-impl").then((m) => m.UsageCallsChart),
  { ssr: false },
);

export const UsageBreakdownChart = dynamic(
  () => import("./usage-charts-impl").then((m) => m.UsageBreakdownChart),
  { ssr: false },
);

export const AuditActivityChart = dynamic(
  () => import("./usage-charts-impl").then((m) => m.AuditActivityChart),
  { ssr: false },
);
