import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { adaptersApi, apiErrorMessage } from "@/lib/api";
import type { AdapterInfo, ChannelTestHealthResult, ChannelTestSendResult } from "@/lib/types";
import { Card } from "@/components/common/Card";
import { StatusDot } from "@/components/common/StatusDot";
import { Button, Input, Select, Textarea } from "@/components/ui";
import { cn } from "@/lib/utils";
import {
  Activity,
  Bot,
  CheckCircle,
  FlaskConical,
  Send,
  XCircle,
  Zap,
} from "lucide-react";

const HIDDEN_CHANNELS = new Set(["nonebot_bridge"]);

/** 频道接口测试面板：健康检查 + 真实消息发送 + 能力查看 */
export function ChannelTestPanel({ initialKey = "" }: { initialKey?: string }) {
  const { t } = useTranslation("channels");
  const [selectedKey, setSelectedKey] = useState<string>(initialKey);
  const [health, setHealth] = useState<ChannelTestHealthResult | null>(null);
  const [healthLoading, setHealthLoading] = useState(false);
  const [chatId, setChatId] = useState("");
  const [text, setText] = useState("");
  const [sendResult, setSendResult] = useState<ChannelTestSendResult | null>(null);
  const [sendLoading, setSendLoading] = useState(false);

  // 从频道接口抽屉跳转时预选频道
  useEffect(() => {
    if (!initialKey) return;
    setSelectedKey(initialKey);
    setHealth(null);
    setSendResult(null);
  }, [initialKey]);

  const { data } = useQuery({
    queryKey: ["adapters"],
    queryFn: () => adaptersApi.list().then((r) => r.data),
    refetchInterval: 10000,
  });

  const adapters: AdapterInfo[] = (data?.adapters ?? []).filter(
    (a) => !HIDDEN_CHANNELS.has(a.key),
  );
  const running = adapters.filter((a) => a.status === "running");
  const selected = adapters.find((a) => a.key === selectedKey) ?? null;

  const runHealth = async () => {
    if (!selectedKey) return;
    setHealthLoading(true);
    setHealth(null);
    try {
      const r = await adaptersApi.testHealth(selectedKey);
      setHealth(r.data);
    } catch (e) {
      setHealth({ ready: true, error: apiErrorMessage(e, "request failed") });
    } finally {
      setHealthLoading(false);
    }
  };

  const runSend = async () => {
    if (!selectedKey || !chatId.trim() || !text.trim()) return;
    setSendLoading(true);
    setSendResult(null);
    try {
      const r = await adaptersApi.testSend(selectedKey, { chat_id: chatId.trim(), text: text.trim() });
      setSendResult(r.data);
    } catch (e) {
      setSendResult({ ready: true, success: false, error: apiErrorMessage(e, "request failed") });
    } finally {
      setSendLoading(false);
    }
  };

  const capabilities = health?.capabilities ?? selected?.capabilities ?? [];

  return (
    <div className="space-y-4">
      <p className="text-xs text-muted">{t("test.desc")}</p>

      {/* 通道选择 */}
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-xs font-medium text-muted flex items-center gap-1.5">
          <FlaskConical size={14} /> {t("test.selectChannel")}
        </span>
        <Select
          value={selectedKey}
          onChange={(e) => {
            setSelectedKey(e.target.value);
            setHealth(null);
            setSendResult(null);
          }}
          className="min-w-48"
        >
          <option value="">{t("test.selectPlaceholder")}</option>
          {adapters.map((a) => (
            <option key={a.key} value={a.key}>
              {a.name}（{a.status_display}）
            </option>
          ))}
        </Select>
        {selected && (
          <span className="flex items-center gap-1.5 text-xs text-muted">
            <StatusDot status={selected.status === "running" ? "ok" : selected.status === "error" ? "danger" : "offline"} />
            {selected.status_display}
          </span>
        )}
      </div>

      {running.length === 0 && (
        <p className="text-xs text-warn">{t("test.noRunning")}</p>
      )}

      {selected && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {/* 健康检查 */}
          <Card
            title={t("test.healthTitle")}
            actions={
              <Button variant="primary" size="sm" onClick={runHealth} loading={healthLoading}>
                <Activity size={14} /> {t("test.runHealth")}
              </Button>
            }
          >
            {!health && !healthLoading && (
              <p className="text-sm text-muted">{t("test.notTested")}</p>
            )}
            {health && (
              <div className="space-y-3">
                {health.error && !health.running ? (
                  <div className="flex items-center gap-2 text-sm text-warn">
                    <XCircle size={15} /> {health.error}
                  </div>
                ) : (
                  <>
                    <div className={cn(
                      "flex items-center gap-2 px-3 py-2.5 rounded-md text-sm border",
                      health.healthy
                        ? "bg-ok-subtle text-ok border-[rgba(34,197,94,0.3)]"
                        : "bg-danger-subtle text-danger border-[rgba(239,68,68,0.3)]",
                    )}>
                      {health.healthy ? <CheckCircle size={15} /> : <XCircle size={15} />}
                      <span className="font-medium">
                        {health.healthy ? t("test.healthy") : t("test.unhealthy")}
                      </span>
                      {health.latency_ms != null && (
                        <span className="ml-auto text-xs opacity-80">
                          {t("test.latency")}: {Math.round(health.latency_ms)} ms
                        </span>
                      )}
                    </div>
                    {health.health_detail && (
                      <p className="text-xs text-muted font-mono break-all">{health.health_detail}</p>
                    )}
                    {health.last_error && (
                      <p className="text-xs text-danger break-all">
                        {t("test.lastError")}: {health.last_error}
                      </p>
                    )}
                    {health.self_info && (
                      <div className="flex items-center gap-2 text-xs text-muted">
                        <Bot size={14} />
                        <span>{t("test.botIdentity")}:</span>
                        <span className="text-foreground font-medium">
                          {health.self_info.user_name || health.self_info.user_id}
                        </span>
                        <span className="opacity-70">({health.self_info.platform})</span>
                      </div>
                    )}
                  </>
                )}
              </div>
            )}
          </Card>

          {/* 发送测试消息 */}
          <Card title={t("test.sendTitle")}>
            <div className="space-y-3">
              <div>
                <label className="block text-xs text-muted mb-1">{t("test.chatId")}</label>
                <Input
                  value={chatId}
                  onChange={(e) => setChatId(e.target.value)}
                  placeholder={t("test.chatIdPlaceholder")}
                  className="w-full"
                />
              </div>
              <div>
                <label className="block text-xs text-muted mb-1">{t("test.messageText")}</label>
                <Textarea
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  placeholder={t("test.messagePlaceholder")}
                  rows={3}
                  className="w-full"
                />
              </div>
              <div className="flex items-center gap-3">
                <Button
                  variant="primary"
                  size="sm"
                  onClick={runSend}
                  disabled={!chatId.trim() || !text.trim() || selected.status !== "running"}
                  loading={sendLoading}
                >
                  <Send size={14} /> {t("test.send")}
                </Button>
                {sendResult && (
                  <span className={cn(
                    "flex items-center gap-1.5 text-xs",
                    sendResult.success ? "text-ok" : "text-danger",
                  )}>
                    {sendResult.success ? <CheckCircle size={14} /> : <XCircle size={14} />}
                    {sendResult.success
                      ? `${t("test.sendSuccess")}${sendResult.message_id ? ` · ${t("test.messageId")}: ${sendResult.message_id}` : ""}`
                      : `${t("test.sendFailed")}: ${sendResult.error ?? ""}`}
                  </span>
                )}
              </div>
            </div>
          </Card>

          {/* 通道能力 */}
          <Card title={t("test.capabilitiesTitle")} className="lg:col-span-2">
            {capabilities.length === 0 ? (
              <p className="text-sm text-muted">{t("test.noCapabilities")}</p>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {capabilities.map((cap) => (
                  <span
                    key={cap}
                    className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-[11px] bg-secondary text-muted border border-border"
                  >
                    <Zap size={11} className="text-accent" />
                    {t(`cap.${cap}`, { defaultValue: cap })}
                  </span>
                ))}
              </div>
            )}
          </Card>
        </div>
      )}
    </div>
  );
}
