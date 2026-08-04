import { useCallback, useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Pencil, Plug, Plus, RefreshCw, Star, Trash2, Unplug } from "lucide-react";
import { sshApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { StatusDot } from "@/components/common/StatusDot";
import { toast } from "@/stores/toast-store";
import type { SshConnection, SshStatus } from "@/lib/types";

interface ConnectionsPanelProps {
  onEdit: (conn: SshConnection) => void;
  onCreate: () => void;
}

function statusDot(status: SshStatus): "ok" | "warn" | "danger" | "offline" {
  switch (status) {
    case "connected": return "ok";
    case "connecting": return "warn";
    case "error": return "danger";
    default: return "offline";
  }
}

export function ConnectionsPanel({ onEdit, onCreate }: ConnectionsPanelProps) {
  const { t } = useTranslation("ssh");
  const queryClient = useQueryClient();

  const { data, refetch } = useQuery({
    queryKey: ["sshConnections"],
    queryFn: () => sshApi.list().then((r) => r.data),
  });

  // SSE 实时状态推送：连接状态变更即时刷新列表
  useEffect(() => {
    const es = new EventSource("/api/entity/ssh/stream");
    const refresh = () => {
      queryClient.invalidateQueries({ queryKey: ["sshConnections"] });
    };
    es.addEventListener("status", refresh);
    es.onerror = () => {
      // EventSource 会自动重连，静默处理
    };
    return () => es.close();
  }, [queryClient]);

  const invalidate = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ["sshConnections"] });
  }, [queryClient]);

  const connectMutation = useMutation({
    mutationFn: (name: string) => sshApi.connect(name),
    onSuccess: () => { toast.success(t("messages.connectSuccess")); invalidate(); },
    onError: (err: unknown) => {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(msg || t("messages.connectFailed"));
      invalidate();
    },
  });

  const disconnectMutation = useMutation({
    mutationFn: (name: string) => sshApi.disconnect(name),
    onSuccess: () => { toast.success(t("messages.disconnectSuccess")); invalidate(); },
    onError: () => toast.error(t("messages.disconnectFailed")),
  });

  const removeMutation = useMutation({
    mutationFn: (name: string) => sshApi.remove(name),
    onSuccess: () => { toast.success(t("messages.removeSuccess")); invalidate(); },
    onError: () => toast.error(t("messages.removeFailed")),
  });

  const defaultMutation = useMutation({
    mutationFn: (name: string) => sshApi.setDefault(name),
    onSuccess: () => { toast.success(t("messages.defaultSuccess")); invalidate(); },
    onError: () => toast.error(t("messages.defaultFailed")),
  });

  const connections = data?.connections ?? [];

  const handleRemove = (conn: SshConnection) => {
    if (window.confirm(t("messages.confirmRemove", { name: conn.name }))) {
      removeMutation.mutate(conn.name);
    }
  };

  return (
    <Card
      title={t("tabs.connections")}
      subtitle={`${t("status.total")}: ${connections.length}`}
      actions={
        <div className="flex gap-2">
          <button
            onClick={() => refetch()}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border border-border bg-elevated text-muted hover:bg-hover transition-all"
          >
            <RefreshCw size={14} /> {t("actions.refresh")}
          </button>
          <button
            onClick={onCreate}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md bg-accent text-white hover:opacity-90 transition-all"
          >
            <Plus size={14} /> {t("actions.add")}
          </button>
        </div>
      }
    >
      {connections.length === 0 ? (
        <div className="py-10 text-center text-sm text-muted">
          {t("status.empty")}
        </div>
      ) : (
        <div className="space-y-2">
          {connections.map((conn) => (
            <div
              key={conn.name}
              className="flex items-center gap-3 p-3 rounded-lg border border-border bg-elevated hover:border-border-strong transition-all"
            >
              <StatusDot status={statusDot(conn.status)} />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-heading truncate">{conn.name}</span>
                  {conn.is_default && (
                    <span className="flex items-center gap-0.5 text-[10px] px-1.5 py-0.5 rounded bg-accent-subtle text-accent border border-accent">
                      <Star size={9} /> {t("status.default")}
                    </span>
                  )}
                </div>
                <div className="text-xs text-muted truncate mt-0.5">
                  {conn.username}@{conn.host}:{conn.port}
                  {conn.description && ` · ${conn.description}`}
                </div>
                {conn.last_error && (
                  <div className="text-xs text-danger truncate mt-0.5">{conn.last_error}</div>
                )}
              </div>
              <span className={`text-xs px-2 py-0.5 rounded-full border ${
                conn.status === "connected"
                  ? "text-ok border-ok/40 bg-ok/10"
                  : conn.status === "connecting"
                    ? "text-warn border-warn/40 bg-warn/10"
                    : conn.status === "error"
                      ? "text-danger border-danger/40 bg-danger/10"
                      : "text-muted border-border bg-transparent"
              }`}>
                {t(`status.${conn.status}`)}
              </span>
              <div className="flex items-center gap-1">
                {conn.status === "connected" ? (
                  <button
                    onClick={() => disconnectMutation.mutate(conn.name)}
                    title={t("actions.disconnect")}
                    className="p-1.5 rounded-md text-muted hover:text-danger hover:bg-hover transition-all"
                  >
                    <Unplug size={15} />
                  </button>
                ) : (
                  <button
                    onClick={() => connectMutation.mutate(conn.name)}
                    title={t("actions.connect")}
                    className="p-1.5 rounded-md text-muted hover:text-ok hover:bg-hover transition-all"
                  >
                    <Plug size={15} />
                  </button>
                )}
                {!conn.is_default && (
                  <button
                    onClick={() => defaultMutation.mutate(conn.name)}
                    title={t("actions.setDefault")}
                    className="p-1.5 rounded-md text-muted hover:text-accent hover:bg-hover transition-all"
                  >
                    <Star size={15} />
                  </button>
                )}
                <button
                  onClick={() => onEdit(conn)}
                  title={t("actions.edit")}
                  className="p-1.5 rounded-md text-muted hover:text-accent hover:bg-hover transition-all"
                >
                  <Pencil size={15} />
                </button>
                <button
                  onClick={() => handleRemove(conn)}
                  title={t("actions.remove")}
                  className="p-1.5 rounded-md text-muted hover:text-danger hover:bg-hover transition-all"
                >
                  <Trash2 size={15} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
