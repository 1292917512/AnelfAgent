import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ArrowDown, ArrowUp, Check, RotateCcw, Save, X } from "lucide-react";
import { apiErrorMessage, configMetaApi } from "@/lib/api";
import type { CapabilityStatus } from "@/lib/types";
import { Card } from "./Card";
import { Badge, Button, Select } from "@/components/ui";

/** 自动发现与显式提供者链共用的编辑面，只保存用户覆盖项。 */
export function CapabilityChainPanel({ status, caps, configKey, ns, queryKey }: {
  status: CapabilityStatus;
  caps: string[];
  configKey: string;
  ns: string;
  queryKey: string;
}) {
  const { t } = useTranslation("capabilities");
  const { t: domain } = useTranslation(ns);
  const client = useQueryClient();
  const [draft, setDraft] = useState<Record<string, string[]> | null>(null);
  const overrides = draft ?? status.overrides;
  const dirty = draft !== null;
  const save = useMutation({
    mutationFn: (value: Record<string, string[]>) => configMetaApi.save(configKey, value),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: [queryKey] }),
        client.invalidateQueries({ queryKey: ["configMeta"] }),
      ]);
      setDraft(null);
    },
  });
  const update = (cap: string, chain: string[]) => {
    const next = { ...overrides };
    if (chain.length) next[cap] = chain;
    else delete next[cap];
    setDraft(next);
    save.reset();
  };
  const label = (name: string) => domain(`providers.${name}`, { defaultValue: name });

  return <div className="space-y-4">
    <p className="max-w-3xl text-sm leading-relaxed text-muted">{t("hint")}</p>
    <div className="grid items-start gap-4 xl:grid-cols-2">
      {caps.map((cap) => {
        const automatic = !overrides[cap]?.length;
        const chain = overrides[cap] ?? status.defaults[cap] ?? [];
        const ready = chain.filter((name) => status.providers.find((p) => p.name === name)?.configured[cap]);
        const candidates = status.providers.filter((p) => p.capabilities.includes(cap) && !chain.includes(p.name));
        const move = (index: number, delta: number) => {
          const next = [...chain];
          const other = next[index + delta];
          const current = next[index];
          if (current === undefined || other === undefined) return;
          next[index] = other;
          next[index + delta] = current;
          update(cap, next);
        };
        return <Card key={cap} title={domain(`caps.${cap}`)}
          subtitle={ready.length ? t("readyChain", { name: label(ready[0]!), count: ready.length }) : t("unavailable")}
          actions={<Badge variant={automatic ? "accent" : "neutral"}>{t(automatic ? "automatic" : "custom")}</Badge>}>
          <ol className="space-y-2">
            {chain.map((name, index) => {
              const provider = status.providers.find((p) => p.name === name);
              const configured = !!provider?.configured[cap];
              const videoModels = provider?.details[cap]?.video_models;
              return <li key={name} className="rounded-lg border border-border bg-elevated/50 p-3">
                <div className="flex items-center gap-2">
                  <span className="grid size-6 shrink-0 place-items-center rounded-md bg-secondary text-xs tabular-nums text-muted">{index + 1}</span>
                  <div className="min-w-0 flex-1">
                    <p className="break-words text-sm font-medium text-heading">{label(name)}</p>
                    <p className="mt-0.5 text-xs text-muted">{t(!provider ? "missing" : configured ? "configured" : "notConfigured")}</p>
                  </div>
                  {configured && <Check size={14} className="shrink-0 text-ok" />}
                  <div className="flex shrink-0 gap-0.5">
                    <Button size="icon" variant="ghost" aria-label={t("moveUp", { name: label(name) })} disabled={index === 0 || save.isPending} onClick={() => move(index, -1)}><ArrowUp size={14} /></Button>
                    <Button size="icon" variant="ghost" aria-label={t("moveDown", { name: label(name) })} disabled={index === chain.length - 1 || save.isPending} onClick={() => move(index, 1)}><ArrowDown size={14} /></Button>
                    <Button size="icon" variant="ghost" aria-label={t("remove", { name: label(name) })} disabled={chain.length === 1 || save.isPending} onClick={() => update(cap, chain.filter((p) => p !== name))}><X size={14} /></Button>
                  </div>
                </div>
                {Array.isArray(videoModels) && <p className="mt-2 break-words border-t border-border pt-2 text-xs text-muted">
                  {domain("chains.videoModels")}: {videoModels.filter((name): name is string => typeof name === "string").join(" · ") || domain("chains.noVideoModels")}
                </p>}
              </li>;
            })}
          </ol>
          {!chain.length && <p className="rounded-lg border border-dashed border-border p-4 text-sm text-muted">{t("empty")}</p>}
          <div className="mt-4 flex flex-wrap items-center gap-2">
            {!!candidates.length && <Select value="" className="min-w-0 flex-1" aria-label={t("add")} disabled={save.isPending}
              onChange={(event) => update(cap, [...chain, event.target.value])}>
              <option value="" disabled>{t("add")}</option>
              {candidates.map((provider) => <option key={provider.name} value={provider.name}>{label(provider.name)}</option>)}
            </Select>}
            {!automatic && <Button size="sm" variant="ghost" disabled={save.isPending} onClick={() => update(cap, [])}><RotateCcw size={13} />{t("restoreAuto")}</Button>}
          </div>
        </Card>;
      })}
    </div>
    {dirty && <div className="sticky bottom-3 z-10 flex flex-wrap items-center gap-3 rounded-xl border border-border bg-card p-3 shadow-lg">
      <span className="flex-1 text-sm text-muted">{t("unsaved")}</span>
      <Button size="sm" disabled={save.isPending} onClick={() => { setDraft(null); save.reset(); }}>{t("discard")}</Button>
      <Button size="sm" variant="primary" loading={save.isPending} onClick={() => save.mutate(overrides)}><Save size={14} />{t("save")}</Button>
      {save.isError && <p role="alert" className="w-full text-sm text-danger">{apiErrorMessage(save.error, t("common:requestFailed"))}</p>}
    </div>}
  </div>;
}
