"use client";

import dynamic from "next/dynamic";

// recharts is ~9MB; keep it out of the route bundle by loading the chart
// implementations lazily on the client only.
export const InterceptionsChart = dynamic(
  () => import("./governance-overview-charts-impl").then((m) => m.InterceptionsChart),
  { ssr: false },
);

export const PatrolTrendChart = dynamic(
  () => import("./governance-overview-charts-impl").then((m) => m.PatrolTrendChart),
  { ssr: false },
);

export const RemediationStatusChart = dynamic(
  () => import("./governance-overview-charts-impl").then((m) => m.RemediationStatusChart),
  { ssr: false },
);
