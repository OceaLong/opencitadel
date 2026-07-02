"use client";

import dynamic from "next/dynamic";
import { Layers3 } from "lucide-react";
import type { ComponentType, ReactNode } from "react";

import type { MarketplaceApp, ModelDependency } from "@/lib/api/types";

const MODEL_DEPENDENCY: Record<string, ModelDependency> = {
  "nutrition-analysis": "required",
  "consumption-calculator": "required",
  "smart-translation": "required",
  "prompt-lab": "required",
  "qr-generator": "none",
  "dev-toolbox": "none",
  "secret-generator": "none",
  "document-converter": "none",
  "watermark-tool": "optional",
};

function withModelDependency(meta: MarketplaceApp): MarketplaceApp {
  return {
    ...meta,
    model_dependency: meta.model_dependency ?? MODEL_DEPENDENCY[meta.id] ?? "optional",
  };
}

const lazy = <P extends object>(loader: () => Promise<ComponentType<P>>) =>
  dynamic<P>(loader, { ssr: false });

const NutritionAnalysisApp = lazy(() =>
  import("@/components/marketplace/nutrition-analysis-app").then((m) => m.NutritionAnalysisApp),
);
const ConsumptionCalculatorApp = lazy(() =>
  import("@/components/marketplace/consumption-calculator-app").then((m) => m.ConsumptionCalculatorApp),
);
const SmartTranslationApp = lazy(() =>
  import("@/components/marketplace/smart-translation-app").then((m) => m.SmartTranslationApp),
);
const DocumentConverterApp = lazy(() =>
  import("@/components/marketplace/document-converter-app").then((m) => m.DocumentConverterApp),
);
const WatermarkToolApp = lazy(() =>
  import("@/components/marketplace/watermark-tool-app").then((m) => m.WatermarkToolApp),
);
const PromptLabApp = lazy(() =>
  import("@/components/marketplace/prompt-lab-app").then((m) => m.PromptLabApp),
);
const QrGeneratorApp = lazy(() =>
  import("@/components/marketplace/qr-generator-app").then((m) => m.QrGeneratorApp),
);
const DevToolboxApp = lazy(() =>
  import("@/components/marketplace/dev-toolbox-app").then((m) => m.DevToolboxApp),
);
const SecretGeneratorApp = lazy(() =>
  import("@/components/marketplace/secret-generator-app").then((m) => m.SecretGeneratorApp),
);

export type LaunchParams = Record<string, unknown>;

export type MarketplaceAppEntry = {
  meta: MarketplaceApp;
  render: (params: LaunchParams) => ReactNode;
};

