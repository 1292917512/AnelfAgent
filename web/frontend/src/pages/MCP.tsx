import { useState, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { mcpApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { PageContainer } from "@/components/common/PageContainer";
import { StatusDot } from "@/components/common/StatusDot";
import { cn } from "@/lib/utils";
import { Button, EmptyState, Input, Textarea } from "@/components/ui";
import { Plus, Trash2, Power, Save, Wrench, Loader2, Plug } from "lucide-react";

interface MCPServer {
  name: string;
  url: string;
  enabled: boolean;
  connected: boolean;
  tool_count: number;
  tools: string[];
}

interface ServerMessage {
  type: "success" | "error";
  text: string;
}

export default function MCP() {
  const { t } = useTranslation("mcp");
  const queryClient = useQueryClient();
  const [newName, setNewName] = useState("");
  const [newUrl, setNewUrl] = useState("");
  const [showConfig, setShowConfig] = useState(false);
  const [configJson, setConfigJson] = useState("");
  const [togglingServer, setTogglingServer] = useState<string | null>(null);
  const [messages, setMessages] = useState<Record<string, ServerMessage>>({});

  const showMessage = useCallback((name: string, msg: ServerMessage) => {
    setMessages((prev) => ({ ...prev, [name]: msg }));
    setTimeout(() => {
      setMessages((prev) => {
        const next = { ...prev };
        delete next[name];
        return next;
      });
    }, 6000);
  }, []);

  const { data: servers = [] } = useQuery<MCPServer[]>({
    queryKey: ["mcpServers"],
    queryFn: () => mcpApi.list().then((r) => r.data),
  });

  const addMutation = useMutation({
    mutationFn: () => mcpApi.add(newName, newUrl),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["mcpServers"] });
      setNewName("");
      setNewUrl("");
    },
  });

  const removeMutation = useMutation({
    mutationFn: (name: string) => mcpApi.remove(name),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["mcpServers"] }),
  });

  const toggleMutation = useMutation({
    mutationFn: (name: string) => mcpApi.toggle(name).then((r) => r.data),
    onSettled: () => {
      setTogglingServer(null);
      queryClient.invalidateQueries({ queryKey: ["mcpServers"] });
    },
    onSuccess: (
      data: { success: boolean; message: string },
      name: string,
    ) => {
      showMessage(name, {
        type: data.success ? "success" : "error",
        text: data.message,
      });
    },
    onError: (err: unknown, name: string) => {
      const axErr = err as { response?: { data?: { detail?: string } }; message?: string };
      const msg =
        axErr?.response?.data?.detail || axErr?.message || t("requestFailed");
      showMessage(name, { type: "error", text: msg });
    },
  });

  const saveConfigMutation = useMutation({
    mutationFn: (json: string) => mcpApi.saveConfig(json),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["mcpServers"] });
      setShowConfig(false);
    },
  });

  const loadConfig = async () => {
    const r = await mcpApi.config();
    setConfigJson(r.data.content);
    setShowConfig(true);
  };

  const getServerStatus = (s: MCPServer): "ok" | "warn" | "offline" => {
    if (togglingServer === s.name) return "warn";
    return s.connected ? "ok" : "offline";
  };

  const getStatusLabel = (s: MCPServer): string => {
    if (togglingServer === s.name)
      return s.connected ? t("disconnecting") : t("connecting");
    return s.connected ? t("connectedStatus") : t("disconnectedStatus");
  };

  return (
    <PageContainer wide>
      <div className="flex items-center justify-end">
        <Button variant="secondary" size="sm" onClick={loadConfig}>
          {t("jsonConfig")}
        </Button>
      </div>

      {/* 添加服务器 */}
      <div className="flex gap-2 flex-wrap sm:flex-nowrap">
        <Input
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          placeholder={t("serverName")}
          className="flex-1 min-w-32 sm:max-w-[200px]"
        />
        <Input
          value={newUrl}
          onChange={(e) => setNewUrl(e.target.value)}
          placeholder={t("urlOrCommand")}
          className="flex-2 min-w-40"
        />
        <Button
          variant="primary"
          onClick={() => newName && addMutation.mutate()}
          disabled={!newName}
          loading={addMutation.isPending}
        >
          <Plus size={16} />
          {t("common:add")}
        </Button>
      </div>

      {/* 服务器列表 */}
      <div className="grid gap-3">
        {servers.map((s) => {
          const isToggling = togglingServer === s.name;
          const message = messages[s.name];
          return (
            <div
              key={s.name}
              className={cn(
                "rounded-lg border bg-card p-4 transition-all",
                isToggling
                  ? "border-warn"
                  : "border-border hover:border-border-strong",
              )}
            >
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-3 min-w-0">
                  <StatusDot status={getServerStatus(s)} />
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-heading truncate">
                        {s.name}
                      </span>
                      <span
                        className={cn(
                          "text-[11px] px-1.5 py-0.5 rounded-full shrink-0",
                          isToggling && "text-warn",
                          !isToggling && s.connected && "text-ok",
                          !isToggling && !s.connected && "text-muted",
                        )}
                      >
                        {getStatusLabel(s)}
                      </span>
                    </div>
                    {s.url && (
                      <p className="text-xs text-muted mt-0.5 font-mono truncate">
                        {s.url}
                      </p>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  {s.connected && (
                    <span className="text-xs text-muted hidden sm:inline">
                      <Wrench size={12} className="inline mr-1" />
                      {t("nTools", { count: s.tool_count })}
                    </span>
                  )}
                  <button
                    onClick={() => {
                      setTogglingServer(s.name);
                      toggleMutation.mutate(s.name);
                    }}
                    disabled={!!togglingServer}
                    className={cn(
                      "p-1.5 rounded transition-colors",
                      isToggling && "cursor-wait",
                      !isToggling && s.connected && "text-ok hover:text-danger",
                      !isToggling && !s.connected && "text-muted hover:text-ok",
                      !!togglingServer && !isToggling && "opacity-40 cursor-not-allowed",
                    )}
                    title={
                      isToggling
                        ? s.connected
                          ? t("disconnecting")
                          : t("connecting")
                        : s.connected
                          ? t("disconnect")
                          : t("connect")
                    }
                  >
                    {isToggling ? (
                      <Loader2 size={16} className="animate-spin text-warn" />
                    ) : (
                      <Power size={16} />
                    )}
                  </button>
                  <button
                    onClick={() => removeMutation.mutate(s.name)}
                    disabled={!!togglingServer}
                    className="p-1.5 rounded text-muted hover:text-danger transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
              </div>

              {/* 内联反馈消息 */}
              {message && (
                <div
                  className={cn(
                    "mt-2 px-3 py-1.5 rounded-md text-xs border-l-2 bg-elevated",
                    message.type === "success" && "border-l-ok text-ok",
                    message.type === "error" && "border-l-danger text-danger",
                  )}
                >
                  {message.text}
                </div>
              )}

              {/* 工具 */}
              {s.tools.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {s.tools.map((tool) => (
                    <span
                      key={tool}
                      className="text-[11px] px-2 py-0.5 rounded-full bg-secondary border border-border text-muted"
                    >
                      {tool}
                    </span>
                  ))}
                </div>
              )}
            </div>
          );
        })}
        {servers.length === 0 && (
          <EmptyState icon={Plug} title={t("noServers")} />
        )}
      </div>

      {/* JSON 配置编辑器 */}
      {showConfig && (
        <Card
          title={t("jsonConfig")}
          actions={
            <div className="flex gap-2">
              <Button variant="secondary" size="sm" onClick={() => setShowConfig(false)}>
                {t("common:cancel")}
              </Button>
              <Button
                variant="primary"
                size="sm"
                onClick={() => saveConfigMutation.mutate(configJson)}
                loading={saveConfigMutation.isPending}
              >
                <Save size={14} />
                {t("common:save")}
              </Button>
            </div>
          }
        >
          <Textarea
            value={configJson}
            onChange={(e) => setConfigJson(e.target.value)}
            rows={15}
            className="font-mono"
          />
        </Card>
      )}
    </PageContainer>
  );
}
