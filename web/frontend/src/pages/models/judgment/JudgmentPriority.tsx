import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ArrowDown, ArrowUp, X } from "lucide-react";
import type { JudgmentCatalog } from "@/lib/types";
import { judgmentApi } from "@/lib/api";
import { Badge, Button, Select } from "@/components/ui";
import { QueryError } from "@/components/common/AsyncState";

export function JudgmentPriority({ catalog }: { catalog?: JudgmentCatalog }) {
  const { t } = useTranslation("models");
  const cache = useQueryClient();
  const save = useMutation({
    mutationFn: judgmentApi.setPriority,
    onSuccess: async () => {
      await Promise.all([cache.invalidateQueries({ queryKey: ["judgmentCatalog"] }), cache.invalidateQueries({ queryKey: ["judgmentStatus"] })]);
    },
  });
  const models = Object.entries(catalog?.providers ?? {}).flatMap(([pid, provider]) =>
    Object.entries(provider.models).map(([id, model]) => ({ id, label: model.name || model.model, provider: provider.name || pid })),
  );
  const order = catalog?.default_model ? [catalog.default_model, ...catalog.fallback_models] : [];
  const available = models.filter((model) => !order.includes(model.id));
  const next = order.find((id) => !["offline", "checking"].includes(catalog?.health?.[id]?.status ?? "unknown"));
  const move = (index: number, offset: number) => {
    const updated = [...order];
    const current = updated[index], neighbor = updated[index + offset];
    if (current === undefined || neighbor === undefined) return;
    [updated[index], updated[index + offset]] = [neighbor, current];
    save.mutate(updated);
  };
  return <div className="rounded-xl border border-border bg-card p-4 space-y-3">
    <div><h4 className="text-sm font-medium text-heading">{t("judgment.priority")}</h4><p className="mt-1 text-xs leading-relaxed text-muted">{t("judgment.priorityHint")}</p></div>
    <ol aria-label={t("judgment.priority")} className="space-y-2">
      {order.map((id, index) => {
        const model = models.find((entry) => entry.id === id);
        const label = model?.label || id;
        return <li key={id} className="flex items-center gap-2 rounded-lg border border-border px-3 py-2">
          <span className="w-5 shrink-0 text-sm font-mono tabular-nums text-muted">{index + 1}</span>
          <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-1.5"><p className="break-words text-sm text-heading">{label}</p>{id === next && <Badge variant="info">{t("judgment.nextCandidate")}</Badge>}</div><p className="break-all text-xs text-muted">{model?.provider} · {id}</p></div>
          <div className="flex shrink-0 items-center">
            <Button size="icon" variant="ghost" disabled={save.isPending || index === 0} aria-label={t("judgment.moveUp", { name: label })} onClick={() => move(index, -1)}><ArrowUp size={14} /></Button>
            <Button size="icon" variant="ghost" disabled={save.isPending || index === order.length - 1} aria-label={t("judgment.moveDown", { name: label })} onClick={() => move(index, 1)}><ArrowDown size={14} /></Button>
            <Button size="icon" variant="ghost" disabled={save.isPending} aria-label={t("judgment.removePriority", { name: label })} onClick={() => save.mutate(order.filter((mid) => mid !== id))}><X size={14} /></Button>
          </div>
        </li>;
      })}
    </ol>
    {!order.length && <p className="text-xs text-muted">{t("judgment.noDefault")}</p>}
    <Select aria-label={t("judgment.addCandidate")} value="" disabled={!catalog || save.isPending || !available.length || order.length >= 65} onChange={(e) => { if (e.target.value) save.mutate([...order, e.target.value]); }}>
      <option value="">{t("judgment.addCandidate")}</option>
      {available.map((model) => <option key={model.id} value={model.id}>{model.provider} / {model.label} ({model.id})</option>)}
    </Select>
    {save.error && <QueryError compact error={save.error} />}
  </div>;
}
