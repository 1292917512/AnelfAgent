import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Plus, Trash2 } from "lucide-react";
import { apiErrorMessage, configMetaApi, visionApi } from "@/lib/api";
import { CapabilityChainPanel } from "@/components/common/CapabilityChainPanel";
import { AsyncState } from "@/components/common/AsyncState";
import { Card } from "@/components/common/Card";
import { Button, Input } from "@/components/ui";

const CAPS = ["understand", "image_gen", "image_edit", "video"];

export function VisionCapabilitiesPanel() {
  const query = useQuery({
    queryKey: ["visionCapabilities"], queryFn: () => visionApi.capabilities().then((r) => r.data), throwOnError: false,
  });
  return <div className="space-y-6">
    <AsyncState pending={query.isPending} error={query.error} retry={() => void query.refetch()}>
      {query.data && <CapabilityChainPanel status={query.data} caps={CAPS} configKey="vision_provider_priority" ns="vision" queryKey="visionCapabilities" />}
    </AsyncState>
    <StylePresetsPanel />
  </div>;
}

function StylePresetsPanel() {
  const { t } = useTranslation("vision");
  const client = useQueryClient();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const query = useQuery({ queryKey: ["configMeta"], queryFn: () => configMetaApi.list().then((r) => r.data), throwOnError: false });
  const value: unknown = query.data?.groups.flatMap((g) => g.items).find((item) => item.key === "vision_style_presets")?.value;
  const presets: Record<string, string> = {};
  if (value && typeof value === "object" && !Array.isArray(value)) {
    for (const [key, description] of Object.entries(value)) if (typeof description === "string") presets[key] = description;
  }
  const save = useMutation({
    mutationFn: ({ next }: { next: Record<string, string>; added?: boolean }) => configMetaApi.save("vision_style_presets", next),
    onSuccess: async (_, variables) => {
      if (variables.added) { setName(""); setDescription(""); }
      await client.invalidateQueries({ queryKey: ["configMeta"] });
    },
  });
  const duplicate = Object.prototype.hasOwnProperty.call(presets, name.trim());
  const add = () => {
    if (!name.trim() || !description.trim() || duplicate || save.isPending) return;
    save.mutate({ next: { ...presets, [name.trim()]: description.trim() }, added: true });
  };
  return <Card title={t("presets.title")} subtitle={t("presets.subtitle")}>
    <AsyncState pending={query.isPending} error={query.error} retry={() => void query.refetch()}>
      <div className="grid items-start gap-3 lg:grid-cols-2">
        {Object.entries(presets).map(([key, description]) => <div key={key} className="flex items-start gap-3 rounded-lg border border-border p-3">
          <div className="min-w-0 flex-1"><p className="break-words text-sm font-medium text-heading">{key}</p><p className="mt-1 whitespace-pre-wrap break-words text-xs leading-relaxed text-muted">{description}</p></div>
          <Button size="icon" variant="ghost" aria-label={t("presets.remove", { name: key })} disabled={save.isPending} onClick={() => {
            const next = { ...presets }; delete next[key]; save.mutate({ next });
          }}><Trash2 size={14} /></Button>
        </div>)}
      </div>
      {!Object.keys(presets).length && <p className="text-sm text-muted">{t("presets.empty")}</p>}
      <form className="mt-4 grid gap-3 border-t border-border pt-4 sm:grid-cols-[minmax(0,1fr)_minmax(0,2fr)_auto]" onSubmit={(event) => { event.preventDefault(); add(); }}>
        <Input value={name} onChange={(e) => setName(e.target.value)} disabled={save.isPending} aria-label={t("presets.namePlaceholder")} placeholder={t("presets.namePlaceholder")} />
        <Input value={description} onChange={(e) => setDescription(e.target.value)} disabled={save.isPending} aria-label={t("presets.descPlaceholder")} placeholder={t("presets.descPlaceholder")} />
        <Button type="submit" disabled={!name.trim() || !description.trim() || duplicate} loading={save.isPending}><Plus size={14} />{t("presets.add")}</Button>
      </form>
      {duplicate && <p className="mt-2 text-xs text-danger">{t("presets.duplicate")}</p>}
      {save.isError && <p role="alert" className="mt-2 text-sm text-danger">{apiErrorMessage(save.error, t("saveFailed"))}</p>}
    </AsyncState>
  </Card>;
}
