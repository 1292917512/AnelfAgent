/** 视觉生成能力面板：能力优先级链 + 风格预设管理（默认参数在配置 tab 热编辑）。 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Plus, Trash2 } from "lucide-react";
import { configMetaApi, visionApi } from "@/lib/api";
import { CapabilityChainPanel } from "@/components/common/CapabilityChainPanel";
import { Card } from "@/components/common/Card";

const CAPS = ["understand", "image_gen", "image_edit", "video"];

export function VisionCapabilitiesPanel() {
  const { t } = useTranslation("vision");
  const queryClient = useQueryClient();
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");

  const { data: status } = useQuery({
    queryKey: ["visionCapabilities"],
    queryFn: () => visionApi.capabilities().then((r) => r.data),
  });
  const { data: meta } = useQuery({
    queryKey: ["configMeta"],
    queryFn: () => configMetaApi.list().then((r) => r.data),
  });

  const presetsItem = meta?.groups
    .flatMap((g) => g.items)
    .find((item) => item.key === "vision_style_presets");
  const presets = (presetsItem?.value ?? {}) as Record<string, string>;

  const saveMutation = useMutation({
    mutationFn: (next: Record<string, string>) =>
      configMetaApi.save("vision_style_presets", next),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["configMeta"] }),
  });

  const addPreset = () => {
    const name = newName.trim();
    const desc = newDesc.trim();
    if (!name || !desc) return;
    saveMutation.mutate({ ...presets, [name]: desc });
    setNewName("");
    setNewDesc("");
  };

  const removePreset = (name: string) => {
    const next = { ...presets };
    delete next[name];
    saveMutation.mutate(next);
  };

  return (
    <div className="space-y-6 max-w-2xl">
      {status && (
        <CapabilityChainPanel
          status={status}
          caps={CAPS}
          configKey="vision_provider_priority"
          ns="vision"
          queryKey="visionCapabilities"
        />
      )}

      <Card title={t("presets.title")} subtitle={t("presets.subtitle")}>
        <div className="space-y-2">
          {Object.entries(presets).map(([name, desc]) => (
            <div
              key={name}
              className="flex items-center gap-2 px-2.5 py-1.5 rounded-md border border-border bg-card"
            >
              <span className="text-xs font-medium text-heading shrink-0">{name}</span>
              <span className="text-xs text-muted truncate flex-1">{desc}</span>
              <button
                onClick={() => removePreset(name)}
                disabled={saveMutation.isPending}
                className="p-1 rounded text-muted hover:text-danger disabled:opacity-50 shrink-0"
              >
                <Trash2 size={13} />
              </button>
            </div>
          ))}
          {Object.keys(presets).length === 0 && (
            <p className="text-xs text-muted">{t("presets.empty")}</p>
          )}
          <div className="flex items-center gap-2 pt-1">
            <input
              type="text"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder={t("presets.namePlaceholder")}
              className="w-40 px-2 py-1.5 rounded-md border border-border bg-elevated text-xs text-foreground"
            />
            <input
              type="text"
              value={newDesc}
              onChange={(e) => setNewDesc(e.target.value)}
              placeholder={t("presets.descPlaceholder")}
              className="flex-1 px-2 py-1.5 rounded-md border border-border bg-elevated text-xs text-foreground"
            />
            <button
              onClick={addPreset}
              disabled={!newName.trim() || !newDesc.trim() || saveMutation.isPending}
              className="flex items-center gap-1 px-2.5 py-1.5 rounded-md bg-accent text-white text-xs font-medium hover:opacity-90 disabled:opacity-50 shrink-0"
            >
              <Plus size={13} />
              {t("presets.add")}
            </button>
          </div>
        </div>
      </Card>
    </div>
  );
}
