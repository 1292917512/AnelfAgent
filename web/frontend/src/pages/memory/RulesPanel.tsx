import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { memoryApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { cn } from "@/lib/utils";
import { Check, Save, X } from "lucide-react";
import { useCopyFeedback } from "@/hooks/useCopyFeedback";

/**
 * 记忆体系铁律（完整文档）：stable 工具块的系统级规则段，文件载体为
 * config/memory_rules.md。AI 无写入路径，仅人类在此整文档编辑。
 */
export function RulesPanel() {
  const { t } = useTranslation(["memory", "common", "appconfig"]);
  const queryClient = useQueryClient();

  const { data } = useQuery({
    queryKey: ["memoryRules"],
    queryFn: () => memoryApi.rules.get().then((r) => r.data),
  });

  const [editDoc, setEditDoc] = useState<string | null>(null);
  const [saved, triggerSaved] = useCopyFeedback(2000);

  const saveMutation = useMutation({
    mutationFn: (content: string) => memoryApi.rules.save(content),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["memoryRules"] });
      setEditDoc(null);
      triggerSaved();
    },
  });

  const doc = (data?.content as string | undefined) ?? "";
  const isEditing = editDoc !== null;

  return (
    <Card
      title={t("rulesPanel.title")}
      className="md:flex-1 md:min-h-0 md:flex md:flex-col"
      actions={
        isEditing ? (
          <div className="flex items-center gap-2">
            <button
              onClick={() => editDoc !== null && saveMutation.mutate(editDoc)}
              disabled={saveMutation.isPending}
              className={cn(
                "flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md transition-all",
                saved
                  ? "bg-ok text-white border border-[var(--ok)]"
                  : "bg-accent text-primary-foreground hover:bg-[var(--accent-hover)]",
              )}
            >
              {saved ? <Check size={14} /> : <Save size={14} />}
              {saved ? t("actions.saved", { ns: "appconfig" }) : t("save", { ns: "common" })}
            </button>
            <button
              onClick={() => setEditDoc(null)}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border border-border bg-elevated text-muted hover:bg-hover transition-all"
            >
              <X size={14} /> {t("cancel", { ns: "common" })}
            </button>
          </div>
        ) : (
          <button
            onClick={() => setEditDoc(doc)}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border border-border bg-elevated text-muted hover:bg-hover transition-all"
          >
            {t("edit", { ns: "common" })}
          </button>
        )
      }
    >
      <div className="space-y-2 md:flex-1 md:min-h-0 md:flex md:flex-col">
        <p className="text-muted text-[11px]">{t("rulesPanel.hint")}</p>
        {isEditing ? (
          <textarea
            value={editDoc}
            onChange={(e) => setEditDoc(e.target.value)}
            rows={16}
            className="w-full bg-transparent border border-input rounded-md px-3 py-2 text-xs text-foreground font-mono leading-relaxed outline-none focus:border-ring resize-y md:resize-none md:flex-1 md:min-h-0"
          />
        ) : (
          <pre className="whitespace-pre-wrap break-words text-xs text-foreground font-mono leading-relaxed bg-elevated border border-border rounded-md px-3 py-2 max-h-[45vh] md:max-h-none overflow-y-auto md:flex-1 md:min-h-0">
            {doc}
          </pre>
        )}
      </div>
    </Card>
  );
}
