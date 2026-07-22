import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { adaptersApi, apiErrorMessage } from "@/lib/api";
import type { ChannelToolInfo, ChannelToolTestResult } from "@/lib/types";
import { Drawer } from "@/components/common/Drawer";
import { Badge, Button, Input, LoadingBlock, Switch, toast } from "@/components/ui";
import { cn } from "@/lib/utils";
import { FlaskConical, Play, Share2, ShieldAlert, Wrench } from "lucide-react";

export interface ChannelToolsTarget {
  key: string;
  name: string;
}

interface ChannelToolsDrawerProps {
  channel: ChannelToolsTarget | null;
  onClose: () => void;
  /** 跳转到「接口测试」标签页（健康检查 / 发送测试） */
  onGoTest: (key: string) => void;
}

/** 频道接口抽屉：公共能力 / 专属接口的按频道开关 + 单接口调用测试 */
export function ChannelToolsDrawer({ channel, onClose, onGoTest }: ChannelToolsDrawerProps) {
  const { t } = useTranslation("channels");
  const queryClient = useQueryClient();
  const key = channel?.key ?? "";

  const { data, isLoading } = useQuery({
    queryKey: ["channelTools", key],
    queryFn: () => adaptersApi.channelTools(key).then((r) => r.data),
    enabled: !!key,
  });

  const toggleMut = useMutation({
    mutationFn: (name: string) => adaptersApi.toggleChannelTool(key, name),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["channelTools", key] }),
    onError: (e) => toast.error(apiErrorMessage(e, t("tools.toggleFailed"))),
  });

  const tools = data?.tools ?? [];
  const commonTools = tools.filter((tool) => tool.common);
  const specificTools = tools.filter((tool) => !tool.common);

  return (
    <Drawer
      open={!!channel}
      onClose={onClose}
      width="max-w-xl"
      title={channel ? t("tools.title", { name: channel.name }) : ""}
      footer={
        <Button variant="secondary" size="sm" onClick={() => channel && onGoTest(channel.key)}>
          <FlaskConical size={14} /> {t("tools.goTest")}
        </Button>
      }
    >
      {isLoading ? (
        <LoadingBlock />
      ) : !data?.running ? (
        <p className="text-sm text-muted">{t("tools.notRunning")}</p>
      ) : tools.length === 0 ? (
        <p className="text-sm text-muted">{t("tools.empty")}</p>
      ) : (
        <div className="space-y-6">
          <ToolSection
            title={t("tools.common")}
            hint={t("tools.commonHint")}
            tools={commonTools}
            channelKey={key}
            onToggle={(name) => toggleMut.mutate(name)}
          />
          <ToolSection
            title={t("tools.specific")}
            hint={t("tools.specificHint")}
            tools={specificTools}
            channelKey={key}
            onToggle={(name) => toggleMut.mutate(name)}
          />
        </div>
      )}
    </Drawer>
  );
}

function ToolSection({
  title,
  hint,
  tools,
  channelKey,
  onToggle,
}: {
  title: string;
  hint: string;
  tools: ChannelToolInfo[];
  channelKey: string;
  onToggle: (name: string) => void;
}) {
  if (tools.length === 0) return null;
  return (
    <section>
      <div className="mb-2">
        <p className="text-xs font-semibold text-muted uppercase tracking-wider">{title}</p>
        <p className="text-[11px] text-muted mt-0.5">{hint}</p>
      </div>
      <div className="space-y-2">
        {tools.map((tool) => (
          <ToolRow key={tool.name} tool={tool} channelKey={channelKey} onToggle={onToggle} />
        ))}
      </div>
    </section>
  );
}

function ToolRow({
  tool,
  channelKey,
  onToggle,
}: {
  tool: ChannelToolInfo;
  channelKey: string;
  onToggle: (name: string) => void;
}) {
  const { t } = useTranslation("channels");
  const [testing, setTesting] = useState(false);

  return (
    <div className="rounded-md border border-border bg-panel px-3 py-2.5">
      <div className="flex items-center gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className="text-[13px] font-mono font-medium text-heading">{tool.name}</span>
            {tool.common && (
              <Badge variant="accent" className="gap-0.5">
                <Share2 size={10} /> {t("tools.sharedBadge")}
              </Badge>
            )}
            {tool.sensitive && (
              <Badge variant="warn" className="gap-0.5">
                <ShieldAlert size={10} /> {t("tools.sensitive")}
              </Badge>
            )}
            {!tool.globally_enabled && (
              <Badge variant="neutral">{t("tools.globallyDisabled")}</Badge>
            )}
          </div>
          {tool.description && (
            <p className="text-[11px] text-muted mt-0.5 break-all">{tool.description}</p>
          )}
        </div>
        <Button variant="ghost" size="sm" onClick={() => setTesting((v) => !v)}>
          <Wrench size={13} /> {t("tools.test")}
        </Button>
        <Switch
          checked={tool.enabled}
          disabled={!tool.globally_enabled}
          onChange={() => onToggle(tool.name)}
        />
      </div>
      {testing && <ToolTestForm channelKey={channelKey} tool={tool} />}
    </div>
  );
}

