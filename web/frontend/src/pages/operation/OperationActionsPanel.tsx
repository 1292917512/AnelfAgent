import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Monitor, MousePointerClick, Pencil, Trash2, X } from "lucide-react";
import { operationApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { StatCard } from "@/components/common/StatCard";
import { StatusDot } from "@/components/common/StatusDot";
import { Badge, Button, EmptyState, Input, LoadingBlock, Switch, toast } from "@/components/ui";
import type { OperationEntry, OperationStatus } from "./types";

/** 操作目录：桌面动作与注册 MCP 操作的注释 / 启停 / 移除。 */
export function OperationActionsPanel() {
  const { t } = useTranslation("operation");
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<string | null>(null);
  const [draftNote, setDraftNote] = useState("");

  const { data: items, isLoading } = useQuery({
    queryKey: ["operationList"],
    queryFn: () => operationApi.list().then((r) => r.data.items as OperationEntry[]),
  });
  const { data: status } = useQuery({
    queryKey: ["operationStatus"],
    queryFn: () => operationApi.status().then((r) => r.data as OperationStatus),
    refetchInterval: 8000,
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["operationList"] });
  const updateMut = useMutation({
    mutationFn: ({ id, note, enabled }: { id: string; note?: string; enabled?: boolean }) =>
      operationApi.update(id, {
        ...(note !== undefined ? { note } : {}),
        ...(enabled !== undefined ? { enabled } : {}),
      }),
    onSuccess: () => { invalidate(); toast.success(t("saved")); },
  });
  const removeMut = useMutation({
    mutationFn: (id: string) => operationApi.remove(id),
    onSuccess: (r) => {
      if (r.data.ok) { invalidate(); toast.success(t("removed")); }
      else toast.error(String(r.data.error || t("removeFailed")));
    },
  });

  if (isLoading) return <LoadingBlock />;
  const ops = items || [];
  const desktopOk = Boolean(status?.desktop?.available);

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-3">
        <StatCard
          label={t("statDesktop")}
          variant={desktopOk ? "ok" : "warn"}
          value={
            <span className="flex items-center gap-2 text-base font-medium">
              <StatusDot status={desktopOk ? "ok" : "warn"} />
              {desktopOk
                ? status?.desktop?.screen
                  ? `${status.desktop.screen[0]}×${status.desktop.screen[1]}`
                  : t("desktopReadyShort")
                : t("desktopUnavailable")}
            </span>
          }
        />
        <StatCard label={t("statOperations")} value={`${status?.counts?.enabled ?? 0} / ${status?.counts?.operations ?? 0}`} />
        <StatCard label={t("statMcpRegistered")} value={status?.counts?.mcp_registered ?? 0} />
      </div>
      {!desktopOk && status?.desktop?.hint && (
        <p className="text-xs text-warn">{status.desktop.hint}</p>
      )}

      <Card title={t("actionsTitle")} subtitle={t("actionsSubtitle")}>
        {ops.length === 0 ? (
          <EmptyState icon={MousePointerClick} title={t("noOperations")} />
        ) : (
          <div className="space-y-2">
            {ops.map((op) => (
              <div key={op.id} className="rounded-md border border-border bg-elevated p-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant={op.kind === "desktop" ? "neutral" : "accent"}>
                        {op.kind === "desktop" ? t("kindDesktop") : "MCP"}
                      </Badge>
                      <span className="text-sm font-medium text-foreground">{op.title}</span>
                      <code className="text-[11px] text-muted">{op.id}</code>
                      {op.server && <Badge variant="info">{op.server}</Badge>}
                      {!op.enabled && <Badge variant="warn">{t("common:disabled")}</Badge>}
                    </div>
                    <p className="mt-1 text-xs text-muted break-words">{op.description}</p>

                    {editing === op.id ? (
                      <div className="mt-2 flex gap-2">
                        <Input
                          value={draftNote}
                          onChange={(e) => setDraftNote(e.target.value)}
                          placeholder={t("notePlaceholder")}
                          className="flex-1 text-xs"
                          autoFocus
                          onKeyDown={(e) => {
                            if (e.key === "Enter") {
                              updateMut.mutate({ id: op.id, note: draftNote });
                              setEditing(null);
                            }
                            if (e.key === "Escape") setEditing(null);
                          }}
                        />
                        <Button size="sm" onClick={() => { updateMut.mutate({ id: op.id, note: draftNote }); setEditing(null); }}>
                          {t("common:save")}
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => setEditing(null)}>
                          <X size={14} />
                        </Button>
                      </div>
                    ) : (
                      <p className="mt-1 flex items-center gap-2 text-xs">
                        {op.annotation ? (
                          <span className="text-accent">“{op.annotation}”</span>
                        ) : (
                          <span className="italic text-muted">{t("noNote")}</span>
                        )}
                        <Button
                          size="icon" variant="ghost" className="h-6 w-6"
                          title={t("editNote")}
                          onClick={() => { setEditing(op.id); setDraftNote(op.annotation); }}
                        >
                          <Pencil size={12} />
                        </Button>
                      </p>
                    )}
                  </div>

                  <div className="flex shrink-0 items-center gap-2">
                    <Switch
                      checked={op.enabled}
                      onChange={(v) => updateMut.mutate({ id: op.id, enabled: v })}
                    />
                    {op.removable && (
                      <Button
                        size="icon" variant="ghost" className="h-7 w-7 text-muted hover:text-danger"
                        title={t("common:delete")}
                        onClick={() => removeMut.mutate(op.id)}
                      >
                        <Trash2 size={14} />
                      </Button>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card title={t("guideTitle")} subtitle={t("guideSubtitle")}>
        <ul className="space-y-1.5 text-xs text-muted">
          <li className="flex items-start gap-2"><Monitor size={14} className="mt-0.5 shrink-0 text-accent" />{t("guideLook")}</li>
          <li className="flex items-start gap-2"><MousePointerClick size={14} className="mt-0.5 shrink-0 text-accent" />{t("guideAct")}</li>
          <li className="flex items-start gap-2"><X size={14} className="mt-0.5 shrink-0 text-accent" />{t("guideStop")}</li>
        </ul>
      </Card>
    </div>
  );
}
