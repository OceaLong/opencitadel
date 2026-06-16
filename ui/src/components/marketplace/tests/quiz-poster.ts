import type { QuizBank, QuizResult } from "./types";

function wrapText(
  ctx: CanvasRenderingContext2D,
  text: string,
  x: number,
  y: number,
  maxWidth: number,
  lineHeight: number,
  center = false,
): number {
  const chars = text.split("");
  let line = "";
  let currentY = y;
  for (const ch of chars) {
    const test = line + ch;
    if (ctx.measureText(test).width > maxWidth && line) {
      if (center) ctx.fillText(line, x, currentY);
      else ctx.fillText(line, x, currentY);
      line = ch;
      currentY += lineHeight;
    } else {
      line = test;
    }
  }
  if (line) {
    if (center) ctx.fillText(line, x, currentY);
    else ctx.fillText(line, x, currentY);
  }
  return currentY;
}

export async function downloadQuizPoster(bank: QuizBank, result: QuizResult): Promise<void> {
  const canvas = document.createElement("canvas");
  const width = 900;
  const height = 1400;
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("无法创建画布");

  const gradient = ctx.createLinearGradient(0, 0, width, height);
  gradient.addColorStop(0, "#1e1b4b");
  gradient.addColorStop(0.5, "#312e81");
  gradient.addColorStop(1, "#0f172a");
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, width, height);

  const padding = 64;
  ctx.textAlign = "center";
  ctx.fillStyle = "#f8fafc";
  ctx.font = "72px system-ui, sans-serif";
  ctx.fillText(result.avatar ?? bank.icon, width / 2, 160);

  ctx.font = "bold 48px system-ui, sans-serif";
  wrapText(ctx, result.title, width / 2, 240, width - padding * 2, 56, true);

  ctx.fillStyle = "#c4b5fd";
  ctx.font = "24px system-ui, sans-serif";
  ctx.fillText(`${bank.name} · ${result.code}`, width / 2, 320);

  ctx.fillStyle = "#e2e8f0";
  ctx.font = "26px system-ui, sans-serif";
  ctx.textAlign = "left";
  let y = wrapText(
    ctx,
    result.summary ?? result.description,
    padding,
    400,
    width - padding * 2,
    38,
  );

  y += 40;
  ctx.fillStyle = "#f472b6";
  ctx.font = "bold 28px system-ui, sans-serif";
  ctx.fillText("核心特质", padding, y);
  y += 40;
  ctx.fillStyle = "#cbd5e1";
  ctx.font = "24px system-ui, sans-serif";
  y = wrapText(ctx, result.traits.join(" · "), padding, y, width - padding * 2, 34);

  if (result.strengths?.length) {
    y += 36;
    ctx.fillStyle = "#f472b6";
    ctx.font = "bold 28px system-ui, sans-serif";
    ctx.fillText("核心优势", padding, y);
    y += 36;
    ctx.fillStyle = "#cbd5e1";
    ctx.font = "22px system-ui, sans-serif";
    for (const item of result.strengths.slice(0, 3)) {
      y = wrapText(ctx, `· ${item}`, padding, y, width - padding * 2, 30) + 8;
    }
  }

  if (result.confidence != null) {
    y += 24;
    ctx.fillStyle = "#94a3b8";
    ctx.font = "20px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(`匹配度 ${result.confidence}%`, width / 2, height - 72);
  }

  const link = document.createElement("a");
  link.href = canvas.toDataURL("image/png");
  link.download = `personality-${bank.id}-${result.code}.png`;
  link.click();
}
