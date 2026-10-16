import { useTranslation } from "react-i18next";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { KeyRound, MoonStar, Pencil, Pin, Power, Trash2 } from "lucide-react";
import { apiErrorMessage, mcpApi } from "@/lib/api";
import type { MCPServer } from "@/lib/types";
import { Button, toast } from "@/components/ui";

export function ServerActions({ server, oauth, disabled, onEdit, onDelete }: {
  server: MCPServer; oauth?: { authorized: boolean; pending_url: string }; disabled: boolean;
  onEdit: (server: MCPServer) => void; onDelete: (server: MCPServer) => void;
}) {
  const { t } = useTranslation("mcp");
  const queryClient = useQueryClient();
  const authorizeMutation = useMutation({
    mutationFn: () => mcpApi.oauthAuthorize(server.name).then((r) => r.data),
    onSuccess: (data) => {
      if (data.success && data.url) {
        window.open(data.url, "_blank", "noreferrer");
        toast.success(data.message || t("oauthStarted"));
      } else {
        toast.error(data.message || t("toast.requestFailed"));
      }
    },
    onError: (err) => {
      toast.error(apiErrorMessage(err, t("toast.requestFailed")));
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["mcpOauthStatus"] });
      queryClient.invalidateQueries({ queryKey: ["mcpServers"] });
      queryClient.invalidateQueries({ queryKey: ["mcpConfig"] });
    },
  });

  const oauthCapable = server.transport !== "stdio";
  const showAuthorize = oauthCapable && !oauth?.authorized && !oauth?.pending_url;

  const toggleMutation = useMutation({
    mutationFn: () => mcpApi.toggle(server.name).then((r) => r.data),
    onSuccess: (data) => {
      // 启用成功但目标暂不可达：启用已落盘，如实以错误样式提示连接失败
      if (data.success && !(data.enabled && data.connected === false)) {
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
      queryClient.invalidateQueries({ queryKey: ["mcpConfig"] });
    },
  });

  const stayAwakeMutation = useMutation({
    mutationFn: (enabled: boolean) => mcpApi.setStayAwake(server.name, enabled).then((r) => r.data),
    onSuccess: (data) => {
      toast.success(t(data.stay_awake ? "stayAwakeOn" : "stayAwakeOff"));
    },
    onError: (err) => {
      toast.error(apiErrorMessage(err, t("toast.requestFailed")));
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["mcpServers"] });
      queryClient.invalidateQueries({ queryKey: ["mcpConfig"] });
    },
  });

  const busy = disabled || toggleMutation.isPending || stayAwakeMutation.isPending || authorizeMutation.isPending;
  const actions = [
    ...(showAuthorize ? [{ label: t("oauthAuthorize"), icon: KeyRound, run: () => authorizeMutation.mutate(), pending: authorizeMutation.isPending }] : []),
    { label: t(server.stay_awake ? "stayAwakeHintOff" : "stayAwakeHintOn"), icon: server.stay_awake ? Pin : MoonStar,
      run: () => stayAwakeMutation.mutate(!server.stay_awake), pending: stayAwakeMutation.isPending },
    { label: t(server.enabled ? "disable" : "enable"), icon: Power, run: () => toggleMutation.mutate(), pending: toggleMutation.isPending },
    { label: t("common:edit"), icon: Pencil, run: () => onEdit(server), pending: false },
    { label: t("common:delete"), icon: Trash2, run: () => onDelete(server), pending: false },
  ];
  return <div className="flex shrink-0 items-center gap-0.5">
    {actions.map(({ label, icon: Icon, run, pending }) => <Button key={label} variant="ghost" size="icon"
      title={label} aria-label={`${label} · ${server.name}`} disabled={busy} loading={pending} onClick={run}>
      <Icon size={16} />
    </Button>)}
  </div>;
}
