"use client";

import { useTranslations } from "next-intl";

import {
  Field,
  FieldDescription,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import type { AgentConfig } from "@/lib/api";

type AgentSettingsProps = {
  config: AgentConfig;
  onChange: (config: AgentConfig) => void;
  readOnly?: boolean;
};

export function AgentSettings({ config, onChange, readOnly = false }: AgentSettingsProps) {
  const t = useTranslations("settings");

  const handleChange = (field: keyof AgentConfig, value: string) => {
    const numValue = value === "" ? undefined : Number(value);
    onChange({ ...config, [field]: numValue });
  };

  return (
    <form className="w-full px-1" onSubmit={(e) => e.preventDefault()}>
      <FieldGroup>
        <FieldSet>
          <FieldLegend className="text-foreground text-lg font-semibold">{t("agent")}</FieldLegend>
          <FieldGroup>
            <Field>
              <FieldLabel htmlFor="max_iterations">{t("maxIterations")}</FieldLabel>
              <Input
                id="max_iterations"
                type="number"
                placeholder={t("maxIterationsPlaceholder")}
                value={config.max_iterations ?? 100}
                onChange={(e) => handleChange("max_iterations", e.target.value)}
                min={0}
                max={200}
                readOnly={readOnly}
                disabled={readOnly}
              />
              <FieldDescription className="text-xs">{t("maxIterationsDesc")}</FieldDescription>
            </Field>
            <Field>
              <FieldLabel htmlFor="max_retries">{t("maxRetries")}</FieldLabel>
              <Input
                id="max_retries"
                type="number"
                placeholder={t("maxRetriesPlaceholder")}
                value={config.max_retries ?? 3}
                onChange={(e) => handleChange("max_retries", e.target.value)}
                min={0}
                max={10}
                readOnly={readOnly}
                disabled={readOnly}
              />
              <FieldDescription className="text-xs">{t("maxRetriesDesc")}</FieldDescription>
            </Field>
            <Field>
              <FieldLabel htmlFor="max_search_results">{t("maxSearchResults")}</FieldLabel>
              <Input
                id="max_search_results"
                type="number"
                placeholder={t("maxSearchResultsPlaceholder")}
                value={config.max_search_results ?? 10}
                onChange={(e) => handleChange("max_search_results", e.target.value)}
                min={0}
                max={30}
                readOnly={readOnly}
                disabled={readOnly}
              />
              <FieldDescription className="text-xs">{t("maxSearchResultsDesc")}</FieldDescription>
            </Field>
          </FieldGroup>
        </FieldSet>
      </FieldGroup>
    </form>
  );
}
