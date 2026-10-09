"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";

import { evaluationApi } from "@/lib/api/evaluations";
import type { components } from "@/lib/api/generated/schema";

import { type EvaluationAccess, EvaluationError, useEvaluationTask } from "./evaluation-boundary";
import { EnvironmentPicker } from "./execution-bindings";
import { Check, Choice, Field } from "./form-fields";
type S = components["schemas"];
export function EnvironmentManager({ access }: { access: EvaluationAccess }) {
  const t = useTranslations("evaluations");
  const task = useEvaluationTask(access);
  const { run: runTask } = task;
  const [inventory, setInventory] = useState<S["EnvironmentInventory"] | null>(null);
  const [adapter, setAdapter] = useState("");
  const [image, setImage] = useState("");
  const [fixture, setFixture] = useState("");
  const [health, setHealth] = useState("");
  const [targets, setTargets] = useState<string[]>([]);
  const [credentials, setCredentials] = useState<string[]>([]);
  const [identity, setIdentity] = useState(() => crypto.randomUUID());
  const [saved, setSaved] = useState<string | null>(null);
  const [limits, setLimits] = useState({
    concurrency: 2,
    memory_mb: 1024,
    cpu_millis: 1000,
    pids: 256,
    timeout_seconds: 1800,
  });
  useEffect(() => {
    if (access.canRegister)
      void runTask((o) => evaluationApi.environmentInventory(o), setInventory);
  }, [access.canRegister, runTask]);
  if (task.error === "denied") return <EvaluationError error="denied" />;
  const selected = inventory?.adapters.find((a) => a.name === adapter);
  const selectedImage = selected?.images[Number(image)];
  return (
    <>
      <Link href="/evaluations" className="text-sm underline">
        {t("back")}
      </Link>
      <h1 className="text-xl font-semibold">{t("environments")}</h1>
      <EvaluationError error={task.error} />
      <EnvironmentPicker access={access} value={saved ?? ""} onChange={setSaved} />
      {!access.canRegister ? (
        <p>{t("adminRequired")}</p>
      ) : (
        inventory && (
          <form
            className="space-y-3 rounded-md border p-3"
            onSubmit={(e) => {
              e.preventDefault();
              if (!selected || !selectedImage) return;
              const value: S["EnvironmentVersion"] = {
                id: identity,
                revision: 1,
                image_digest: selectedImage,
                fixture_revision: fixture,
                reset_adapter: selected.name,
                adapter_revision: selected.revision,
                healthcheck_revision: health,
                allowed_targets: inventory.targets
                  .filter((v) => targets.includes(v.id))
                  .map(({ id, revision }) => ({ id, revision })),
                credential_refs: inventory.credentials
                  .filter((v) => credentials.includes(v.id))
                  .map(({ id, revision }) => ({ id, revision })),
                limits,
              };
              void runTask(
                async (o) => {
                  for (const target of inventory.targets.filter((v) => targets.includes(v.id)))
                    await evaluationApi.registerInventory(
                      "target",
                      target.id,
                      { request_id: task.requestId("target", target), revision: target.revision },
                      o,
                    );
                  for (const credential of inventory.credentials.filter((v) =>
                    credentials.includes(v.id),
                  ))
                    await evaluationApi.registerInventory(
                      "credential",
                      credential.id,
                      {
                        request_id: task.requestId("credential", credential),
                        revision: credential.revision,
                      },
                      o,
                    );
                  return evaluationApi.registerEnvironment(
                    {
                      kind: "environment",
                      value,
                      request_id: task.requestId("environment", value),
                    },
                    o,
                  );
                },
                (v) => {
                  setSaved(v.id);
                  setIdentity(crypto.randomUUID());
                },
              );
            }}
          >
            <h2>{t("registerEnvironment")}</h2>
            <p className="text-muted-foreground text-sm">{t("trustedInventory")}</p>
            <fieldset disabled={task.pending} className="space-y-3">
              <Choice
                label={t("adapter")}
                value={adapter}
                onChange={(value) => {
                  setAdapter(value);
                  setImage("");
                  setFixture("");
                  setHealth("");
                }}
                required
              >
                <option value="">{t("select")}</option>
                {inventory.adapters.map((a) => (
                  <option key={a.name} value={a.name}>
                    {a.name} · {a.revision}
                  </option>
                ))}
              </Choice>
              <Choice label={t("image")} value={image} onChange={setImage} required>
                <option value="">{t("select")}</option>
                {selected?.images.map((v, i) => (
                  <option key={i} value={i}>
                    {v.repository ?? t("localImage")} · {v.value}
                  </option>
                ))}
              </Choice>
              <Choice label={t("fixture")} value={fixture} onChange={setFixture} required>
                <option value="">{t("select")}</option>
                {selected?.fixtures.map((v) => (
                  <option key={v}>{v}</option>
                ))}
              </Choice>
              <Choice label={t("healthcheck")} value={health} onChange={setHealth} required>
                <option value="">{t("select")}</option>
                {selected?.healthchecks.map((v) => (
                  <option key={v}>{v}</option>
                ))}
              </Choice>
              <fieldset>
                <legend>{t("testTargets")}</legend>
                {inventory.targets.map((v) => (
                  <Check
                    key={v.id}
                    label={`${v.kind} · ${v.id} · ${t("revision")} ${v.revision}`}
                    checked={targets.includes(v.id)}
                    onChange={(checked) => {
                      setTargets((old) =>
                        checked ? [...old, v.id] : old.filter((id) => id !== v.id),
                      );
                      setCredentials([]);
                    }}
                  />
                ))}
              </fieldset>
              <fieldset>
                <legend>{t("testCredentials")}</legend>
                {inventory.credentials
                  .filter((v) => targets.includes(v.target.id))
                  .map((v) => (
                    <Check
                      key={v.id}
                      label={`${v.id} · ${t("revision")} ${v.revision}`}
                      checked={credentials.includes(v.id)}
                      onChange={(checked) =>
                        setCredentials((old) =>
                          checked ? [...old, v.id] : old.filter((id) => id !== v.id),
                        )
                      }
                    />
                  ))}
              </fieldset>
              <div className="grid gap-2 sm:grid-cols-2">
                {(Object.keys(limits) as (keyof typeof limits)[]).map((key) => (
                  <Field
                    key={key}
                    label={t(key)}
                    type="number"
                    min={1}
                    value={limits[key]}
                    onChange={(raw) => setLimits((old) => ({ ...old, [key]: Number(raw) }))}
                    required
                  />
                ))}
              </div>
              <Button disabled={!selected || !selectedImage || !fixture || !health}>
                {t("registerEnvironment")}
              </Button>
            </fieldset>
            {inventory.adapters.length === 0 && <p>{t("inventoryEmpty")}</p>}
          </form>
        )
      )}
      {saved && (
        <p role="status" className="break-all">
          {t("registered")}: {saved}
        </p>
      )}
    </>
  );
}
