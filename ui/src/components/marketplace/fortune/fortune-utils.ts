import type { FortuneMode, FortunePredictionData } from "@/lib/api/types";

export const FORTUNE_MODES: Array<{
  id: FortuneMode;
  name: string;
  icon: string;
  description: string;
}> = [
  {
    id: "fortune",
    name: "运势预测",
    icon: "🌙",
    description: "看近期整体运势与行动建议",
  },
  {
    id: "lottery",
    name: "抽签",
    icon: "🎋",
    description: "抽一支灵签，得签文指引",
  },
  {
    id: "divination",
    name: "算命",
    icon: "☯️",
    description: "卦象解读，事缓则圆",
  },
  {
    id: "astrology",
    name: "星盘推演",
    icon: "✨",
    description: "根据出生信息做星象解读",
  },
];

export const MODE_LABELS: Record<FortuneMode, string> = {
  fortune: "运势预测",
  lottery: "抽签",
  divination: "算命",
  astrology: "星盘推演",
};

export function buildFortuneSummaryText(data: FortunePredictionData): string {
  const lines = [
    `${data.result.title}`,
    data.result.summary,
    ...data.result.sections.map((section) => `${section.heading}: ${section.content}`),
    `幸运色 ${data.result.lucky_items.color} · 数字 ${data.result.lucky_items.number} · 关键词 ${data.result.lucky_items.keyword}`,
    data.result.disclaimer,
  ];
  return lines.filter(Boolean).join("\n\n");
}

export function modeAccent(mode: FortuneMode): string {
  switch (mode) {
    case "lottery":
      return "from-amber-500/20 via-orange-500/10";
    case "divination":
      return "from-emerald-500/20 via-teal-500/10";
    case "astrology":
      return "from-indigo-500/20 via-violet-500/10";
    default:
      return "from-rose-500/20 via-violet-500/10";
  }
}

export async function downloadFortunePoster(data: FortunePredictionData): Promise<void> {
  const canvas = document.createElement("canvas");
  const width = 900;
  const height = 1400;
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("无法创建画布");

  const gradient = ctx.createLinearGradient(0, 0, width, height);
  gradient.addColorStop(0, "#1a1033");
  gradient.addColorStop(0.5, "#2d1b4e");
  gradient.addColorStop(1, "#0f172a");
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, width, height);

  ctx.fillStyle = "rgba(255,255,255,0.06)";
  for (let i = 0; i < 40; i++) {
    const x = Math.random() * width;
    const y = Math.random() * height;
    const r = Math.random() * 2 + 0.5;
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fill();
  }

  const modeLabel = MODE_LABELS[data.result.mode] ?? data.result.mode;
  const padding = 64;

  ctx.fillStyle = "#f8fafc";
  ctx.font = "bold 52px system-ui, sans-serif";
  ctx.textAlign = "center";
  ctx.fillText(data.result.title, width / 2, 140);

  ctx.fillStyle = "#c4b5fd";
  ctx.font = "24px system-ui, sans-serif";
  ctx.fillText(modeLabel, width / 2, 190);

  ctx.fillStyle = "#e2e8f0";
  ctx.font = "28px system-ui, sans-serif";
  wrapText(ctx, data.result.summary, width / 2, 250, width - padding * 2, 40);

  let y = 360;
  for (const section of data.result.sections.slice(0, 3)) {
    ctx.fillStyle = "#f472b6";
    ctx.font = "bold 30px system-ui, sans-serif";
    ctx.textAlign = "left";
    ctx.fillText(section.heading, padding, y);
    y += 42;
    ctx.fillStyle = "#cbd5e1";
    ctx.font = "24px system-ui, sans-serif";
    y = wrapText(ctx, section.content, padding, y, width - padding * 2, 36) + 24;
  }

  const lucky = data.result.lucky_items;
  y = Math.max(y + 20, 900);
  ctx.fillStyle = "rgba(255,255,255,0.08)";
  roundRect(ctx, padding, y, width - padding * 2, 180, 20);
  ctx.fill();

  ctx.fillStyle = "#f8fafc";
  ctx.font = "bold 26px system-ui, sans-serif";
  ctx.textAlign = "left";
  ctx.fillText("幸运指引", padding + 24, y + 48);

  ctx.fillStyle = "#cbd5e1";
  ctx.font = "22px system-ui, sans-serif";
  const luckyText = `幸运色 ${lucky.color}  ·  数字 ${lucky.number}  ·  关键词 ${lucky.keyword}`;
  wrapText(ctx, luckyText, padding + 24, y + 90, width - padding * 2 - 48, 32);
  if (lucky.element) {
    wrapText(ctx, `元素 ${lucky.element}`, padding + 24, y + 130, width - padding * 2 - 48, 32);
  }

  ctx.fillStyle = "#94a3b8";
  ctx.font = "18px system-ui, sans-serif";
  ctx.textAlign = "center";
  wrapText(ctx, data.result.disclaimer, width / 2, height - 80, width - padding * 2, 26);

  const link = document.createElement("a");
  link.href = canvas.toDataURL("image/png");
  link.download = `fortune-${data.share_id.slice(0, 8) || "result"}.png`;
  link.click();
}

function wrapText(
  ctx: CanvasRenderingContext2D,
  text: string,
  x: number,
  y: number,
  maxWidth: number,
  lineHeight: number,
): number {
  const words = text.split("");
  let line = "";
  let currentY = y;
  const isCenter = ctx.textAlign === "center";

  for (let i = 0; i < words.length; i++) {
    const testLine = line + words[i];
    const metrics = ctx.measureText(testLine);
    if (metrics.width > maxWidth && line) {
      if (isCenter) ctx.fillText(line, x, currentY);
      else ctx.fillText(line, x, currentY);
      line = words[i];
      currentY += lineHeight;
    } else {
      line = testLine;
    }
  }
  if (line) {
    if (isCenter) ctx.fillText(line, x, currentY);
    else ctx.fillText(line, x, currentY);
  }
  return currentY;
}

function roundRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number,
) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r);
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}
