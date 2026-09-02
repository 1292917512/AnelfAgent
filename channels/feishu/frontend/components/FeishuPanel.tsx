/**
 * 飞书频道面板 — 连接状态 + 健康探测 + 接入指引 + 测试发送。
 *
 * 复用核心 /adapters 管理面 API，无独立后端路由。
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { apiErrorMessage } from "@/lib/api";
import { feishuApi } from "../api";
import { cn } from "@/lib/utils";
import type { ChannelTestHealthResult } from "@/lib/types/channels";
import type { FeishuTestSendResult } from "../types";
import {
  Activity, ExternalLink, Loader2, Send, Wifi, WifiOff,
} from "lucide-react";

export default function FeishuPanel() {
  const { t } = useTranslation("channel-feishu");
  const [probing, setProbing] = useState(false);
  const [health, setHealth] = useState<ChannelTestHealthResult | null>(null);
  const [chatId, setChatId] = useState("");
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [sendResult, setSendResult] = useState<FeishuTestSendResult | null>(null);

  const { data: adapterList } = useQuery({
    queryKey: ["adapters", "feishu-panel"],
    queryFn: async () => (await feishuApi.listAdapters()).data,
    refetchInterval: 15000,
  });
  const adapter = adapterList?.adapters.find((a) => a.key === "feishu");
  const running = adapter?.status === "running";

  const probe = async () => {
    setProbing(true);
    try {
      setHealth((await feishuApi.health()).data);
    } catch (e) {
      setHealth({ ready: false, error: apiErrorMessage(e, "probe failed") });
    } finally {
      setProbing(false);
    }
  };

  const send = async () => {
    if (!chatId.trim() || !text.trim() || sending) return;
    setSending(true);
    setSendResult(null);
    try {
      setSendResult((await feishuApi.testSend(chatId.trim(), text.trim())).data as FeishuTestSendResult);
    } catch (e) {
      setSendResult({ ready: true, success: false, error: apiErrorMessage(e, "send failed") });
    } finally {
      setSending(false);
    }
  };

  const steps = t("panel.guideSteps", { returnObjects: true }) as string[];

  return (
    <div className="rounded-md border border-border bg-panel p-3 space-y-3">
      {/* 状态行 */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-2 text-sm">
          {running ? (
            <Wifi size={14} className="text-ok" />
          ) : (
            <WifiOff size={14} className="text-muted" />
          )}
          <span className="font-medium">{t("panel.title")}</span>
          <span className="text-xs text-muted">
            {adapter?.status_display ?? t("panel.notRunning")}
            {adapter?.detail ? ` · ${adapter.detail}` : ""}
          </span>
        </div>
        <button
          onClick={probe}
          disabled={probing}
          className={cn(
            "flex items-center gap-1 text-xs px-2 py-1 rounded border border-border",
            "hover:bg-secondary transition-colors disabled:opacity-50",
          )}
        >
          {probing ? <Loader2 size={12} className="animate-spin" /> : <Activity size={12} />}
          {probing ? t("panel.probing") : t("panel.probe")}
        </button>
      </div>

      {/* 探测结果 */}
      {health && (
        <div className={cn(
          "text-xs rounded border px-2.5 py-2",
          health.healthy
            ? "border-[rgba(34,197,94,0.3)] bg-ok-subtle text-ok"
            : "border-[rgba(239,68,68,0.3)] bg-danger-subtle text-danger",
        )}>
          {health.healthy ? t("panel.healthy") : t("panel.unhealthy")}
          {health.detail ? ` · ${health.detail}` : ""}
          {health.latency_ms != null && ` · ${t("panel.latency", { ms: Math.round(health.latency_ms) })}`}
          {health.error ? ` · ${health.error}` : ""}
        </div>
      )}

      {/* 能力标签 */}
      {adapter?.capabilities && adapter.capabilities.length > 0 && (
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className="text-xs text-muted">{t("panel.capabilities")}:</span>
          {adapter.capabilities.map((c) => (
            <span key={c} className="text-[11px] px-1.5 py-0.5 rounded bg-secondary text-muted border border-border">
              {c}
            </span>
          ))}
        </div>
      )}

      {/* 测试发送 */}
      <div className="space-y-1.5">
        <div className="text-xs font-medium text-muted">{t("panel.testTitle")}</div>
        <input
          value={chatId}
          onChange={(e) => setChatId(e.target.value)}
          placeholder={t("panel.chatIdPlaceholder")}
          className="w-full text-xs px-2 py-1.5 rounded border border-border bg-secondary/40 outline-none focus:border-accent"
        />
        <div className="flex gap-1.5">
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && send()}
            placeholder={t("panel.textPlaceholder")}
            className="flex-1 text-xs px-2 py-1.5 rounded border border-border bg-secondary/40 outline-none focus:border-accent"
          />
          <button
            onClick={send}
            disabled={sending || !chatId.trim() || !text.trim()}
            className={cn(
              "flex items-center gap-1 text-xs px-3 py-1.5 rounded",
              "bg-accent text-white hover:opacity-90 transition-opacity disabled:opacity-50",
            )}
          >
            {sending ? <Loader2 size={12} className="animate-spin" /> : <Send size={12} />}
            {sending ? t("panel.sending") : t("panel.send")}
          </button>
        </div>
        {sendResult && (
          <div className={cn(
            "text-xs rounded border px-2.5 py-2",
            sendResult.success
              ? "border-[rgba(34,197,94,0.3)] bg-ok-subtle text-ok"
              : "border-[rgba(239,68,68,0.3)] bg-danger-subtle text-danger",
          )}>
            {sendResult.success
              ? (sendResult.rendered_as === "post"
                ? t("panel.sendOkRich", { id: sendResult.message_id })
                : t("panel.sendOk", { id: sendResult.message_id }))
              : `${t("panel.sendFailed")}: ${sendResult.error ?? "unknown"}${sendResult.hint ? ` · ${sendResult.hint}` : ""}`}
          </div>
        )}
      </div>

      {/* 接入指引 */}
      <details className="text-xs">
        <summary className="cursor-pointer text-muted hover:text-foreground transition-colors">
          {t("panel.guideTitle")}
        </summary>
        <ol className="list-decimal ml-4 mt-1.5 space-y-1 text-muted">
          {Array.isArray(steps) && steps.map((s, i) => <li key={i}>{s}</li>)}
        </ol>
        <a
          href="https://open.feishu.cn/document/home/index"
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 mt-1.5 text-accent hover:underline"
        >
          <ExternalLink size={11} />
          {t("panel.guideLink")}
        </a>
      </details>
    </div>
  );
}