function ToolTestForm({ channelKey, tool }: { channelKey: string; tool: ChannelToolInfo }) {
  const { t } = useTranslation("channels");
  // 公共能力的 channel_id 由后端按当前频道自动注入，无需手填
  const params = tool.params.filter((p) => p.name !== "channel_id");
  const [values, setValues] = useState<Record<string, string>>({});
  const [result, setResult] = useState<ChannelToolTestResult | null>(null);

  const runMut = useMutation({
    mutationFn: (args: Record<string, unknown>) =>
      adaptersApi.testChannelTool(channelKey, tool.name, args).then((r) => r.data),
    onSuccess: setResult,
    onError: (e) =>
      setResult({ ready: true, success: false, error: apiErrorMessage(e, "request failed") }),
  });

  const missingRequired = params.some(
    (p) => p.required && p.type !== "boolean" && !(values[p.name] ?? "").trim(),
  );

  const run = () => {
    const args: Record<string, unknown> = {};
    for (const p of params) {
      const raw = (values[p.name] ?? "").trim();
      if (p.type === "boolean") {
        if (raw || p.required) args[p.name] = raw === "true";
        continue;
      }
      if (!raw) continue;
      if (p.type === "integer" || p.type === "number") {
        const n = Number(raw);
        if (!Number.isNaN(n)) {
          args[p.name] = n;
          continue;
        }
      }
      args[p.name] = raw;
    }
    setResult(null);
    runMut.mutate(args);
  };

  const prettyResult = (res: ChannelToolTestResult): string => {
    if (!res.result) return res.error ?? "";
    try {
      return JSON.stringify(JSON.parse(res.result), null, 2);
    } catch {
      return res.result;
    }
  };

  return (
    <div className="mt-2.5 pt-2.5 border-t border-border space-y-2">
      {params.length === 0 && (
        <p className="text-[11px] text-muted">{t("tools.noParams")}</p>
      )}
      {params.map((p) => (
        <div key={p.name} className="flex items-center gap-2">
          <label className="w-32 shrink-0 text-[11px] text-muted">
            <span className="font-mono text-foreground">{p.name}</span>
            {p.required && <span className="text-danger ml-0.5">*</span>}
            {p.description && <span className="block opacity-70 truncate">{p.description}</span>}
          </label>
          {p.type === "boolean" ? (
            <select
              value={values[p.name] ?? ""}
              onChange={(e) => setValues((prev) => ({ ...prev, [p.name]: e.target.value }))}
              className="flex-1 h-8 px-2 text-xs rounded-md border border-border bg-card text-foreground"
            >
              <option value="">{t("tools.unset")}</option>
              <option value="true">true</option>
              <option value="false">false</option>
            </select>
          ) : (
            <Input
              type={p.type === "integer" || p.type === "number" ? "number" : "text"}
              value={values[p.name] ?? ""}
              onChange={(e) => setValues((prev) => ({ ...prev, [p.name]: e.target.value }))}
              placeholder={p.type}
              className="flex-1 h-8 text-xs"
            />
          )}
        </div>
      ))}
      <div className="flex items-center gap-2">
        <Button
          variant="primary"
          size="sm"
          onClick={run}
          disabled={missingRequired}
          loading={runMut.isPending}
        >
          <Play size={13} /> {t("tools.run")}
        </Button>
        {result?.latency_ms != null && (
          <span className="text-[11px] text-muted">
            {t("tools.latency")}: {Math.round(result.latency_ms)} ms
          </span>
        )}
      </div>
      {result && (
        <pre
          className={cn(
            "max-h-64 overflow-auto rounded-md border px-2.5 py-2 text-[11px] font-mono whitespace-pre-wrap break-all",
            result.success
              ? "border-[rgba(34,197,94,0.3)] bg-ok-subtle text-ok"
              : "border-[rgba(239,68,68,0.3)] bg-danger-subtle text-danger",
          )}
        >
          {prettyResult(result)}
        </pre>
      )}
    </div>
  );
}
