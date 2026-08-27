"use client";

import { useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";

import { InlineOptionPicker } from "@/components/session/inline-option-picker";

import { skillsApi } from "@/lib/api/skills";
import type { Skill, SkillSummary } from "@/lib/api/types";
import { clientDataScopeKey } from "@/lib/data/client-data-scope";
import { useAuth } from "@/providers/auth-provider";
import { useClientDataScope } from "@/providers/client-data-provider";

type Props = {
  value?: string | null;
  onChange: (skillId: string | undefined, skill?: SkillSummary | null) => void;
  disabled?: boolean;
  onSkillLoaded?: (skill: Skill | null) => void;
  className?: string;
};

export function SessionSkillPicker({ value, onChange, disabled, onSkillLoaded, className }: Props) {
  const t = useTranslations("skillPicker");
  const tCommon = useTranslations("common");
  const { user } = useAuth();
  const { scope, scopeRevision, loadResource, resourceRevision } = useClientDataScope();
  const skillsRevision = resourceRevision("skills");
  const scopeKey = scope ? clientDataScopeKey(scope) : null;
  const [loaded, setLoaded] = useState<{ scopeKey: string; skills: Skill[] } | null>(null);
  const skills = useMemo(
    () => (loaded?.scopeKey === scopeKey ? loaded.skills : []),
    [loaded, scopeKey],
  );

  useEffect(() => {
    if (!user || !scopeKey) return;
    let cancelled = false;
    const requestedScopeKey = scopeKey;
    void loadResource("skills", async () => {
      const data = await skillsApi.list(true);
      return data.skills;
    })
      .then((items) => {
        if (!cancelled) setLoaded({ scopeKey: requestedScopeKey, skills: items });
      })
      .catch(() => {
        if (!cancelled) setLoaded(null);
      });
    return () => {
      cancelled = true;
    };
  }, [loadResource, scopeKey, scopeRevision, skillsRevision, user]);

  useEffect(() => {
    if (!value) {
      onSkillLoaded?.(null);
      return;
    }
    const s = skills.find((sk) => sk.id === value);
    if (s) {
      onSkillLoaded?.(s);
    }
  }, [value, skills, onSkillLoaded]);

  const options = useMemo(
    () =>
      (user ? skills : []).map((s) => ({
        id: s.id,
        title: s.name,
        description: s.description || s.category,
        icon: <span className="text-base leading-none">{s.icon}</span>,
        badge: s.is_builtin ? tCommon("builtin") : undefined,
      })),
    [skills, tCommon, user],
  );

  const handleChange = (skillId: string | undefined) => {
    if (!skillId) {
      onChange(undefined, null);
      onSkillLoaded?.(null);
      return;
    }
    const s = skills.find((sk) => sk.id === skillId);
    onChange(skillId, s ? { id: s.id, name: s.name, icon: s.icon, examples: s.examples } : null);
    onSkillLoaded?.(s || null);
  };

  return (
    <InlineOptionPicker
      value={value || undefined}
      options={options}
      placeholder={t("none")}
      onChange={handleChange}
      disabled={disabled}
      allowClear
      clearValue="__none__"
      className={className}
    />
  );
}
