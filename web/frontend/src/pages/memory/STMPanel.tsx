import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { memoryApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { cn } from "@/lib/utils";
import { Trash2 } from "lucide-react";

type STMFilter = "all" | "pending" | "task" | "analysis" | "temporary";

export function STMPanel() {
  const { t } = useTranslation("memory");
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState<STMFilter>("all");

  const { data: stm = [] } = useQuery({ queryKey: ["stm"], queryFn: () => memoryApi.stm.list().then((r) => r.data), refetchInterval: 3000 });
  const { data: pfcStatus = [] } = useQuery({ queryKey: ["pfcStatus"], queryFn: () => memoryApi.stm.status().then((r) => r.data), refetchInterval: 3000 });

  const deleteMutation = useMutation({ mutationFn: (index: number) => memoryApi.stm.delete(index), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["stm"] }) });
  const clearMutation = useMutation({ mutationFn: () => memoryApi.stm.clear(), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["stm"] }) });

  const allItems = [
    ...stm.map((item: Record<string, unknown>, i: number) => ({ type: "temporary" as const, content: typeof item === "string" ? item : (item.content as string ?? JSON.stringify(item)), role: (item.role as string) ?? "memory", index: i })),
    ...pfcStatus.map((s: Record<string, string>, i: number) => ({ type: (s.role ?? "pending") as STMFilter, content: s.content ?? "", role: s.role ?? "pending", index: -1 - i })),
  ];
  const filtered = filter === "all" ? allItems : allItems.filter((it) => it.type === filter);
  return (
    <Card title={t("stmTitle")} subtitle={`${allItems.length} · ${t("autoRefresh")}`} actions={
      <div className="flex items-center gap-2">
        <select
          value={filter}
          onChange={(e) => setFilter(e.target.value as STMFilter)}
          className="bg-card border border-input rounded-md px-2 py-1 text-xs text-foreground outline-none"
        >
          <option value="all">{t("common:all")}</option>
          <option value="temporary">{t("temporary")}</option>
          <option value="pending">{t("pendingFilter")}</option>
          <option value="task">{t("taskFilter")}</option>
          <option value="analysis">{t("analysisFilter")}</option>
        </select>
        <button onClick={() => clearMutation.mutate()} className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border border-border bg-danger-subtle text-danger hover:bg-[rgba(239,68,68,0.15)] transition-all"><Trash2 size={14} /> {t("common:clear")}</button>
      </div>
    }>
      <div className="space-y-2 max-h-96 overflow-y-auto">
        {filtered.length === 0 && <p className="text-sm text-muted">{t("common:noData")}</p>}
        {filtered.map((item) => (
          <div key={`${item.type}-${item.index}-${item.role}`} className="flex items-start gap-3 p-3 rounded-md bg-elevated border border-border">
            <span className={cn("text-[10px] px-2 py-0.5 rounded-full font-medium flex-shrink-0 mt-0.5", item.type === "temporary" ? "bg-accent-subtle text-accent" : item.type === "pending" ? "bg-warn-subtle text-warn" : item.type === "task" ? "bg-ok-subtle text-ok" : "bg-secondary text-muted")}>{item.role}</span>
            <p className="text-sm text-foreground flex-1 break-all">{item.content}</p>
            {item.index >= 0 && <button onClick={() => deleteMutation.mutate(item.index)} className="flex-shrink-0 p-1 text-muted hover:text-danger transition-colors"><Trash2 size={14} /></button>}
          </div>
        ))}
      </div>
    </Card>
  );
}
