import { Select } from "@/components/ui/Select";
import { Button, ConfirmDialog } from "@/components/ui";
import { QueryError } from "@/components/common/AsyncState";
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
  const [confirmClear, setConfirmClear] = useState(false);

  const { data: stm = [] } = useQuery({ queryKey: ["stm"], queryFn: () => memoryApi.stm.list().then((r) => r.data), refetchInterval: 3000 });
  const { data: pendingStatus = [] } = useQuery({ queryKey: ["pendingStatus"], queryFn: () => memoryApi.stm.status().then((r) => r.data), refetchInterval: 3000 });

  const deleteMutation = useMutation({ mutationFn: (index: number) => memoryApi.stm.delete(index), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["stm"] }) });
  const clearMutation = useMutation({ mutationFn: () => memoryApi.stm.clear(), onSuccess: () => {
    void queryClient.invalidateQueries({ queryKey: ["stm"] });
    setConfirmClear(false);
  } });

  const allItems = [
    ...stm.map((item: Record<string, unknown>, i: number) => ({ type: "temporary" as const, content: typeof item === "string" ? item : (item.content as string ?? JSON.stringify(item)), role: (item.role as string) ?? "memory", index: i })),
    ...pendingStatus.map((s: Record<string, string>, i: number) => ({ type: (s.role ?? "pending") as STMFilter, content: s.content ?? "", role: s.role ?? "pending", index: -1 - i })),
  ];
  const filtered = filter === "all" ? allItems : allItems.filter((it) => it.type === filter);
  return (
    <><Card title={t("stmTitle")} subtitle={`${allItems.length} · ${t("autoRefresh")}`} actions={
      <div className="flex flex-wrap items-center gap-2">
        <Select
          value={filter}
          aria-label={t("common:status")}
          onChange={(e) => setFilter(e.target.value as STMFilter)}
        >
          <option value="all">{t("common:all")}</option>
          <option value="temporary">{t("temporary")}</option>
          <option value="pending">{t("pendingFilter")}</option>
          <option value="task">{t("taskFilter")}</option>
          <option value="analysis">{t("analysisFilter")}</option>
        </Select>
        <Button disabled={!stm.length || clearMutation.isPending || deleteMutation.isPending}
          onClick={() => { clearMutation.reset(); setConfirmClear(true); }}><Trash2 size={14} /> {t("common:clear")}</Button>
      </div>
    }>
      <div className="space-y-2 max-h-96 overflow-y-auto">
        {filtered.length === 0 && <p className="text-sm text-muted">{t("common:noData")}</p>}
        {filtered.map((item) => (
          <div key={`${item.type}-${item.index}-${item.role}`} className="flex items-start gap-3 p-3 rounded-md bg-elevated border border-border">
            <span className={cn("text-[10px] px-2 py-0.5 rounded-full font-medium flex-shrink-0 mt-0.5", item.type === "temporary" ? "bg-accent-subtle text-accent" : item.type === "pending" ? "bg-warn-subtle text-warn" : item.type === "task" ? "bg-ok-subtle text-ok" : "bg-secondary text-muted")}>{item.role}</span>
            <p className="text-sm text-foreground flex-1 break-all">{item.content}</p>
            {item.index >= 0 && <Button variant="ghost" size="icon" title={t("common:delete")} className="shrink-0 hover:text-danger"
              disabled={deleteMutation.isPending || clearMutation.isPending} onClick={() => deleteMutation.mutate(item.index)}><Trash2 size={14} /></Button>}
          </div>
        ))}
      </div>
    </Card>
    <ConfirmDialog open={confirmClear} onClose={() => setConfirmClear(false)} title={t("common:clear")}
      danger loading={clearMutation.isPending} onConfirm={() => clearMutation.mutate()}
      message={<><p>{t("clearStmConfirm")}</p>{clearMutation.error && <QueryError title={t("common:requestFailed")} error={clearMutation.error} compact />}</>} />
    </>
  );
}
