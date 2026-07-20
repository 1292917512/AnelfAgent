import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { memoryApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { cn } from "@/lib/utils";
import { Trash2 } from "lucide-react";

export function ConvPanel() {
  const { t } = useTranslation("memory");
  const queryClient = useQueryClient();
  const { data: scopes = [] } = useQuery({ queryKey: ["convScopes"], queryFn: () => memoryApi.conv.scopes().then((r) => r.data) });
  const [selected, setSelected] = useState<{ type: string; id: string } | null>(null);
  const { data: messages = [] } = useQuery({ queryKey: ["convMessages", selected], enabled: !!selected, queryFn: () => selected ? memoryApi.conv.messages(selected.type, selected.id).then((r) => r.data) : [] });

  const deleteMsgMutation = useMutation({ mutationFn: (rowId: number) => memoryApi.conv.delete(rowId), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["convMessages", selected] }) });
  const clearConvMutation = useMutation({
    mutationFn: async () => { if (selected) await memoryApi.conv.clear(selected.type, selected.id); },
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["convMessages", selected] }); queryClient.invalidateQueries({ queryKey: ["convScopes"] }); },
  });

  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
      <Card title={t("conversationList")}>
        <div className="space-y-1 max-h-80 overflow-y-auto">
          {scopes.length === 0 && <p className="text-sm text-muted">{t("noConversation")}</p>}
          {scopes.map((s: Record<string, string>) => (
            <button key={`${s.scope_type}-${s.scope_id}`} onClick={() => setSelected({ type: s.scope_type ?? "", id: s.scope_id ?? "" })}
              className={cn("w-full text-left p-2 rounded-md text-sm transition-colors", selected?.id === s.scope_id ? "bg-accent-subtle text-accent" : "text-foreground hover:bg-hover")}>
              {s.scope_id}<span className="text-xs text-muted ml-1">({s.scope_type})</span>
            </button>
          ))}
        </div>
      </Card>
      <Card title={t("messageRecord")} className="md:col-span-2" actions={selected ? (
        <button onClick={() => clearConvMutation.mutate()} className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border border-border bg-danger-subtle text-danger hover:bg-[rgba(239,68,68,0.15)] transition-all"><Trash2 size={14} /> {t("clearConversation")}</button>
      ) : undefined}>
        <div className="flex flex-col-reverse space-y-2 space-y-reverse max-h-96 overflow-y-auto">
          {!selected && <p className="text-sm text-muted">{t("selectConversation")}</p>}
          {[...messages].reverse().map((m: Record<string, unknown>) => (
            <div key={m.id != null ? String(m.id) : `${m.role}-${m.timestamp}`} className={cn("flex items-start gap-2 p-2 rounded-md text-sm", m.role === "user" ? "bg-accent-subtle" : "bg-elevated border border-border")}>
              <div className="flex-1 min-w-0">
                <span className="text-xs font-medium text-muted">{String(m.role)}</span>
                <p className="text-foreground mt-0.5 break-all">{String(m.content)}</p>
              </div>
              {typeof m.id === "number" && <button onClick={() => deleteMsgMutation.mutate(m.id as number)} className="flex-shrink-0 p-1 text-muted hover:text-danger transition-colors"><Trash2 size={13} /></button>}
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
