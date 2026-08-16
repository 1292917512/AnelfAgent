import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Eye, EyeOff, Plus, Settings2, Trash2 } from "lucide-react";
import { apiErrorMessage, nonebotApi } from "@/lib/api";
import { Badge, Button, EmptyState, Input, LoadingBlock, Switch, Textarea, toast } from "@/components/ui";

interface EnvRow {
  key: string;
  value: string;
  isJson: boolean;
}

const JSON_KEY_RE = /(_BOTS|_CLIENTS|_WS_URLS|COMMAND_START|COMMAND_SEP|NICKNAME|SUPERUSERS)$/i;

function rowsFromEnv(env: Record<string, string> | undefined): EnvRow[] {
  return Object.entries(env || {})
    .filter(([k]) => k !== "DRIVER" && k !== "HOST" && k !== "PORT" && k !== "LOG_LEVEL")
    .map(([key, value]) => ({
      key,
      value,
      isJson: JSON_KEY_RE.test(key) || value.trim().startsWith("[") || value.trim().startsWith("{"),
    }));
}

/** worker .env 高级编辑器：nonebot_env 键值对 + 常用开关（intercept_all / 端口） */
export function EnvPanel() {
  const { t } = useTranslation("nonebot");
  const queryClient = useQueryClient();

  const { data: config, isLoading } = useQuery({
    queryKey: ["nonebotConfig"],
    queryFn: () => nonebotApi.config().then((r) => r.data),
  });

  const [rows, setRows] = useState<EnvRow[]>([]);
  const [reveal, setReveal] = useState<Record<string, boolean>>({});
  const [interceptAll, setInterceptAll] = useState(false);

  useEffect(() => {
    if (config) {
      setRows(rowsFromEnv(config.nonebot_env));
      setInterceptAll(!!config.intercept_all);
    }
  }, [config]);

  const saveMutation = useMutation({
    mutationFn: () => {
      const env: Record<string, string> = {};
      rows.forEach(({ key, value }) => {
        const trimmedKey = key.trim();
        if (trimmedKey) env[trimmedKey] = value;
      });
      return nonebotApi.saveConfig({ nonebot_env: env, intercept_all: interceptAll });
    },
    onSuccess: () => {
      toast.success(t("toast.envSaved"));
      queryClient.invalidateQueries({ queryKey: ["nonebotConfig"] });
      queryClient.invalidateQueries({ queryKey: ["nonebotStatus"] });
    },
    onError: (err) => toast.error(apiErrorMessage(err, t("toast.saveFailed"))),
  });

  if (isLoading) return <LoadingBlock label={t("common:loading")} />;
  if (!config) return <EmptyState icon={Settings2} title={t("loadFailed")} />;

  const updateRow = (index: number, patch: Partial<EnvRow>) => {
    setRows((prev) => prev.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  };

  return (
    <div className="space-y-4">
      {/* 消息处理模式 */}
      <div className="rounded-lg border border-border bg-panel p-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h3 className="text-sm font-medium">{t("env.interceptTitle")}</h3>
            <p className="mt-0.5 text-xs text-muted">{t("env.interceptDesc")}</p>
          </div>
          <Switch checked={interceptAll} onChange={setInterceptAll} />
        </div>
        <div className="mt-2">
          <Badge variant={interceptAll ? "warn" : "ok"}>
            {interceptAll ? t("env.interceptOn") : t("env.passthroughOn")}
          </Badge>
        </div>
      </div>

      {/* 环境变量编辑器 */}
      <div className="rounded-lg border border-border bg-panel p-4">
        <div className="mb-3 flex items-center justify-between">
          <div>
            <h3 className="text-sm font-medium">{t("env.envTitle")}</h3>
            <p className="mt-0.5 text-xs text-muted">{t("env.envDesc")}</p>
          </div>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => setRows((prev) => [...prev, { key: "", value: "", isJson: false }])}
          >
            <Plus size={14} />
            {t("env.addKey")}
          </Button>
        </div>

        {rows.length === 0 ? (
          <p className="text-xs text-muted">{t("env.noKeys")}</p>
        ) : (
          <div className="space-y-2">
            {rows.map((row, index) => {
              const isSecret = /token|secret|key|password/i.test(row.key) && !row.isJson;
              const shown = !isSecret || reveal[row.key + index];
              return (
                <div key={index} className="flex items-start gap-2">
                  <Input
                    className="w-52 shrink-0 font-mono text-xs"
                    placeholder={t("env.keyPlaceholder")}
                    value={row.key}
                    onChange={(e) => updateRow(index, { key: e.target.value })}
                  />
                  {row.isJson ? (
                    <Textarea
                      rows={2}
                      className="min-w-0 flex-1 font-mono text-xs"
                      placeholder='[{"token": "..."}]'
                      value={row.value}
                      onChange={(e) => updateRow(index, { value: e.target.value })}
                    />
                  ) : (
                    <Input
                      className="min-w-0 flex-1 font-mono text-xs"
                      type={shown ? "text" : "password"}
                      value={row.value}
                      onChange={(e) => updateRow(index, { value: e.target.value })}
                    />
                  )}
                  {isSecret && (
                    <Button
                      variant="ghost"
                      size="sm"
                      type="button"
                      onClick={() =>
                        setReveal((prev) => ({ ...prev, [row.key + index]: !prev[row.key + index] }))
                      }
                    >
                      {shown ? <EyeOff size={14} /> : <Eye size={14} />}
                    </Button>
                  )}
                  <Button
                    variant="ghost"
                    size="sm"
                    type="button"
                    className="text-danger"
                    onClick={() => setRows((prev) => prev.filter((_, i) => i !== index))}
                  >
                    <Trash2 size={14} />
                  </Button>
                </div>
              );
            })}
          </div>
        )}

        <div className="mt-3 flex items-center gap-2">
          <Button
            variant="primary"
            size="sm"
            disabled={saveMutation.isPending}
            onClick={() => saveMutation.mutate()}
          >
            {t("common:save")}
          </Button>
          <span className="text-xs text-muted">{t("env.saveHint")}</span>
        </div>
      </div>

      {/* 端口信息（只读展示） */}
      <div className="rounded-lg border border-border bg-panel p-4 text-xs text-muted">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          <div>
            <div className="text-[11px]">{t("env.bridgeWsPort")}</div>
            <div className="font-mono text-foreground">{config.bridge_ws_port}</div>
          </div>
          <div>
            <div className="text-[11px]">{t("env.workerHost")}</div>
            <div className="font-mono text-foreground">{config.worker_host}</div>
          </div>
          <div>
            <div className="text-[11px]">{t("env.workerPort")}</div>
            <div className="font-mono text-foreground">{config.worker_port}</div>
          </div>
        </div>
      </div>
    </div>
  );
}
