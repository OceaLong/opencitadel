"use client";

import dynamic from "next/dynamic";
import { Layers3 } from "lucide-react";
import { useTranslations } from "next-intl";
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

export const APP_I18N_KEYS: Record<string, string> = {
  "nutrition-analysis": "nutritionAnalysis",
  "consumption-calculator": "consumptionCalculator",
  "smart-translation": "smartTranslation",
  "prompt-lab": "promptLab",
  "qr-generator": "qrGenerator",
  "dev-toolbox": "devToolbox",
  "secret-generator": "secretGenerator",
  "document-converter": "documentConverter",
  "watermark-tool": "watermarkTool",
};

export const CATEGORY_I18N_KEYS = new Set([
  "categoryHealth",
  "categoryLife",
  "categoryOffice",
  "categoryProductivity",
]);

export function getCategoryLabel(
  category: string,
  tMarketplace: (key: string) => string,
): string {
  if (category === "all") return tMarketplace("categoryAll");
  if (CATEGORY_I18N_KEYS.has(category)) return tMarketplace(category);
  return category;
}

type MarketplaceAppsTranslator = {
  (key: string): string;
  raw: (key: string) => unknown;
};

export function localizeMarketplaceApp(
  app: MarketplaceApp,
  tApps: MarketplaceAppsTranslator,
): MarketplaceApp {
  const appKey = APP_I18N_KEYS[app.id];
  if (!appKey) return app;
  return {
    ...app,
    name: tApps(`${appKey}.name`),
    description: tApps(`${appKey}.description`),
    tags: tApps.raw(`${appKey}.tags`) as string[],
    examples: tApps.raw(`${appKey}.examples`) as string[],
  };
}

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
      name: "nutrition-analysis",
      description: "nutrition-analysis",
      icon: "🥗",
      category: "categoryHealth",
      tags: [],
      featured: true,
      accent: "emerald",
      needs_vision: true,
      examples: [],
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
      name: "consumption-calculator",
      description: "consumption-calculator",
      icon: "📦",
      category: "categoryLife",
      tags: [],
      featured: false,
      accent: "amber",
      needs_vision: true,
      examples: [],
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
      name: "smart-translation",
      description: "smart-translation",
      icon: "🌐",
      category: "categoryOffice",
      tags: [],
      featured: true,
      accent: "indigo",
      needs_vision: false,
      examples: [],
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
      name: "prompt-lab",
      description: "prompt-lab",
      icon: "✨",
      category: "categoryProductivity",
      tags: [],
      featured: false,
      accent: "rose",
      needs_vision: false,
      examples: [],
    },
    render: (params) => (
      <PromptLabApp initialPrompt={typeof params.text === "string" ? params.text : ""} />
    ),
  },
  {
    meta: {
      id: "qr-generator",
      name: "qr-generator",
      description: "qr-generator",
      icon: "📱",
      category: "categoryProductivity",
      tags: [],
      featured: false,
      accent: "sky",
      needs_vision: false,
      examples: [],
    },
    render: (params) => (
      <QrGeneratorApp initialText={typeof params.text === "string" ? params.text : ""} />
    ),
  },
  {
    meta: {
      id: "dev-toolbox",
      name: "dev-toolbox",
      description: "dev-toolbox",
      icon: "🛠️",
      category: "categoryProductivity",
      tags: [],
      featured: false,
      accent: "indigo",
      needs_vision: false,
      examples: [],
    },
    render: (params) => (
      <DevToolboxApp initialText={typeof params.text === "string" ? params.text : ""} />
    ),
  },
  {
    meta: {
      id: "secret-generator",
      name: "secret-generator",
      description: "secret-generator",
      icon: "🔐",
      category: "categoryProductivity",
      tags: [],
      featured: false,
      accent: "rose",
      needs_vision: false,
      examples: [],
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
      name: "document-converter",
      description: "document-converter",
      icon: "📑",
      category: "categoryOffice",
      tags: [],
      featured: true,
      accent: "sky",
      needs_vision: false,
      examples: [],
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
      name: "watermark-tool",
      description: "watermark-tool",
      icon: "💧",
      category: "categoryOffice",
      tags: [],
      featured: false,
      accent: "indigo",
      needs_vision: true,
      examples: [],
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

function SelectAppPlaceholder() {
  const t = useTranslations("marketplace");
  return (
    <div className="text-muted-foreground flex flex-col items-center justify-center py-16 text-center">
      <Layers3 className="mb-3 size-10 opacity-40" />
      <p className="text-sm">{t("selectApp")}</p>
    </div>
  );
}

export function renderApp(appId: string, params: LaunchParams): ReactNode {
  const entry = REGISTRY_BY_ID.get(appId);
  if (entry) {
    return entry.render(params);
  }
  return <SelectAppPlaceholder />;
}
