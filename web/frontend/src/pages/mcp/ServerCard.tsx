import { useTranslation } from "react-i18next";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, Pencil, Power, Trash2 } from "lucide-react";
import { apiErrorMessage, mcpApi } from "@/lib/api";
import type { MCPServer } from "@/lib/types";
import { StatusDot } from "@/components/common/StatusDot";
import { Badge, toast } from "@/components/ui";
import { cn } from "@/lib/utils";
import { ToolsSection } from "./ToolsSection";

interface ServerCardProps {
  server: MCPServer;
  onEdit: (server: MCPServer) => void;
  onDelete: (server: MCPServer) => void;
}

/** 单个 MCP 服务器卡片：状态、操作（连接/断开、编辑、删除）、工具列表 */
export function ServerCard({ server, onEdit, onDelete }: ServerCardProps) {
  const { t } = useTranslation("mcp");
  const queryClient = useQueryClient();

  const toggleMutation = useMutation({
    mutationFn: () => mcpApi.toggle(server.name).then((r) => r.data),
    onSuccess: (data) => {
      if (data.success) {
        toast.success(data.message);
      } else {
        toast.error(data.message);
      }
    },
    onError: (err) => {
      toast.error(apiErrorMessage(err, t("toast.requestFailed")));
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["mcpServers"] });
    },
  });

  const isToggling = toggleMutation.isPending;
  const status = isToggling ? "warn" : server.connected ? "ok" : "offline";
  const statusLabel = isToggling
    ? server.connected
      ? t("disconnecting")
      : t("connecting")
    : server.connected
      ? t("connectedStatus")
      : t("disconnectedStatus");

  return (
    <div
      className={cn(
        "rounded-lg border bg-card p-4 transition-all",
        isToggling ? "border-warn" : "border-border hover:border-border-strong",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-3 min-w-0">
          <StatusDot status={status} />
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-medium text-heading truncate">{server.name}</span>
              <span
                className={cn(
                  "text-[11px] shrink-0",
                  isToggling && "text-warn",
                  !isToggling && server.connected && "text-ok",
                  !isToggling && !server.connected && "text-muted",
                )}
              >
                {statusLabel}
              </span>
              <Badge variant="neutral">{server.transport}</Badge>
              {!server.enabled && (
                <Badge variant="warn">{t("disabledStatus")}</Badge>
              )}
              {server.connected && (
                <Badge variant="accent">
                  {t("nTools", { count: server.tool_count })}
                </Badge>
              )}
            </div>
            {server.url && (
              <p className="text-xs text-muted mt-0.5 font-mono truncate">
                {server.url}
              </p>
            )}
            {!server.connected && server.last_error && (
              <p className="text-[11px] text-danger mt-1 break-all">
                {t("lastError")}: {server.last_error}
              </p>
            )}
          </div>
        </div>

        <div className="flex items-center gap-1 shrink-0">
          <button
            onClick={() => toggleMutation.mutate()}
            disabled={isToggling}
            title={server.connected ? t("disconnect") : t("connect")}
            className={cn(
              "p-1.5 rounded transition-colors disabled:cursor-wait",
              server.connected
                ? "text-ok hover:text-danger"
                : "text-muted hover:text-ok",
            )}
          >
            {isToggling ? (
              <Loader2 size={16} className="animate-spin text-warn" />
            ) : (
              <Power size={16} />
            )}
          </button>
          <button
            onClick={() => onEdit(server)}
            disabled={isToggling}
            title={t("common:edit")}
            className="p-1.5 rounded text-muted hover:text-foreground transition-colors disabled:opacity-40"
          >
            <Pencil size={15} />
          </button>
          <button
            onClick={() => onDelete(server)}
            disabled={isToggling}
            title={t("common:delete")}
            className="p-1.5 rounded text-muted hover:text-danger transition-colors disabled:opacity-40"
          >
            <Trash2 size={16} />
          </button>
        </div>
      </div>

      {server.connected && (
        <ToolsSection serverName={server.name} toolCount={server.tool_count} />
      )}
    </div>
  );
}
