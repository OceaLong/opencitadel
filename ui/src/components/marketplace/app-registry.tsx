"use client";

import dynamic from "next/dynamic";
import { Layers3 } from "lucide-react";
import type { ComponentType, ReactNode } from "react";

import type { MarketplaceApp } from "@/lib/api/types";

const lazy = <P extends object>(loader: () => Promise<{ default: ComponentType<P> }>) =>
  dynamic(loader, { ssr: false });

const VideoSearchApp = lazy(() =>
  import("@/components/marketplace/video-search-app").then((m) => ({ default: m.VideoSearchApp })),
);
const NutritionAnalysisApp = lazy(() =>
  import("@/components/marketplace/nutrition-analysis-app").then((m) => ({
    default: m.NutritionAnalysisApp,
  })),
);
const ConsumptionCalculatorApp = lazy(() =>
  import("@/components/marketplace/consumption-calculator-app").then((m) => ({
    default: m.ConsumptionCalculatorApp,
  })),
);
const DocumentQaApp = lazy(() =>
  import("@/components/marketplace/document-qa-app").then((m) => ({ default: m.DocumentQaApp })),
);
const SmartTranslationApp = lazy(() =>
  import("@/components/marketplace/smart-translation-app").then((m) => ({
    default: m.SmartTranslationApp,
  })),
);
const DocumentConverterApp = lazy(() =>
  import("@/components/marketplace/document-converter-app").then((m) => ({
    default: m.DocumentConverterApp,
  })),
);
const WatermarkToolApp = lazy(() =>
  import("@/components/marketplace/watermark-tool-app").then((m) => ({
    default: m.WatermarkToolApp,
  })),
);
const PromptLabApp = lazy(() =>
  import("@/components/marketplace/prompt-lab-app").then((m) => ({ default: m.PromptLabApp })),
);
const QrGeneratorApp = lazy(() =>
  import("@/components/marketplace/qr-generator-app").then((m) => ({ default: m.QrGeneratorApp })),
);
const DevToolboxApp = lazy(() =>
  import("@/components/marketplace/dev-toolbox-app").then((m) => ({ default: m.DevToolboxApp })),
);
const SecretGeneratorApp = lazy(() =>
  import("@/components/marketplace/secret-generator-app").then((m) => ({
    default: m.SecretGeneratorApp,
  })),
);
const QuestionnaireApp = lazy(() =>
  import("@/components/marketplace/questionnaire-app").then((m) => ({ default: m.QuestionnaireApp })),
);
const RoomApp = lazy(() =>
  import("@/components/marketplace/room-app").then((m) => ({ default: m.RoomApp })),
);
const FortuneTellerApp = lazy(() =>
  import("@/components/marketplace/fortune-teller-app").then((m) => ({ default: m.FortuneTellerApp })),
);
const PersonalityTestsApp = lazy(() =>
  import("@/components/marketplace/personality-tests-app").then((m) => ({
    default: m.PersonalityTestsApp,
  })),
);
const UnitConverterApp = lazy(() =>
  import("@/components/marketplace/unit-converter-app").then((m) => ({ default: m.UnitConverterApp })),
);

export type LaunchParams = Record<string, unknown>;

export type MarketplaceAppEntry = {
  meta: MarketplaceApp;
  render: (params: LaunchParams) => ReactNode;
};

export const MARKETPLACE_REGISTRY: MarketplaceAppEntry[] = [
  {
    meta: {
      id: "video-search",
      name: "影视资源聚合",
      description: "聚合正版免费观看入口，支持中文/英文剧名搜索",
      icon: "🎬",
      category: "娱乐",
      tags: ["影视", "搜索", "正版资源", "在线播放"],
      featured: true,
      accent: "violet",
      needs_vision: false,
      examples: ["搜索三体免费观看入口", "帮我找 Breaking Bad 正版播放"],
    },
    render: (params) => (
      <VideoSearchApp
        initialQuery={typeof params.query === "string" ? params.query : undefined}
        autoRun={Boolean(params.query)}
      />
    ),
  },
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
      id: "document-qa",
      name: "文档/图片问答",
      description: "上传资料或截图，AI 提炼重点并回答问题",
      icon: "📄",
      category: "办公",
      tags: ["文档", "图片理解", "问答", "总结"],
      featured: true,
      accent: "sky",
      needs_vision: false,
      examples: ["总结这个文档的重点", "看这张截图告诉我哪里异常"],
    },
    render: (params) => (
      <DocumentQaApp initialQuestion={typeof params.question === "string" ? params.question : ""} />
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
  {
    meta: {
      id: "party-room",
      name: "派对房间",
      description: "房间码加入，摇骰子、真心话大冒险，支持多人实时",
      icon: "🎲",
      category: "娱乐",
      tags: ["骰子", "真心话大冒险", "多人", "房间"],
      featured: true,
      accent: "rose",
      needs_vision: false,
      examples: ["创建派对房间", "加入房间码 123456", "摇骰子真心话大冒险"],
    },
    render: (params) => (
      <RoomApp initialCode={typeof params.code === "string" ? params.code : undefined} />
    ),
  },
  {
    meta: {
      id: "questionnaire",
      name: "自定义问卷",
      description: "创建问卷、发布分享链接、收集回复并查看统计",
      icon: "📋",
      category: "社交",
      tags: ["问卷", "调查", "统计", "分享"],
      featured: true,
      accent: "sky",
      needs_vision: false,
      examples: ["创建一份满意度问卷", "发布活动报名表", "查看问卷统计"],
    },
    render: () => <QuestionnaireApp />,
  },
  {
    meta: {
      id: "fortune-teller",
      name: "AI 运势预测",
      description: "运势预测、抽签、算命、星盘推演，生成可分享的精美结果",
      icon: "🔮",
      category: "娱乐",
      tags: ["运势", "抽签", "算命", "星盘", "分享"],
      featured: true,
      accent: "rose",
      needs_vision: false,
      examples: ["帮我测一下近期运势", "抽一支签看看事业", "根据生日做星盘推演"],
    },
    render: (params) => (
      <FortuneTellerApp
        initialMode={
          params.mode === "fortune" ||
          params.mode === "lottery" ||
          params.mode === "divination" ||
          params.mode === "astrology"
            ? params.mode
            : undefined
        }
        initialQuestion={typeof params.question === "string" ? params.question : ""}
      />
    ),
  },
  {
    meta: {
      id: "personality-tests",
      name: "趣味人格测试",
      description: "MBTI、九型人格、DISC、爱之语言、EQ、动物人格等 6 套测试",
      icon: "🎯",
      category: "娱乐",
      tags: ["MBTI", "人格", "测试", "分享"],
      featured: true,
      accent: "violet",
      needs_vision: false,
      examples: ["测一下我的 MBTI", "九型人格测试", "我是哪种动物"],
    },
    render: (params) => (
      <PersonalityTestsApp
        initialTestId={typeof params.test_id === "string" ? params.test_id : undefined}
      />
    ),
  },
  {
    meta: {
      id: "unit-converter",
      name: "单位换算器",
      description: "长度、重量、温度、存储与面积常用单位互转",
      icon: "📏",
      category: "生活",
      tags: ["换算", "单位", "温度", "存储"],
      featured: false,
      accent: "amber",
      needs_vision: false,
      examples: ["100 公里等于多少英里", "32 华氏度转摄氏度", "1GB 等于多少 MB"],
    },
    render: (params) => (
      <UnitConverterApp initialText={typeof params.text === "string" ? params.text : ""} />
    ),
  },
];

export const FALLBACK_APPS = MARKETPLACE_REGISTRY.map((entry) => entry.meta);

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
