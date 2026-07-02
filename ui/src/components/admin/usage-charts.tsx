"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

import { formatCompactNumber, formatShortDate } from "@/lib/admin-utils";
import type { UsageBreakdownItem, UsageTimeseriesPoint } from "@/lib/api/admin";

const CHART_COLORS = [
  "hsl(var(--chart-1))",
  "hsl(var(--chart-2))",
  "hsl(var(--chart-3))",
  "hsl(var(--chart-4))",
  "hsl(var(--chart-5))",
];

function ChartTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: Array<{ name?: string; value?: number; color?: string }>;
  label?: string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-popover text-popover-foreground rounded-lg border px-3 py-2 text-xs shadow-md">
      <div className="mb-1 font-medium">{label}</div>
      {payload.map((entry) => (
        <div key={entry.name} className="flex items-center gap-2">
          <span className="size-2 rounded-full" style={{ background: entry.color }} />
          <span>{entry.name}</span>
          <span className="font-medium">{formatCompactNumber(entry.value ?? 0)}</span>
        </div>
      ))}
    </div>
  );
}

export function UsageTimeseriesChart({ points }: { points: UsageTimeseriesPoint[] }) {
  const data = points.map((point) => ({
    ...point,
    label: formatShortDate(point.date),
  }));

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Token 使用趋势</CardTitle>
        <CardDescription>Prompt / Completion / Total 按日统计</CardDescription>
      </CardHeader>
      <CardContent className="h-72">
        {data.length === 0 ? (
          <div className="text-muted-foreground flex h-full items-center justify-center text-sm">
            暂无用量数据
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data}>
              <CartesianGrid strokeDasharray="3 3" className="stroke-border/60" />
              <XAxis dataKey="label" tick={{ fontSize: 12 }} />
              <YAxis tickFormatter={formatCompactNumber} tick={{ fontSize: 12 }} width={48} />
              <Tooltip content={<ChartTooltip />} />
              <Legend />
              <Line type="monotone" dataKey="prompt_tokens" name="Prompt" stroke={CHART_COLORS[0]} strokeWidth={2} dot={false} />
              <Line type="monotone" dataKey="completion_tokens" name="Completion" stroke={CHART_COLORS[1]} strokeWidth={2} dot={false} />
              <Line type="monotone" dataKey="total_tokens" name="Total" stroke={CHART_COLORS[2]} strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}

export function UsageCallsChart({ points }: { points: UsageTimeseriesPoint[] }) {
  const data = points.map((point) => ({
    ...point,
    label: formatShortDate(point.date),
  }));

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">调用次数趋势</CardTitle>
        <CardDescription>LLM 调用按日统计</CardDescription>
      </CardHeader>
      <CardContent className="h-64">
        {data.length === 0 ? (
          <div className="text-muted-foreground flex h-full items-center justify-center text-sm">
            暂无调用数据
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data}>
              <CartesianGrid strokeDasharray="3 3" className="stroke-border/60" />
              <XAxis dataKey="label" tick={{ fontSize: 12 }} />
              <YAxis tick={{ fontSize: 12 }} width={40} />
              <Tooltip content={<ChartTooltip />} />
              <Bar dataKey="call_count" name="Calls" fill={CHART_COLORS[0]} radius={[6, 6, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}

export function UsageBreakdownChart({
  title,
  description,
  items,
}: {
  title: string;
  description: string;
  items: UsageBreakdownItem[];
}) {
  const data = items.slice(0, 8).map((item) => ({
    name: item.key.length > 18 ? `${item.key.slice(0, 18)}…` : item.key,
    value: item.total_tokens,
    fullName: item.key,
  }));

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent className="h-72">
        {data.length === 0 ? (
          <div className="text-muted-foreground flex h-full items-center justify-center text-sm">
            暂无分布数据
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={data}
                dataKey="value"
                nameKey="name"
                innerRadius={56}
                outerRadius={92}
                paddingAngle={2}
              >
                {data.map((entry, index) => (
                  <Cell key={entry.fullName} fill={CHART_COLORS[index % CHART_COLORS.length]} />
                ))}
              </Pie>
              <Tooltip
                formatter={(value: number) => formatCompactNumber(value)}
                contentStyle={{
                  borderRadius: "0.75rem",
                  border: "1px solid hsl(var(--border))",
                  background: "hsl(var(--popover))",
                }}
              />
              <Legend />
            </PieChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}

export function AuditActivityChart({
  byDay,
}: {
  byDay: Array<{ date: string; count: number }>;
}) {
  const data = byDay.map((item) => ({
    ...item,
    label: formatShortDate(item.date),
  }));

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">审计活动</CardTitle>
        <CardDescription>按日记录的管理操作数量</CardDescription>
      </CardHeader>
      <CardContent className="h-64">
        {data.length === 0 ? (
          <div className="text-muted-foreground flex h-full items-center justify-center text-sm">
            暂无审计数据
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data}>
              <CartesianGrid strokeDasharray="3 3" className="stroke-border/60" />
              <XAxis dataKey="label" tick={{ fontSize: 12 }} />
              <YAxis allowDecimals={false} tick={{ fontSize: 12 }} width={32} />
              <Tooltip content={<ChartTooltip />} />
              <Bar dataKey="count" name="Events" fill={CHART_COLORS[3]} radius={[6, 6, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}
