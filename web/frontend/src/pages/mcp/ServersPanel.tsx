import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plug, Plus, RefreshCw } from "lucide-react";
import { apiErrorMessage, mcpApi } from "@/lib/api";
import type { MCPServer } from "@/lib/types";
import { Button, ConfirmDialog, EmptyState, LoadingBlock, toast } from "@/components/ui";
import { ServerCard } from "./ServerCard";
import { ServerFormModal } from "./ServerFormModal";
import type { EditingServer } from "./types";

/** MCP 服务器列表面板：状态轮询 + 增删改 + 连接控制 */
export function ServersPanel() {
  const { t } = useTranslation("mcp");
  const queryClient = useQueryClient();

  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<EditingServer | null>(null);
  const [deleting, setDeleting] = useState<MCPServer | null>(null);

  const {
    data: servers = [],
    isLoading,
    isError,
    refetch,
  } = useQuery({
    queryKey: ["mcpServers"],
    queryFn: () => mcpApi.list().then((r) => r.data),
    refetchInterval: 5000,
  });

  const removeMutation = useMutation({
    mutationFn: (name: string) => mcpApi.remove(name),
    onSuccess: (_r, name) => {
      queryClient.invalidateQueries({ queryKey: ["mcpServers"] });
      toast.success(t("toast.serverDeleted", { name }));
      setDeleting(null);
    },
    onError: (err) => {
      toast.error(apiErrorMessage(err, t("toast.requestFailed")));
    },
  });

  const handleEdit = async (server: MCPServer) => {
    try {
      const r = await mcpApi.get(server.name);
      setEditing({ name: server.name, config: r.data });
      setFormOpen(true);
    } catch (err) {
      toast.error(apiErrorMessage(err, t("toast.requestFailed")));
    }
  };

  const handleAdd = () => {
    setEditing(null);
    setFormOpen(true);
  };

  if (isLoading) {
    return <LoadingBlock label={t("common:loading")} />;
  }

  if (isError) {
    return (
      <EmptyState
        icon={Plug}
        title={t("loadFailed")}
        action={
          <Button variant="secondary" size="sm" onClick={() => refetch()}>
            <RefreshCw size={14} />
            {t("retry")}
          </Button>
        }
      />
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-end">
        <Button variant="primary" size="sm" onClick={handleAdd}>
          <Plus size={15} />
          {t("addServer")}
        </Button>
      </div>

      {servers.length === 0 ? (
        <EmptyState
          icon={Plug}
          title={t("noServers")}
          description={t("noServersHint")}
          action={
            <Button variant="primary" size="sm" onClick={handleAdd}>
              <Plus size={15} />
              {t("addServer")}
            </Button>
          }
        />
      ) : (
        <div className="grid gap-3">
          {servers.map((s) => (
            <ServerCard
              key={s.name}
              server={s}
              onEdit={handleEdit}
              onDelete={setDeleting}
            />
          ))}
        </div>
      )}

      <ServerFormModal
        open={formOpen}
        onClose={() => setFormOpen(false)}
        editing={editing}
      />

      <ConfirmDialog
        open={!!deleting}
        onClose={() => setDeleting(null)}
        onConfirm={() => deleting && removeMutation.mutate(deleting.name)}
        title={t("deleteTitle")}
        message={deleting ? t("deleteMessage", { name: deleting.name }) : ""}
        confirmText={t("common:delete")}
        cancelText={t("common:cancel")}
        danger
        loading={removeMutation.isPending}
      />
    </div>
  );
}