export const MARKETPLACE_REGISTRY: MarketplaceAppEntry[] = [
  {
    meta: {
      id: "nutrition-analysis",
      name: "AI营养分析",
      description: "拍照识别餐食营养，减脂/增肌红绿灯评估",
      icon: "🥗",
      category: "健康",
      tags: ["视觉识别", "营养", "健身", "减脂"],
      featured: true,
      accent: "emerald",
      needs_vision: true,
      examples: ["分析这顿饭适不适合减脂", "看看这张餐食蛋白够不够"],
    },
    render: (params) => (
      <NutritionAnalysisApp
        initialGoal={
          params.goal === "cut" || params.goal === "bulk" || params.goal === "maintain"
            ? params.goal
            : undefined
        }
      />
    ),
  },
  {
    meta: {
      id: "consumption-calculator",
      name: "消耗计算器",
      description: "识别包装净含量，计算可食用次数",
      icon: "📦",
      category: "生活",
      tags: ["OCR", "计算", "包装识别", "生活效率"],
      featured: false,
      accent: "amber",
      needs_vision: true,
      examples: ["这包 1.2kg 每次 50g 能吃几次", "识别包装净含量并计算消耗"],
    },
    render: (params) => (
      <ConsumptionCalculatorApp
        initialTotalGrams={typeof params.total_grams === "number" ? params.total_grams : undefined}
        initialServingGrams={
          typeof params.serving_grams === "number" ? params.serving_grams : undefined
        }
      />
    ),
  },
  {
    meta: {
      id: "smart-translation",
      name: "智能翻译",
      description: "自动识别语种，按正式/口语/技术风格翻译",
      icon: "🌐",
      category: "办公",
      tags: ["翻译", "润色", "多语言", "技术文档"],
      featured: true,
      accent: "indigo",
      needs_vision: false,
      examples: ["把这段英文翻译成正式中文", "翻译截图里的文字为英文"],
    },
    render: (params) => (
      <SmartTranslationApp
        initialText={typeof params.text === "string" ? params.text : ""}
        initialTargetLanguage={
          typeof params.target_language === "string" ? params.target_language : undefined
        }
        initialStyle={
          params.style === "formal" || params.style === "casual" || params.style === "technical"
            ? params.style
            : undefined
        }
      />
    ),
  },
  {
    meta: {
      id: "prompt-lab",
      name: "提示词工坊",
      description: "把粗略想法改写为可复用的高质量提示词",
      icon: "✨",
      category: "效率",
      tags: ["提示词", "工作流", "创作", "效率"],
      featured: false,
      accent: "rose",
      needs_vision: false,
      examples: ["帮我写一个数据分析 Agent 提示词", "优化这段客服回复 prompt"],
    },
    render: (params) => (
      <PromptLabApp initialPrompt={typeof params.text === "string" ? params.text : ""} />
    ),
  },
  {
    meta: {
      id: "qr-generator",
      name: "二维码生成器",
      description: "将文本或链接快速生成可下载的二维码图片",
      icon: "📱",
      category: "效率",
      tags: ["二维码", "链接", "分享", "下载"],
      featured: false,
      accent: "sky",
      needs_vision: false,
      examples: ["生成 https://example.com 的二维码", "把这段文字做成二维码"],
    },
    render: (params) => (
      <QrGeneratorApp initialText={typeof params.text === "string" ? params.text : ""} />
    ),
  },
  {
    meta: {
      id: "dev-toolbox",
      name: "开发者工具箱",
      description: "JSON 格式化、Base64 与 URL 编解码一站式处理",
      icon: "🛠️",
      category: "效率",
      tags: ["JSON", "Base64", "URL", "格式化"],
      featured: false,
      accent: "indigo",
      needs_vision: false,
      examples: ["格式化这段 JSON", "把文本 Base64 编码", "URL 解码这段参数"],
    },
    render: (params) => (
      <DevToolboxApp initialText={typeof params.text === "string" ? params.text : ""} />
    ),
  },
  {
    meta: {
      id: "secret-generator",
      name: "密码 & UUID 生成器",
      description: "生成可配置强度的密码与批量 UUID",
      icon: "🔐",
      category: "效率",
      tags: ["密码", "UUID", "安全", "随机"],
      featured: false,
      accent: "rose",
      needs_vision: false,
      examples: ["生成 16 位强密码", "批量生成 5 个 UUID"],
    },
    render: (params) => (
      <SecretGeneratorApp
        initialLength={typeof params.length === "number" ? params.length : undefined}
      />
    ),
  },
  {
    meta: {
      id: "document-converter",
      name: "文档格式转换",
      description: "md/txt 转 PDF、PDF 转 Word、常用文档格式互转",
      icon: "📑",
      category: "办公",
      tags: ["PDF", "Word", "Markdown", "格式转换"],
      featured: true,
      accent: "sky",
      needs_vision: false,
      examples: ["把这份 PDF 转成 Word", "md 转 pdf", "pdf 转 markdown"],
    },
    render: (params) => (
      <DocumentConverterApp
        initialTargetFormat={
          params.target_format === "pdf" ||
          params.target_format === "docx" ||
          params.target_format === "md" ||
          params.target_format === "txt"
            ? params.target_format
            : undefined
        }
      />
    ),
  },
  {
    meta: {
      id: "watermark-tool",
      name: "水印工具",
      description: "图片/PDF 加水印，图片 AI 去水印与 PDF 去水印",
      icon: "💧",
      category: "办公",
      tags: ["水印", "去水印", "PDF", "图片处理"],
      featured: false,
      accent: "indigo",
      needs_vision: true,
      examples: ["给 PDF 加上机密水印", "去掉图片右下角水印", "批量平铺文字水印"],
    },
    render: (params) => (
      <WatermarkToolApp
        initialMode={params.mode === "remove" ? "remove" : "add"}
        initialText={typeof params.text === "string" ? params.text : ""}
      />
    ),
  },
];

export const FALLBACK_APPS = MARKETPLACE_REGISTRY.map((entry) => withModelDependency(entry.meta));

const REGISTRY_BY_ID = new Map(MARKETPLACE_REGISTRY.map((entry) => [entry.meta.id, entry]));

export function renderApp(appId: string, params: LaunchParams): ReactNode {
  const entry = REGISTRY_BY_ID.get(appId);
  if (entry) {
    return entry.render(params);
  }
  return (
    <div className="text-muted-foreground flex flex-col items-center justify-center py-16 text-center">
      <Layers3 className="mb-3 size-10 opacity-40" />
      <p className="text-sm">请选择一个应用开始使用</p>
    </div>
  );
}
