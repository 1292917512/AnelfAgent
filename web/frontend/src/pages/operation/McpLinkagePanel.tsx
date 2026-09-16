import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { History, Pencil, Play, Plug, Plus, Trash2, X } from "lucide-react";
import { operationApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { StatusDot } from "@/components/common/StatusDot";
import {
  Badge, Button, EmptyState, Input, LoadingBlock, Modal, Select, Switch,
  Textarea, toast,
} from "@/components/ui";
import { cn } from "@/lib/utils";
import type { McpToolEntry, OperationEntry, OperationStatus } from "./types";
import { formatAgo } from "./types";

/** MCP 联动：已关联操作管理（注释/启停/测试/移除）+ 批量添加关联 + 执行历史。
 *
 * 关联 = 语义索引：注入操作态势让 AI 知道有哪些语义化能力；
 * AI 实际执行走 mcp:<server> 工具组，Web 测试执行仅供人工验证关联有效。
 */
export function McpLinkagePanel() {
  const { t } = useTranslation("operation");
  const queryClient = useQueryClient();
  const [showAdd, setShowAdd] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [draftNote, setDraftNote] = useState("");
  const [execOp, setExecOp] = useState<string | null>(null);
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

  const linked = (list || []).filter((o) => o.kind === "mcp");
  const servers = status?.mcp?.servers ?? [];

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["operationList"] });
    queryClient.invalidateQueries({ queryKey: ["operationStatus"] });
  };
  const updateMut = useMutation({
    mutationFn: ({ id, note, enabled }: { id: string; note?: string; enabled?: boolean }) =>
      operationApi.update(id, {
        ...(note !== undefined ? { note } : {}),
        ...(enabled !== undefined ? { enabled } : {}),
      }),
    onSuccess: () => { invalidate(); },
  });
  const removeMut = useMutation({
    mutationFn: (id: string) => operationApi.remove(id),
    onSuccess: (r) => {
      if (r.data.ok) { invalidate(); toast.success(t("removed")); }
      else toast.error(String(r.data.error || t("removeFailed")));
    },
  });
  const execMut = useMutation({
    mutationFn: async (opId: string) => {
      let args: Record<string, unknown> = {};
      try {
        args = execArgs.trim() ? JSON.parse(execArgs) : {};
      } catch {
        throw new Error(t("invalidJson"));
      }
      const res = await operationApi.execute(opId, args).then((r) => r.data);
      setExecResult(JSON.stringify(res, null, 2));
      if (!res.ok) toast.error(String(res.error || t("execFailed")));
      queryClient.invalidateQueries({ queryKey: ["operationStatus"] });
    },
    onError: (err: unknown) => toast.error((err as Error).message),
  });

  if (isLoading) return <LoadingBlock />;

  return (
    <div className="space-y-4">
      <Card
        title={t("linkedTitle")}
        subtitle={t("linkedSubtitle")}
        actions={
          <Button size="sm" onClick={() => setShowAdd(true)} disabled={servers.every((s) => !s.connected)}>
            <Plus size={14} /> {t("addLink")}
          </Button>
        }
      >
        {linked.length === 0 ? (
          <EmptyState
            icon={Plug}
            title={t("noLinked")}
            description={t("noLinkedHint")}
            action={
              <Button size="sm" variant="secondary" onClick={() => setShowAdd(true)}
                disabled={servers.every((s) => !s.connected)}>
                <Plus size={14} /> {t("addLink")}
              </Button>
            }
          />
        ) : (
          <div className="space-y-2">
            {linked.map((op) => (
              <div key={op.id} className={cn(
                "rounded-md border border-border bg-elevated p-3",
                !op.enabled && "opacity-60",
              )}>
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium text-foreground">{op.title}</span>
                      <Badge variant="info">mcp:{op.server}</Badge>
                      <code className="text-[11px] text-muted">{op.id}</code>
                    </div>
                    <p className="mt-1 text-xs text-muted break-words">{op.description}</p>
                    {op.params.length > 0 && (
                      <p className="mt-0.5 text-[11px] text-muted">
                        {t("paramsLabel")}: {op.params.map((p) => p.name).join(", ")}
                      </p>
                    )}
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
                  <div className="flex shrink-0 items-center gap-1.5">
                    <Button
                      size="icon" variant="ghost" className="h-7 w-7 text-muted hover:text-accent"
                      title={t("execAction")}
                      onClick={() => {
                        setExecOp(execOp === op.id ? null : op.id);
                        setExecResult("");
                      }}
                    >
                      <Play size={14} />
                    </Button>
                    <Switch
                      checked={op.enabled}
                      onChange={(v) => updateMut.mutate({ id: op.id, enabled: v })}
                    />
                    <Button
                      size="icon" variant="ghost" className="h-7 w-7 text-muted hover:text-danger"
                      title={t("unlink")}
                      onClick={() => removeMut.mutate(op.id)}
                    >
                      <Trash2 size={14} />
                    </Button>
                  </div>
                </div>
                {execOp === op.id && (
                  <div className="mt-2 space-y-2 border-t border-border pt-2">
                    <Textarea
                      value={execArgs}
                      onChange={(e) => setExecArgs(e.target.value)}
                      rows={2}
                      placeholder='{"url": "https://example.com"}'
                      className="font-mono text-xs"
                    />
                    <div className="flex items-center gap-2">
                      <Button size="sm" onClick={() => execMut.mutate(op.id)} disabled={execMut.isPending}>
                        <Play size={13} /> {t("execAction")}
                      </Button>
                      <span className="text-[11px] text-muted">{t("execHint")}</span>
                    </div>
                    {execResult && (
                      <pre className="max-h-40 overflow-y-auto whitespace-pre-wrap break-all rounded-md border border-border bg-card p-2 font-mono text-[11px]">
                        {execResult}
                      </pre>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </Card>

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

      <AddLinkModal open={showAdd} onClose={() => setShowAdd(false)} onDone={invalidate} />
    </div>
  );
}

/** 添加关联：选 server → 工具多选（已关联禁选）→ 批量注册。 */
function AddLinkModal({ open, onClose, onDone }: {
  open: boolean;
  onClose: () => void;
  onDone: () => void;
}) {
  const { t } = useTranslation("operation");
  const [server, setServer] = useState("");
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState("");

  const { data: serversData } = useQuery({
    queryKey: ["operationStatus"],
    queryFn: () => operationApi.status().then((r) => r.data as OperationStatus),
  });
  const { data: tools } = useQuery({
    queryKey: ["operationMcpTools", server],
    queryFn: () => operationApi.mcpTools(server || undefined).then((r) => r.data.items as McpToolEntry[]),
    enabled: open && Boolean(server),
  });
  const { data: list } = useQuery({
    queryKey: ["operationList"],
    queryFn: () => operationApi.list().then((r) => r.data.items as OperationEntry[]),
  });

  const servers = (serversData?.mcp?.servers ?? []).filter((s) => s.connected);
  const linkedTools = useMemo(
    () => new Set((list || []).filter((o) => o.kind === "mcp").map((o) => o.tool)),
    [list],
  );
  const filtered = (tools || []).filter((x) =>
    !search || x.name.toLowerCase().includes(search.toLowerCase())
    || (x.description || "").toLowerCase().includes(search.toLowerCase()),
  );
  const selectable = filtered.filter((x) => !linkedTools.has(x.name));

  const registerMut = useMutation({
    mutationFn: async () => {
      const picked = (tools || []).filter((x) => checked.has(`${x.server}/${x.name}`));
      for (const entry of picked) {
        const res = await operationApi.registerMcp(entry.server, entry.name, "").then((r) => r.data);
        if (!res.ok) throw new Error(String(res.error || entry.name));
      }
      return picked.length;
    },
    onSuccess: (n) => {
      toast.success(t("linkedCount", { n }));
      setChecked(new Set()); setSearch("");
      onDone();
      onClose();
    },
    onError: (err: unknown) => toast.error((err as Error).message),
  });

  const toggle = (key: string) => {
    const next = new Set(checked);
    if (next.has(key)) next.delete(key); else next.add(key);
    setChecked(next);
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={t("addLinkTitle")}
      width="max-w-2xl"
      footer={
        <div className="flex items-center justify-between gap-2">
          <span className="text-xs text-muted">
            {checked.size > 0 ? t("pickedCount", { n: checked.size }) : t("pickToolsHint")}
          </span>
          <div className="flex gap-2">
            <Button variant="ghost" onClick={onClose}>{t("common:cancel")}</Button>
            <Button onClick={() => registerMut.mutate()} disabled={checked.size === 0 || registerMut.isPending}>
              <Plus size={14} /> {t("linkAction")}
            </Button>
          </div>
        </div>
      }
    >
      <div className="space-y-3">
        <div className="flex gap-2">
          <Select value={server} onChange={(e) => { setServer(e.target.value); setChecked(new Set()); }} className="w-52">
            <option value="">{t("pickServer")}</option>
            {servers.map((s) => (
              <option key={s.name} value={s.name}>{s.name}（{s.tool_count ?? 0}）</option>
            ))}
          </Select>
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t("searchTools")}
            className="flex-1"
            disabled={!server}
          />
          <Button
            size="sm" variant="secondary" disabled={!server}
            onClick={() => setChecked(
              checked.size === selectable.length
                ? new Set()
                : new Set(selectable.map((x) => `${x.server}/${x.name}`)),
            )}
          >
            {t("selectAll")}
          </Button>
        </div>
        <div className="max-h-80 divide-y divide-border overflow-y-auto rounded-md border border-border">
          {!server && <p className="p-3 text-xs text-muted">{t("pickServerHint")}</p>}
          {server && filtered.length === 0 && <p className="p-3 text-xs text-muted">{t("noTools")}</p>}
          {filtered.map((entry) => {
            const key = `${entry.server}/${entry.name}`;
            const isLinked = linkedTools.has(entry.name);
            return (
              <label key={key} className={cn(
                "flex items-start gap-2 p-2.5",
                isLinked ? "opacity-50" : "cursor-pointer hover:bg-hover",
                checked.has(key) && "bg-ok-subtle",
              )}>
                <input
                  type="checkbox"
                  checked={checked.has(key)}
                  disabled={isLinked}
                  onChange={() => toggle(key)}
                  className="mt-1"
                />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <code className="truncate text-xs font-medium">{entry.name.split("__").pop()}</code>
                    {isLinked && <Badge variant="accent">{t("registered")}</Badge>}
                  </div>
                  <p className="mt-0.5 line-clamp-2 text-[11px] text-muted">{entry.description}</p>
                </div>
              </label>
            );
          })}
        </div>
      </div>
    </Modal>
  );
}
