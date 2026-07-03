"use client";

import { useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";

import { InlineOptionPicker } from "@/components/inline-option-picker";

import { skillsApi } from "@/lib/api/skills";
import type { Skill, SkillSummary } from "@/lib/api/types";
import { useAuth } from "@/providers/auth-provider";

let skillsCache: Skill[] | null = null;
let skillsPromise: Promise<Skill[]> | null = null;

function loadSkills(): Promise<Skill[]> {
  if (skillsCache) return Promise.resolve(skillsCache);
  if (!skillsPromise) {
    skillsPromise = skillsApi.list(true).then((data) => {
      skillsCache = data.skills;
      skillsPromise = null;
      return data.skills;
    });
  }
  return skillsPromise;
}

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
  const [skills, setSkills] = useState<Skill[]>([]);

  useEffect(() => {
    if (!user) {
      setSkills([]);
      return;
    }
    let cancelled = false;
    loadSkills()
      .then((items) => {
        if (!cancelled) setSkills(items);
      })
      .catch(() => {
        if (!cancelled) setSkills([]);
      });
    return () => {
      cancelled = true;
    };
  }, [user]);

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
      skills.map((s) => ({
        id: s.id,
        title: s.name,
        description: s.description || s.category,
        icon: <span className="text-base leading-none">{s.icon}</span>,
        badge: s.is_builtin ? tCommon("builtin") : undefined,
      })),
    [skills],
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
