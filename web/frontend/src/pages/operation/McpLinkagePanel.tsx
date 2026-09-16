import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { History, Play, Plug, Plus } from "lucide-react";
import { operationApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { StatusDot } from "@/components/common/StatusDot";
import {
  Badge, Button, EmptyState, Input, LoadingBlock, Select, Textarea, toast,
} from "@/components/ui";
import { cn } from "@/lib/utils";
import type { McpToolEntry, OperationEntry, OperationStatus } from "./types";
import { formatAgo } from "./types";

/** MCP 联动：server 状态 → 工具注册为操作 → 参数化测试执行 → 执行历史。 */
export function McpLinkagePanel() {
  const { t } = useTranslation("operation");
  const queryClient = useQueryClient();
  const [serverFilter, setServerFilter] = useState("");
  const [picked, setPicked] = useState("");
  const [note, setNote] = useState("");
  const [execOp, setExecOp] = useState("");
  const [execArgs, setExecArgs] = useState("{}");
  const [execResult, setExecResult] = useState("");

  const { data: status, isLoading } = useQuery({
    queryKey: ["operationStatus"],
    queryFn: () => operationApi.status().then((r) => r.data as OperationStatus),
    refetchInterval: 8000,
  });
  const { data: list } = useQuery({
    queryKey: ["operationList"],
    queryFn: () => operationApi.list().then((r) => r.data.items as OperationEntry[]),
  });
  const { data: tools } = useQuery({
    queryKey: ["operationMcpTools", serverFilter],
    queryFn: () =>
      operationApi.mcpTools(serverFilter || undefined).then((r) => r.data.items as McpToolEntry[]),
  });

  const servers = status?.mcp?.servers ?? [];
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
      if (!entry) return Promise.reject(new Error(t("pickFirst")));
      return operationApi.registerMcp(entry.server, entry.name, note).then((r) => r.data);
    },
    onSuccess: (res) => {
      if (res.ok) {
        toast.success(t("registeredAs", { id: res.op_id }));
        setPicked(""); setNote(""); invalidate();
      } else {
        toast.error(String(res.error || t("registerFailed")));
      }
    },
    onError: (err: unknown) => toast.error((err as Error).message),
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
      if (!res.ok) toast.error(String(res.error || t("execFailed")));
      queryClient.invalidateQueries({ queryKey: ["operationStatus"] });
    },
    onError: (err: unknown) => toast.error((err as Error).message),
  });

  if (isLoading) return <LoadingBlock />;

  return (
    <div className="space-y-4">
      <Card title={t("mcpTitle")} subtitle={t("mcpSubtitle")}>
        {servers.length === 0 ? (
          <EmptyState icon={Plug} title={t("noServers")} description={t("noServersHint")} />
        ) : (
          <div className="grid gap-2 md:grid-cols-2 lg:grid-cols-3">
            {servers.map((sv) => (
              <div
                key={sv.name}
                className={cn(
                  "rounded-md border p-3",
                  sv.connected ? "border-[var(--ok)] bg-ok-subtle" : "border-border bg-elevated opacity-70",
                )}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="flex items-center gap-2 text-sm font-medium">
                    <StatusDot status={sv.connected ? "ok" : "offline"} />
                    {sv.name}
                  </span>
                  <Badge variant={sv.connected ? "ok" : "neutral"}>
                    {sv.connected ? t("connected") : t("disconnected")}
                  </Badge>
                </div>
                <p className="mt-1 text-[11px] text-muted">
                  {sv.connected ? t("toolCount", { n: sv.tool_count ?? 0 }) : (sv.last_error || t("notLinked"))}
                </p>
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card title={t("registerTitle")} subtitle={t("registerSubtitle")}>
        <div className="flex flex-col gap-3">
          <div className="flex gap-2">
            <Select
              value={serverFilter}
              onChange={(e) => { setServerFilter(e.target.value); setPicked(""); }}
              className="w-48"
            >
              <option value="">{t("allServers")}</option>
              {servers.filter((s) => s.connected).map((s) => (
                <option key={s.name} value={s.name}>{s.name}</option>
              ))}
            </Select>
          </div>

          <div className="max-h-64 divide-y divide-border overflow-y-auto rounded-md border border-border">
            {(tools || []).length === 0 && (
              <p className="p-3 text-xs text-muted">{t("noTools")}</p>
            )}
            {(tools || []).map((entry) => {
              const key = `${entry.server}/${entry.name}`;
              const registered = registeredTools.has(entry.name);
              return (
                <label
                  key={key}
                  className={cn(
                    "flex cursor-pointer items-start gap-2 p-2.5 hover:bg-hover",
                    picked === key && "bg-ok-subtle",
                    registered && "opacity-60",
                  )}
                >
                  <input
                    type="radio"
                    checked={picked === key}
                    onChange={() => setPicked(key)}
                    disabled={registered}
                    className="mt-1"
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <code className="truncate text-xs font-medium">
                        {entry.name.split("__").pop()}
                      </code>
                      <Badge variant="neutral">{entry.server}</Badge>
                      {registered && <Badge variant="accent">{t("registered")}</Badge>}
                    </div>
                    <p className="mt-0.5 line-clamp-2 text-[11px] text-muted">{entry.description}</p>
                  </div>
                </label>
              );
            })}
          </div>

          <div className="flex gap-2">
            <Input
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder={t("notePlaceholder")}
              className="flex-1"
            />
            <Button onClick={() => registerMut.mutate()} disabled={!picked || registerMut.isPending}>
              <Plus size={14} /> {t("registerAction")}
            </Button>
          </div>
        </div>
      </Card>

      <Card title={t("execTitle")} subtitle={t("execSubtitle")}>
        {mcpOperations.length === 0 ? (
          <EmptyState icon={Play} title={t("noRegisteredOps")} description={t("noRegisteredOpsHint")} />
        ) : (
          <div className="flex flex-col gap-2">
            <div className="flex gap-2">
              <Select
                value={execOp}
                onChange={(e) => setExecOp(e.target.value)}
                className="w-72"
              >
                <option value="">{t("pickOperation")}</option>
                {mcpOperations.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.annotation ? `${o.annotation} · ${o.id}` : o.id}
                  </option>
                ))}
              </Select>
              <Button onClick={() => execMut.mutate()} disabled={!execOp || execMut.isPending}>
                <Play size={14} /> {t("execAction")}
              </Button>
            </div>
            <Textarea
              value={execArgs}
              onChange={(e) => setExecArgs(e.target.value)}
              rows={3}
              placeholder='{"url": "https://example.com"}'
              className="font-mono text-xs"
            />
            {execResult && (
              <pre className="max-h-48 overflow-y-auto whitespace-pre-wrap break-all rounded-md border border-border bg-elevated p-2 font-mono text-[11px]">
                {execResult}
              </pre>
            )}
          </div>
        )}
      </Card>

      <Card title={t("historyTitle")} subtitle={t("historySubtitle")}>
        {(status?.history || []).length === 0 ? (
          <EmptyState icon={History} title={t("noHistory")} />
        ) : (
          <div className="space-y-1.5">
            {(status?.history || []).map((h, i) => (
              <div key={i} className="flex items-center gap-2 text-xs">
                <Badge variant={h.ok ? "ok" : "danger"}>{h.ok ? "✓" : "✗"}</Badge>
                <code className="shrink-0 text-muted">{h.op}</code>
                <span className="flex-1 truncate text-muted" title={h.detail}>{h.detail}</span>
                <span className="shrink-0 text-[10px] text-muted">{formatAgo(h.ts)}</span>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
