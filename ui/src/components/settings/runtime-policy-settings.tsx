"use client";

import { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { AlertTriangle, Loader2, RefreshCw } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import { ApiError } from "@/lib/api";
import {
  type ActiveExecutionPolicy,
  type ActiveOperationsPolicy,
  type ExecutionPolicy,
  type ExecutionPolicyRevision,
  type OperationsPolicy,
  type OperationsPolicyRevision,
  runtimePolicyApi,
} from "@/lib/api/runtime-policies";

import { ExecutionPolicyForm } from "./runtime-policy/execution-policy-form";
import { OperationsPolicyForm } from "./runtime-policy/operations-policy-form";
import { PolicyDiff } from "./runtime-policy/policy-diff";
import { PolicyHistory } from "./runtime-policy/policy-history";

function clone<T>(value: T): T {
  return structuredClone(value);
}

export function RuntimePolicySettings() {
  const t = useTranslations("runtimePolicy");
  const [execution, setExecution] = useState<ActiveExecutionPolicy>();
  const [operations, setOperations] = useState<ActiveOperationsPolicy>();
  const [executionDraft, setExecutionDraft] = useState<ExecutionPolicy>();
  const [operationsDraft, setOperationsDraft] = useState<OperationsPolicy>();
  const [executionHistory, setExecutionHistory] = useState<ExecutionPolicyRevision[]>([]);
  const [operationsHistory, setOperationsHistory] = useState<OperationsPolicyRevision[]>([]);
  const [executionNote, setExecutionNote] = useState("");
  const [operationsNote, setOperationsNote] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<"execution" | "operations">();
  const [conflicts, setConflicts] = useState<Record<"execution" | "operations", boolean>>({
    execution: false,
    operations: false,
  });

  const loadAll = useCallback(async () => {
    setLoading(true);
    try {
      const [nextExecution, nextOperations, executionPage, operationsPage] = await Promise.all([
        runtimePolicyApi.getExecution(),
        runtimePolicyApi.getOperations(),
        runtimePolicyApi.listExecutionRevisions(20, 0),
        runtimePolicyApi.listOperationsRevisions(20, 0),
      ]);
      setExecution(nextExecution);
      setOperations(nextOperations);
      setExecutionDraft(clone(nextExecution.revision.policy));
      setOperationsDraft(clone(nextOperations.revision.policy));
      setExecutionHistory(executionPage.items);
      setOperationsHistory(operationsPage.items);
      setConflicts({ execution: false, operations: false });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t("loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  const refreshHistory = async (kind: "execution" | "operations") => {
    if (kind === "execution") {
      setExecutionHistory((await runtimePolicyApi.listExecutionRevisions(20, 0)).items);
    } else {
      setOperationsHistory((await runtimePolicyApi.listOperationsRevisions(20, 0)).items);
    }
  };

  const reloadActive = async (kind: "execution" | "operations") => {
    if (kind === "execution") {
      const active = await runtimePolicyApi.getExecution();
      setExecution(active);
      setExecutionDraft(clone(active.revision.policy));
    } else {
      const active = await runtimePolicyApi.getOperations();
      setOperations(active);
      setOperationsDraft(clone(active.revision.policy));
    }
    setConflicts((current) => ({ ...current, [kind]: false }));
    await refreshHistory(kind);
  };

  const handleError = (kind: "execution" | "operations", error: unknown) => {
    if (error instanceof ApiError && error.errorKey === "runtimePolicy.headConflict") {
      setConflicts((current) => ({ ...current, [kind]: true }));
      return;
    }
    toast.error(error instanceof Error ? error.message : t("saveFailed"));
  };

  const saveExecution = async () => {
    if (!execution || !executionDraft || !executionNote.trim()) return;
    setSaving("execution");
    try {
      const active = await runtimePolicyApi.createExecution({
        expected_head_version: execution.head.version,
        expected_active_revision_id: execution.revision.id,
        note: executionNote.trim(),
        policy: executionDraft,
      });
      setExecution(active);
      setExecutionDraft(clone(active.revision.policy));
      setExecutionNote("");
      setConflicts((current) => ({ ...current, execution: false }));
      await refreshHistory("execution");
      toast.success(t("saveSuccess"));
    } catch (error) {
      handleError("execution", error);
    } finally {
      setSaving(undefined);
    }
  };

  const saveOperations = async () => {
    if (!operations || !operationsDraft || !operationsNote.trim()) return;
    setSaving("operations");
    try {
      const active = await runtimePolicyApi.createOperations({
        expected_head_version: operations.head.version,
        expected_active_revision_id: operations.revision.id,
        note: operationsNote.trim(),
        policy: operationsDraft,
      });
      setOperations(active);
      setOperationsDraft(clone(active.revision.policy));
      setOperationsNote("");
      setConflicts((current) => ({ ...current, operations: false }));
      await refreshHistory("operations");
      toast.success(t("saveSuccess"));
    } catch (error) {
      handleError("operations", error);
    } finally {
      setSaving(undefined);
    }
  };

  if (loading || !execution || !operations || !executionDraft || !operationsDraft) {
    return (
      <div className="flex min-h-48 items-center justify-center">
        <Loader2 className="text-muted-foreground size-6 animate-spin" />
      </div>
    );
  }

  const conflictBanner = (kind: "execution" | "operations") =>
    conflicts[kind] ? (
      <div className="border-warning-subtle bg-warning-subtle text-warning flex items-center justify-between gap-3 rounded-lg border p-3 text-sm">
        <span className="flex items-center gap-2">
          <AlertTriangle className="size-4" />
          {t("headConflict")}
        </span>
        <Button type="button" size="xs" variant="outline" onClick={() => reloadActive(kind)}>
          <RefreshCw className="size-3.5" />
          {t("reloadActive")}
        </Button>
      </div>
    ) : null;

  return (
    <Tabs defaultValue="execution" className="w-full">
      <TabsList className="grid w-full grid-cols-2">
        <TabsTrigger value="execution">{t("execution.title")}</TabsTrigger>
        <TabsTrigger value="operations">{t("operations.title")}</TabsTrigger>
      </TabsList>
      <TabsContent value="execution">
        <Card>
          <CardHeader>
            <CardTitle>{t("execution.title")}</CardTitle>
            <CardDescription>
              {t("activeMetadata", {
                sequence: execution.revision.sequence,
                version: execution.head.version,
                actor: execution.head.updated_by,
              })}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-5">
            {conflictBanner("execution")}
            <ExecutionPolicyForm
              policy={executionDraft}
              disabled={saving === "execution"}
              onChange={setExecutionDraft}
            />
            <Field>
              <FieldLabel>{t("note")}</FieldLabel>
              <FieldDescription>{t("noteDescription")}</FieldDescription>
              <Input
                aria-label={t("note")}
                value={executionNote}
                maxLength={1000}
                onChange={(event) => setExecutionNote(event.target.value)}
              />
            </Field>
            <PolicyDiff before={execution.revision.policy} after={executionDraft} />
            <Button
              type="button"
              disabled={saving === "execution" || !executionNote.trim()}
              onClick={saveExecution}
            >
              {saving === "execution" ? <Loader2 className="animate-spin" /> : null}
              {t("actions.saveExecution")}
            </Button>
            <PolicyHistory
              kind="execution"
              head={execution.head}
              revisions={executionHistory}
              onRestored={async (active) => {
                const next = active as ActiveExecutionPolicy;
                setExecution(next);
                setExecutionDraft(clone(next.revision.policy));
                await refreshHistory("execution");
              }}
            />
          </CardContent>
        </Card>
      </TabsContent>
      <TabsContent value="operations">
        <Card>
          <CardHeader>
            <CardTitle>{t("operations.title")}</CardTitle>
            <CardDescription>
              {t("activeMetadata", {
                sequence: operations.revision.sequence,
                version: operations.head.version,
                actor: operations.head.updated_by,
              })}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-5">
            {conflictBanner("operations")}
            <OperationsPolicyForm
              policy={operationsDraft}
              disabled={saving === "operations"}
              onChange={setOperationsDraft}
            />
            <Field>
              <FieldLabel>{t("note")}</FieldLabel>
              <FieldDescription>{t("noteDescription")}</FieldDescription>
              <Input
                aria-label={t("note")}
                value={operationsNote}
                maxLength={1000}
                onChange={(event) => setOperationsNote(event.target.value)}
              />
            </Field>
            <PolicyDiff before={operations.revision.policy} after={operationsDraft} />
            <Button
              type="button"
              disabled={saving === "operations" || !operationsNote.trim()}
              onClick={saveOperations}
            >
              {saving === "operations" ? <Loader2 className="animate-spin" /> : null}
              {t("actions.saveOperations")}
            </Button>
            <PolicyHistory
              kind="operations"
              head={operations.head}
              revisions={operationsHistory}
              onRestored={async (active) => {
                const next = active as ActiveOperationsPolicy;
                setOperations(next);
                setOperationsDraft(clone(next.revision.policy));
                await refreshHistory("operations");
              }}
            />
          </CardContent>
        </Card>
      </TabsContent>
    </Tabs>
  );
}
