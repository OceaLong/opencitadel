"use client";

import { Moon, Sun } from "lucide-react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { useTheme } from "@/providers/theme-provider";

export function ThemeToggle() {
  const { theme, toggleTheme } = useTheme();
  const t = useTranslations("theme");

  return (
    <Button
      type="button"
      variant="outline"
      size="icon-sm"
      onClick={toggleTheme}
      aria-label={theme === "dark" ? t("switchToLight") : t("switchToDark")}
      title={theme === "dark" ? t("light") : t("dark")}
    >
      {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
    </Button>
  );
}
