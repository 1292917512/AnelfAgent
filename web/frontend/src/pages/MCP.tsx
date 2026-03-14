import { useState, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { mcpApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { StatusDot } from "@/components/common/StatusDot";
import { cn } from "@/lib/utils";
import { Plus, Trash2, Power, Save, Wrench, Loader2 } from "lucide-react";

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
    onSettled: (_data, _err, name) => {
      setTogglingServer(null);
      queryClient.invalidateQueries({ queryKey: ["mcpServers"] });
      if (!name) return;
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
    <div className="space-y-6 max-w-6xl">
      <div className="flex items-center justify-end">
        <button
          onClick={loadConfig}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--bg-elevated)] text-[var(--muted)] hover:bg-[var(--bg-hover)] transition-all"
        >
          {t("jsonConfig")}
        </button>
      </div>

      {/* Add server */}
      <div className="flex gap-2">
        <input
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          placeholder={t("serverName")}
          className="flex-1 max-w-[180px] bg-[var(--card)] border border-[var(--input)] rounded-[var(--radius-md)] px-3 py-2 text-sm text-[var(--text)] outline-none focus:border-[var(--ring)]"
        />
        <input
          value={newUrl}
          onChange={(e) => setNewUrl(e.target.value)}
          placeholder={t("urlOrCommand")}
          className="flex-1 bg-[var(--card)] border border-[var(--input)] rounded-[var(--radius-md)] px-3 py-2 text-sm text-[var(--text)] outline-none focus:border-[var(--ring)]"
        />
        <button
          onClick={() => newName && addMutation.mutate()}
          disabled={!newName || addMutation.isPending}
          className="flex items-center gap-1.5 px-4 py-2 text-sm font-medium rounded-[var(--radius-md)] bg-[var(--accent)] text-[var(--primary-foreground)] hover:bg-[var(--accent-hover)] disabled:opacity-50 transition-all"
        >
          {addMutation.isPending ? (
            <Loader2 size={16} className="animate-spin" />
          ) : (
            <Plus size={16} />
          )}
          {t("common:add")}
        </button>
      </div>

      {/* Server list */}
      <div className="grid gap-3">
        {servers.map((s) => {
          const isToggling = togglingServer === s.name;
          const message = messages[s.name];
          return (
            <div
              key={s.name}
              className={cn(
                "rounded-[var(--radius-lg)] border bg-[var(--card)] p-4 transition-all",
                isToggling
                  ? "border-[var(--warn)]"
                  : "border-[var(--border)] hover:border-[var(--border-strong)]",
              )}
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <StatusDot status={getServerStatus(s)} />
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-[var(--text-strong)]">
                        {s.name}
                      </span>
                      <span
                        className={cn(
                          "text-[11px] px-1.5 py-0.5 rounded-full",
                          isToggling && "text-[var(--warn)]",
                          !isToggling &&
                            s.connected &&
                            "text-[var(--ok)]",
                          !isToggling &&
                            !s.connected &&
                            "text-[var(--muted)]",
                        )}
                      >
                        {getStatusLabel(s)}
                      </span>
                    </div>
                    {s.url && (
                      <p className="text-xs text-[var(--muted)] mt-0.5 font-mono truncate max-w-[400px]">
                        {s.url}
                      </p>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  {s.connected && (
                    <span className="text-xs text-[var(--muted)]">
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
                      !isToggling &&
                        s.connected &&
                        "text-[var(--ok)] hover:text-[var(--danger)]",
                      !isToggling &&
                        !s.connected &&
                        "text-[var(--muted)] hover:text-[var(--ok)]",
                      !!togglingServer &&
                        !isToggling &&
                        "opacity-40 cursor-not-allowed",
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
                      <Loader2
                        size={16}
                        className="animate-spin text-[var(--warn)]"
                      />
                    ) : (
                      <Power size={16} />
                    )}
                  </button>
                  <button
                    onClick={() => removeMutation.mutate(s.name)}
                    disabled={!!togglingServer}
                    className="p-1.5 rounded text-[var(--muted)] hover:text-[var(--danger)] transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
              </div>

              {/* Inline feedback message */}
              {message && (
                <div
                  className={cn(
                    "mt-2 px-3 py-1.5 rounded-[var(--radius-md)] text-xs border-l-2 bg-[var(--bg-elevated)]",
                    message.type === "success" &&
                      "border-l-[var(--ok)] text-[var(--ok)]",
                    message.type === "error" &&
                      "border-l-[var(--danger)] text-[var(--danger)]",
                  )}
                >
                  {message.text}
                </div>
              )}

              {/* Tools */}
              {s.tools.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {s.tools.map((tool) => (
                    <span
                      key={tool}
                      className="text-[11px] px-2 py-0.5 rounded-full bg-[var(--secondary)] border border-[var(--border)] text-[var(--muted)]"
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
          <p className="text-sm text-[var(--muted)] text-center py-8">
            {t("noServers")}
          </p>
        )}
      </div>

      {/* JSON Config Editor */}
      {showConfig && (
        <Card
          title={t("jsonConfig")}
          actions={
            <div className="flex gap-2">
              <button
                onClick={() => setShowConfig(false)}
                className="px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--bg-elevated)] text-[var(--muted)] hover:bg-[var(--bg-hover)] transition-all"
              >
                {t("common:cancel")}
              </button>
              <button
                onClick={() => saveConfigMutation.mutate(configJson)}
                disabled={saveConfigMutation.isPending}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)] bg-[var(--accent)] text-[var(--primary-foreground)] hover:bg-[var(--accent-hover)] disabled:opacity-50 transition-all"
              >
                {saveConfigMutation.isPending ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <Save size={14} />
                )}
                {t("common:save")}
              </button>
            </div>
          }
        >
          <textarea
            value={configJson}
            onChange={(e) => setConfigJson(e.target.value)}
            rows={15}
            className="w-full bg-[var(--bg-elevated)] border border-[var(--input)] rounded-[var(--radius-md)] px-3 py-2 text-sm text-[var(--text)] font-mono outline-none focus:border-[var(--ring)] resize-y"
          />
        </Card>
      )}
    </div>
  );
}
