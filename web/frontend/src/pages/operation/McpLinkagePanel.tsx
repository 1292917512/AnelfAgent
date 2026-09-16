import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { operationApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { cn } from "@/lib/utils";

interface McpToolEntry {
  server: string;
  name: string;
  description: string;
  params: { name: string; description?: string; type?: string; required?: boolean }[];
}

/** MCP 联动：server 状态、可用工具注册为操作、操作执行与历史。 */
export function McpLinkagePanel() {
  const { t } = useTranslation("operation");
  const queryClient = useQueryClient();
  const [serverFilter, setServerFilter] = useState("");
  const [picked, setPicked] = useState("");
  const [note, setNote] = useState("");
  const [execOp, setExecOp] = useState("");
  const [execArgs, setExecArgs] = useState("{}");
  const [execResult, setExecResult] = useState("");

  const { data: status } = useQuery({
    queryKey: ["operationStatus"],
    queryFn: () => operationApi.status().then((r) => r.data),
    refetchInterval: 8000,
  });
  const { data: list } = useQuery({
    queryKey: ["operationList"],
    queryFn: () => operationApi.list().then((r) => r.data.items as { id: string; kind: string; tool?: string }[]),
  });
  const { data: tools } = useQuery({
    queryKey: ["operationMcpTools", serverFilter],
    queryFn: () => operationApi.mcpTools(serverFilter || undefined).then((r) => r.data.items as McpToolEntry[]),
  });

  const servers = (status?.mcp?.servers || []) as { name: string; connected: boolean; tool_count?: number; enabled?: boolean }[];
  const registeredTools = useMemo(
    () => new Set((list || []).filter((o) => o.kind === "mcp").map((o) => o.tool)),
    [list],
  );
  const mcpOperations = (list || []).filter((o) => o.kind === "mcp");

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["operationList"] });
    queryClient.invalidateQueries({ queryKey: ["operationMcpTools", serverFilter] });
  };
  const registerMut = useMutation({
    mutationFn: () => {
      const entry = (tools || []).find((x) => `${x.server}/${x.name}` === picked);
      if (!entry) throw new Error(t("pickFirst"));
      return operationApi.registerMcp(entry.server, entry.name, note).then((r) => r.data);
    },
    onSuccess: () => { setPicked(""); setNote(""); invalidate(); },
  });
  const execMut = useMutation({
    mutationFn: async () => {
      let args: Record<string, unknown> = {};
      try {
        args = execArgs.trim() ? JSON.parse(execArgs) : {};
      } catch {
        throw new Error(t("invalidJson"));
      }
      const res = await operationApi.execute(execOp, args).then((r) => r.data);
      setExecResult(JSON.stringify(res, null, 2));
    },
  });

  return (
    <div className="space-y-4">
      <Card title={t("mcpTitle")} subtitle={t("mcpSubtitle")}>
        <div className="grid gap-2 md:grid-cols-2 lg:grid-cols-3">
          {servers.length === 0 && <p className="text-sm text-muted">{t("noServers")}</p>}
          {servers.map((sv) => (
            <div key={sv.name} className={cn(
              "p-3 rounded-md border",
              sv.connected ? "bg-ok-subtle border-[var(--ok)]" : "bg-elevated border-border opacity-70",
            )}>
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium">{sv.name}</span>
                <span className={cn("text-[11px]", sv.connected ? "text-ok" : "text-muted")}>
                  {sv.connected ? t("connected") : t("disconnected")}
                </span>
              </div>
              <p className="text-[11px] text-muted mt-1">
                {sv.connected ? t("toolCount", { n: sv.tool_count ?? 0 }) : t("notLinked")}
              </p>
            </div>
          ))}
        </div>
      </Card>

      <Card title={t("registerTitle")} subtitle={t("registerSubtitle")}>
        <div className="flex flex-col gap-2">
          <div className="flex gap-2">
            <select
              value={serverFilter}
              onChange={(e) => { setServerFilter(e.target.value); setPicked(""); }}
              className="bg-elevated border border-input rounded-md px-2 py-1.5 text-xs text-foreground outline-none">
              <option value="">{t("allServers")}</option>
              {servers.filter((s) => s.connected).map((s) => (
                <option key={s.name} value={s.name}>{s.name}</option>
              ))}
            </select>
          </div>
          <div className="max-h-56 overflow-y-auto rounded-md border border-border divide-y divide-border">
            {(tools || []).map((entry) => {
              const key = `${entry.server}/${entry.name}`;
              const registered = registeredTools.has(entry.name);
              return (
                <label key={key} className={cn(
                  "flex items-start gap-2 p-2 cursor-pointer hover:bg-hover",
                  picked === key && "bg-ok-subtle",
                )}>
                  <input
                    type="radio"
                    checked={picked === key}
                    onChange={() => setPicked(key)}
                    disabled={registered}
                    className="mt-1"
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-medium font-mono truncate">{entry.name.split("__").pop()}</span>
                      <span className="text-[10px] text-muted">{entry.server}</span>
                      {registered && <span className="text-[10px] text-accent">{t("registered")}</span>}
                    </div>
                    <p className="text-[11px] text-muted line-clamp-2">{entry.description}</p>
                  </div>
                </label>
              );
            })}
            {(tools || []).length === 0 && (
              <p className="p-2 text-xs text-muted">{t("noTools")}</p>
            )}
          </div>
          <div className="flex gap-2">
            <input
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder={t("notePlaceholder")}
              className="flex-1 bg-card border border-input rounded-md px-2 py-1.5 text-xs text-foreground outline-none focus:border-ring"
            />
            <button
              onClick={() => registerMut.mutate()}
              disabled={!picked || registerMut.isPending}
              className="px-3 py-1.5 text-xs font-medium rounded-md bg-accent text-primary-foreground disabled:opacity-50">
              {t("registerAction")}
            </button>
          </div>
          {registerMut.isError && (
            <p className="text-xs text-danger">{String((registerMut.error as Error).message)}</p>
          )}
          {registerMut.data?.ok && <p className="text-xs text-ok">{t("registeredAs", { id: registerMut.data.op_id })}</p>}
        </div>
      </Card>

      <Card title={t("execTitle")} subtitle={t("execSubtitle")}>
        <div className="flex flex-col gap-2">
          <div className="flex gap-2">
            <select
              value={execOp}
              onChange={(e) => setExecOp(e.target.value)}
              className="bg-elevated border border-input rounded-md px-2 py-1.5 text-xs text-foreground outline-none">
              <option value="">{t("pickOperation")}</option>
              {mcpOperations.map((o) => <option key={o.id} value={o.id}>{o.id}</option>)}
            </select>
            <button
              onClick={() => execMut.mutate()}
              disabled={!execOp || execMut.isPending}
              className="px-3 py-1.5 text-xs font-medium rounded-md bg-accent text-primary-foreground disabled:opacity-50">
              {t("execAction")}
            </button>
          </div>
          <textarea
            value={execArgs}
            onChange={(e) => setExecArgs(e.target.value)}
            rows={3}
            placeholder='{"url": "https://example.com"}'
            className="w-full bg-card border border-input rounded-md px-2 py-1.5 text-xs font-mono text-foreground outline-none focus:border-ring resize-y"
          />
          {execMut.isError && <p className="text-xs text-danger">{(execMut.error as Error).message}</p>}
          {execResult && (
            <pre className="p-2 rounded-md bg-elevated border border-border text-[11px] font-mono whitespace-pre-wrap break-all max-h-48 overflow-y-auto">{execResult}</pre>
          )}
        </div>
      </Card>

      <Card title={t("historyTitle")} subtitle={t("historySubtitle")}>
        <div className="space-y-1">
          {(status?.history || []).length === 0 && <p className="text-xs text-muted">{t("noHistory")}</p>}
          {(status?.history || []).map((h: { op: string; detail: string; ok: boolean; ts: number }, i: number) => (
            <div key={i} className="flex items-start gap-2 text-xs">
              <span className={cn("shrink-0", h.ok ? "text-ok" : "text-danger")}>{h.ok ? "✓" : "✗"}</span>
              <span className="font-mono text-muted shrink-0">{h.op}</span>
              <span className="text-muted truncate flex-1">{h.detail}</span>
              <span className="text-[10px] text-muted shrink-0">{new Date(h.ts * 1000).toLocaleTimeString()}</span>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
