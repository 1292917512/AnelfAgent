import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { personasApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { PageContainer } from "@/components/common/PageContainer";
import { cn } from "@/lib/utils";
import { Button, Input, Textarea } from "@/components/ui";
import { Plus, Trash2, Star, Save } from "lucide-react";

interface PersonaItem {
  key: string;
  name: string;
  description?: string;
}

export default function Personas() {
  const { t } = useTranslation(["personas", "common"]);
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const [editing, setEditing] = useState<Record<string, unknown> | null>(null);
  const [newKey, setNewKey] = useState("");

  const { data: personas = [] } = useQuery<PersonaItem[]>({
    queryKey: ["personas"],
    queryFn: () => personasApi.list().then((r) => r.data),
  });

  const { data: active } = useQuery({
    queryKey: ["activePersona"],
    queryFn: () => personasApi.active().then((r) => r.data.active as string),
  });

  const { data: personaConfig } = useQuery({
    queryKey: ["personaConfig", selected],
    queryFn: () => selected ? personasApi.get(selected).then((r) => r.data) : null,
    enabled: !!selected,
  });

  const createMutation = useMutation({
    mutationFn: (key: string) => personasApi.create(key),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["personas"] }); setNewKey(""); },
  });

  const deleteMutation = useMutation({
    mutationFn: (key: string) => personasApi.remove(key),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["personas"] }); setSelected(null); },
  });

  const activateMutation = useMutation({
    mutationFn: (key: string) => personasApi.activate(key),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["activePersona"] }),
  });

  const saveMutation = useMutation({
    mutationFn: ({ key, data }: { key: string; data: Record<string, unknown> }) =>
      personasApi.save(key, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["personas"] });
      queryClient.invalidateQueries({ queryKey: ["personaConfig", selected] });
      setEditing(null);
    },
  });

  return (
    <PageContainer wide>
      {/* 新建 */}
      <div className="flex gap-2">
        <Input
          value={newKey}
          onChange={(e) => setNewKey(e.target.value)}
          placeholder={t("newPersonaKey")}
          className="flex-1 max-w-xs"
        />
        <Button
          variant="primary"
          onClick={() => newKey && createMutation.mutate(newKey)}
          disabled={!newKey}
          loading={createMutation.isPending}
        >
          <Plus size={16} /> {t("createNew")}
        </Button>
      </div>

      {/* 人设列表 */}
      <div className="grid gap-3">
        {personas.map((p) => (
          <div
            key={p.key}
            onClick={() => setSelected(p.key === selected ? null : p.key)}
            className={cn(
              "flex items-center justify-between gap-2 p-4 rounded-md border cursor-pointer transition-all",
              "bg-card hover:border-border-strong",
              p.key === selected
                ? "border-accent shadow-[0_0_0_2px_var(--bg),0_0_0_4px_var(--ring)]"
                : "border-border",
            )}
          >
            <div className="flex items-center gap-3 min-w-0">
              {p.key === active && <Star size={16} className="text-warn fill-warn shrink-0" />}
              <div className="min-w-0">
                <span className="font-medium text-heading">{p.name || p.key}</span>
                {p.description && (
                  <p className="text-xs text-muted mt-0.5">{p.description}</p>
                )}
              </div>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <button onClick={(e) => { e.stopPropagation(); activateMutation.mutate(p.key); }}
                className="p-1.5 rounded text-muted hover:text-warn transition-colors" title={t("activate")}>
                <Star size={14} />
              </button>
              <button onClick={(e) => { e.stopPropagation(); deleteMutation.mutate(p.key); }}
                className="p-1.5 rounded text-muted hover:text-danger transition-colors" title={t("common:delete")}>
                <Trash2 size={14} />
              </button>
            </div>
          </div>
        ))}
      </div>

      {/* 人设编辑器 */}
      {selected && personaConfig && (
        <Card
          title={`${t("editPrefix")}: ${selected}`}
          actions={
            editing ? (
              <Button variant="primary" size="sm"
                onClick={() => saveMutation.mutate({ key: selected, data: editing })}
                loading={saveMutation.isPending}>
                <Save size={14} /> {t("common:save")}
              </Button>
            ) : (
              <Button variant="secondary" size="sm" onClick={() => setEditing({ ...personaConfig })}>
                {t("common:edit")}
              </Button>
            )
          }
        >
          <div className="space-y-3">
            <div className="space-y-1">
              <label className="text-xs font-medium text-muted">{t("nameLabel")}</label>
              <Input
                value={String((editing ?? personaConfig).name ?? "")}
                readOnly={!editing}
                onChange={(e) => editing && setEditing({ ...editing, name: e.target.value })}
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium text-muted">{t("descriptionLabel")}</label>
              <Input
                value={String((editing ?? personaConfig).description ?? "")}
                readOnly={!editing}
                onChange={(e) => editing && setEditing({ ...editing, description: e.target.value })}
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium text-muted">{t("personalityLabel")}</label>
              <Textarea
                value={JSON.stringify((editing ?? personaConfig).personality ?? [], null, 2)}
                readOnly={!editing}
                onChange={(e) => {
                  if (!editing) return;
                  try {
                    setEditing({ ...editing, personality: JSON.parse(e.target.value) });
                  } catch { /* 保留当前值直到 JSON 合法 */ }
                }}
                rows={8}
                className="font-mono"
              />
            </div>
          </div>
        </Card>
      )}
    </PageContainer>
  );
}
