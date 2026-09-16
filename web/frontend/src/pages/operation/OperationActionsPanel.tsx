import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { operationApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { cn } from "@/lib/utils";

export interface OperationEntry {
  id: string;
  kind: "desktop" | "mcp";
  title: string;
  description: string;
  annotation: string;
  enabled: boolean;
  server?: string;
  tool?: string;
  params?: { name: string; description?: string; type?: string; required?: boolean }[];
  removable: boolean;
}

/** 操作目录：内置桌面动作与注册的 MCP 操作的注释/启停/执行面板。 */
export function OperationActionsPanel() {
  const { t } = useTranslation("operation");
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<string | null>(null);
  const [draftNote, setDraftNote] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["operationList"],
    queryFn: () => operationApi.list().then((r) => r.data.items as OperationEntry[]),
  });
  const { data: status } = useQuery({
    queryKey: ["operationStatus"],
    queryFn: () => operationApi.status().then((r) => r.data),
    refetchInterval: 8000,
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["operationList"] });
  const updateMut = useMutation({
    mutationFn: ({ id, note, enabled }: { id: string; note?: string; enabled?: boolean }) =>
      operationApi.update(id, { ...(note !== undefined ? { note } : {}), ...(enabled !== undefined ? { enabled } : {}) }),
    onSuccess: invalidate,
  });
  const removeMut = useMutation({
    mutationFn: (id: string) => operationApi.remove(id),
    onSuccess: invalidate,
  });

  if (isLoading) return <p className="text-sm text-muted">{t("common:loading")}</p>;
  const items = data || [];

  return (
    <div className="space-y-4">
      <Card title={t("actionsTitle")} subtitle={
        status?.desktop?.available
          ? t("desktopReady", { w: status.desktop.screen?.[0], h: status.desktop.screen?.[1] })
          : t("desktopMissing")
      }>
        <div className="space-y-2">
          {items.map((op) => (
            <div key={op.id} className={cn(
              "p-3 rounded-md border transition-all",
              op.enabled ? "bg-elevated border-border" : "bg-elevated/50 border-border opacity-60",
            )}>
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className={cn(
                      "text-[10px] px-1.5 py-0.5 rounded border",
                      op.kind === "desktop" ? "border-border text-muted" : "bg-accent-subtle text-accent",
                    )}>{op.kind === "desktop" ? t("kindDesktop") : "MCP"}</span>
                    <span className="text-sm font-medium text-foreground truncate">{op.title}</span>
                    <span className="text-[11px] text-muted font-mono">{op.id}</span>
                    {op.server && <span className="text-[11px] text-muted">· {op.server}</span>}
                  </div>
                  <p className="text-xs text-muted mt-1 break-words">{op.description}</p>
                  {editing === op.id ? (
                    <div className="flex gap-2 mt-2">
                      <input
                        value={draftNote}
                        onChange={(e) => setDraftNote(e.target.value)}
                        placeholder={t("notePlaceholder")}
                        className="flex-1 bg-card border border-input rounded-md px-2 py-1 text-xs text-foreground outline-none focus:border-ring"
                      />
                      <button
                        onClick={() => { updateMut.mutate({ id: op.id, note: draftNote }); setEditing(null); }}
                        className="px-2 py-1 text-xs rounded-md bg-accent text-primary-foreground">{t("common:save")}</button>
                      <button onClick={() => setEditing(null)} className="px-2 py-1 text-xs text-muted">{t("common:cancel")}</button>
                    </div>
                  ) : (
                    <p className={cn("text-xs mt-1", op.annotation ? "text-accent" : "text-muted italic")}>
                      {op.annotation ? `“${op.annotation}”` : t("noNote")}
                      <button
                        onClick={() => { setEditing(op.id); setDraftNote(op.annotation); }}
                        className="ml-2 underline underline-offset-2 hover:text-accent">{t("editNote")}</button>
                    </p>
                  )}
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <button
                    onClick={() => updateMut.mutate({ id: op.id, enabled: !op.enabled })}
                    className={cn(
                      "px-2 py-1 text-xs rounded-md border transition-colors",
                      op.enabled
                        ? "text-ok border-[var(--ok)] hover:bg-ok-subtle"
                        : "text-muted border-border",
                    )}>
                    {op.enabled ? t("common:enabled") : t("common:disabled")}
                  </button>
                  {op.removable && (
                    <button
                      onClick={() => removeMut.mutate(op.id)}
                      className="px-2 py-1 text-xs rounded-md border border-border text-muted hover:text-danger hover:border-danger">
                      {t("common:delete")}
                    </button>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
