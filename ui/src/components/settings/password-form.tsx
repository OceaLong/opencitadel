"use client";

import { type FormEvent, useId, useState } from "react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function PasswordForm({
  adminReset = false,
  onSubmit,
}: {
  adminReset?: boolean;
  onSubmit: (newPassword: string, currentPassword: string) => Promise<void>;
}) {
  const t = useTranslations("password");
  const id = useId();
  const [current, setCurrent] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (pending) return;
    setError("");
    setSuccess(false);
    if (password !== confirmation) {
      setError(t("mismatch"));
      return;
    }
    if (password.length < 8 || password.length > 128 || !password.trim()) {
      setError(t("policy"));
      return;
    }
    setPending(true);
    try {
      await onSubmit(password, current);
      setCurrent("");
      setPassword("");
      setConfirmation("");
      setSuccess(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("failed"));
    } finally {
      setPending(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      <p className="text-muted-foreground text-sm">
        {adminReset ? t("resetNotice") : t("changeNotice")}
      </p>
      {!adminReset && (
        <div className="space-y-2">
          <Label htmlFor={`${id}-current`}>{t("current")}</Label>
          <Input
            id={`${id}-current`}
            name="current-password"
            type="password"
            autoComplete="current-password"
            value={current}
            onChange={(event) => setCurrent(event.target.value)}
            required
            maxLength={128}
            disabled={pending}
          />
        </div>
      )}
      <div className="space-y-2">
        <Label htmlFor={`${id}-new`}>{t("new")}</Label>
        <Input
          id={`${id}-new`}
          name="new-password"
          type="password"
          autoComplete="new-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          required
          minLength={8}
          maxLength={128}
          disabled={pending}
        />
        <p className="text-muted-foreground text-xs">{t("policy")}</p>
      </div>
      <div className="space-y-2">
        <Label htmlFor={`${id}-confirm`}>{t("confirm")}</Label>
        <Input
          id={`${id}-confirm`}
          name="confirm-password"
          type="password"
          autoComplete="new-password"
          value={confirmation}
          onChange={(event) => setConfirmation(event.target.value)}
          required
          maxLength={128}
          disabled={pending}
        />
      </div>
      {error && (
        <p role="alert" className="text-destructive text-sm">
          {error}
        </p>
      )}
      {success && (
        <p role="status" className="text-sm">
          {adminReset ? t("resetSuccess") : t("changeSuccess")}
        </p>
      )}
      <Button type="submit" disabled={pending}>
        {pending ? t("saving") : adminReset ? t("reset") : t("change")}
      </Button>
    </form>
  );
}
