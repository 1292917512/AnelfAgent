import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { personasApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { cn } from "@/lib/utils";
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
    <div className="space-y-6 max-w-6xl">
      {/* Create */}
      <div className="flex gap-2">
        <input
          value={newKey}
          onChange={(e) => setNewKey(e.target.value)}
          placeholder={t("newPersonaKey")}
          className="flex-1 max-w-xs bg-[var(--card)] border border-[var(--input)] rounded-[var(--radius-md)] px-3 py-2 text-sm text-[var(--text)] outline-none focus:border-[var(--ring)]"
        />
        <button
          onClick={() => newKey && createMutation.mutate(newKey)}
          disabled={!newKey}
          className="flex items-center gap-1.5 px-4 py-2 text-sm font-medium rounded-[var(--radius-md)]
            bg-[var(--accent)] text-[var(--primary-foreground)] hover:bg-[var(--accent-hover)]
            disabled:opacity-50 transition-all"
        >
          <Plus size={16} /> {t("createNew")}
        </button>
      </div>

      {/* Persona List */}
      <div className="grid gap-3">
        {personas.map((p) => (
          <div
            key={p.key}
            onClick={() => setSelected(p.key === selected ? null : p.key)}
            className={cn(
              "flex items-center justify-between p-4 rounded-[var(--radius-md)] border cursor-pointer transition-all",
              "bg-[var(--card)] hover:border-[var(--border-strong)]",
              p.key === selected
                ? "border-[var(--accent)] shadow-[0_0_0_2px_var(--bg),0_0_0_4px_var(--ring)]"
                : "border-[var(--border)]",
            )}
          >
            <div className="flex items-center gap-3">
              {p.key === active && <Star size={16} className="text-[var(--warn)] fill-[var(--warn)]" />}
              <div>
                <span className="font-medium text-[var(--text-strong)]">{p.name || p.key}</span>
                {p.description && (
                  <p className="text-xs text-[var(--muted)] mt-0.5">{p.description}</p>
                )}
              </div>
            </div>
            <div className="flex items-center gap-2">
              <button onClick={(e) => { e.stopPropagation(); activateMutation.mutate(p.key); }}
                className="p-1.5 rounded text-[var(--muted)] hover:text-[var(--warn)] transition-colors" title={t("activate")}>
                <Star size={14} />
              </button>
              <button onClick={(e) => { e.stopPropagation(); deleteMutation.mutate(p.key); }}
                className="p-1.5 rounded text-[var(--muted)] hover:text-[var(--danger)] transition-colors" title={t("common:delete")}>
                <Trash2 size={14} />
              </button>
            </div>
          </div>
        ))}
      </div>

      {/* Persona Editor */}
      {selected && personaConfig && (
        <Card
          title={`${t("editPrefix")}: ${selected}`}
          actions={
            editing ? (
              <button
                onClick={() => saveMutation.mutate({ key: selected, data: editing })}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)]
                  bg-[var(--accent)] text-[var(--primary-foreground)] hover:bg-[var(--accent-hover)] transition-all"
              >
                <Save size={14} /> {t("common:save")}
              </button>
            ) : (
              <button
                onClick={() => setEditing({ ...personaConfig })}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)]
                  border border-[var(--border)] bg-[var(--bg-elevated)] text-[var(--muted)]
                  hover:bg-[var(--bg-hover)] transition-all"
              >
                {t("common:edit")}
              </button>
            )
          }
        >
          <div className="space-y-3">
            <div className="space-y-1">
              <label className="text-xs font-medium text-[var(--muted)]">{t("nameLabel")}</label>
              <input
                value={String((editing ?? personaConfig).name ?? "")}
                readOnly={!editing}
                onChange={(e) => editing && setEditing({ ...editing, name: e.target.value })}
                className="w-full bg-[var(--bg-elevated)] border border-[var(--input)] rounded-[var(--radius-md)] px-3 py-2 text-sm text-[var(--text)] outline-none focus:border-[var(--ring)]"
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium text-[var(--muted)]">{t("descriptionLabel")}</label>
              <input
                value={String((editing ?? personaConfig).description ?? "")}
                readOnly={!editing}
                onChange={(e) => editing && setEditing({ ...editing, description: e.target.value })}
                className="w-full bg-[var(--bg-elevated)] border border-[var(--input)] rounded-[var(--radius-md)] px-3 py-2 text-sm text-[var(--text)] outline-none focus:border-[var(--ring)]"
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium text-[var(--muted)]">{t("personalityLabel")}</label>
              <textarea
                value={JSON.stringify((editing ?? personaConfig).personality ?? [], null, 2)}
                readOnly={!editing}
                onChange={(e) => {
                  if (!editing) return;
                  try {
                    setEditing({ ...editing, personality: JSON.parse(e.target.value) });
                  } catch { /* keep current */ }
                }}
                rows={8}
                className="w-full bg-[var(--bg-elevated)] border border-[var(--input)] rounded-[var(--radius-md)] px-3 py-2 text-sm text-[var(--text)] font-mono outline-none focus:border-[var(--ring)] resize-y"
              />
            </div>
          </div>
        </Card>
      )}
    </div>
  );
}
