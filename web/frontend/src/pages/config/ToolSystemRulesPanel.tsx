import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { configApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { cn } from "@/lib/utils";
import { Check, Save, Plus, Trash2, X, GripVertical } from "lucide-react";

const RULES_FIELD = "tool_system_rules";

export function ToolSystemRulesPanel() {
  const { t } = useTranslation(["status", "common", "appconfig"]);
  const queryClient = useQueryClient();

  const { data: mindConfig } = useQuery({
    queryKey: ["mindConfig"],
    queryFn: () => configApi.getMind().then((r) => r.data?.config || r.data),
  });

  const [editRules, setEditRules] = useState<string[] | null>(null);
  const [saved, setSaved] = useState(false);

  const saveMutation = useMutation({
    mutationFn: (data: Record<string, unknown>) => configApi.saveMind(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["mindConfig"] });
      setEditRules(null);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    },
  });

  const currentRules = (editRules ?? (mindConfig?.[RULES_FIELD] as string[] | undefined)) ?? [];
  const isEditing = editRules !== null;

  const handleStartEdit = () => {
    if (!mindConfig) return;
    setEditRules([...(mindConfig[RULES_FIELD] as string[] ?? [])]);
  };

  const handleSave = () => {
    if (!mindConfig || !editRules) return;
    saveMutation.mutate({ ...mindConfig, [RULES_FIELD]: editRules });
  };

  return (
    <Card
      title={t("toolSystemRules")}
      actions={
        isEditing ? (
          <div className="flex items-center gap-2">
            <button
              onClick={() => setEditRules((prev) => (prev ? [...prev, ""] : [""]))}
              className="flex items-center gap-1 px-2.5 py-1 text-[11px] font-medium rounded-[var(--radius-md)] border border-dashed border-[var(--border)] text-[var(--muted)] hover:border-[var(--accent)] hover:text-[var(--accent)] transition-all"
            >
              <Plus size={12} /> {t("addRule")}
            </button>
            <button
              onClick={handleSave}
              disabled={saveMutation.isPending}
              className={cn(
                "flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)] transition-all",
                saved
                  ? "bg-[var(--ok)] text-white border border-[var(--ok)]"
                  : "bg-[var(--accent)] text-[var(--primary-foreground)] hover:bg-[var(--accent-hover)]",
              )}
            >
              {saved ? <Check size={14} /> : <Save size={14} />}
              {saved ? t("actions.saved", { ns: "appconfig" }) : t("save", { ns: "common" })}
            </button>
            <button
              onClick={() => setEditRules(null)}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--bg-elevated)] text-[var(--muted)] hover:bg-[var(--bg-hover)] transition-all"
            >
              <X size={14} /> {t("cancel", { ns: "common" })}
            </button>
          </div>
        ) : (
          <button
            onClick={handleStartEdit}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--bg-elevated)] text-[var(--muted)] hover:bg-[var(--bg-hover)] transition-all"
          >
            {t("edit", { ns: "common" })}
          </button>
        )
      }
    >
      <div className="space-y-2">
        {currentRules.length > 0 ? (
          currentRules.map((rule, i) => (
            <div key={`rule-${i}`} className="flex items-start gap-2">
              {isEditing && <GripVertical size={14} className="mt-2 text-[var(--muted)] flex-shrink-0 cursor-grab" />}
              <span className="text-[var(--muted)] text-xs font-mono mt-1.5 w-5 flex-shrink-0 text-right">{i + 1}</span>
              {isEditing ? (
                <>
                  <textarea
                    value={rule}
                    rows={1}
                    onChange={(e) => {
                      const newRules = [...(editRules ?? [])];
                      newRules[i] = e.target.value;
                      setEditRules(newRules);
                    }}
                    className="flex-1 bg-transparent border border-[var(--input)] rounded-[var(--radius-sm)] px-2.5 py-1.5 text-xs text-[var(--text)] font-mono outline-none focus:border-[var(--ring)] resize-none"
                  />
                  <button
                    onClick={() => setEditRules((prev) => (prev ? prev.filter((_, j) => j !== i) : null))}
                    className="mt-1 p-1 text-[var(--muted)] hover:text-[var(--danger)] transition-colors flex-shrink-0"
                  >
                    <Trash2 size={13} />
                  </button>
                </>
              ) : (
                <p
                  className={cn(
                    "flex-1 text-xs font-mono py-1.5 px-2.5 rounded-[var(--radius-sm)]",
                    rule.startsWith("#")
                      ? "text-[var(--accent)] font-semibold bg-[var(--accent-subtle)] border border-[var(--accent)]/20"
                      : "text-[var(--text)] bg-[var(--bg-elevated)] border border-[var(--border)]",
                  )}
                >
                  {rule}
                </p>
              )}
            </div>
          ))
        ) : (
          <p className="text-[var(--muted)] text-sm py-2">{t("noRules")}</p>
        )}
      </div>
    </Card>
  );
}
