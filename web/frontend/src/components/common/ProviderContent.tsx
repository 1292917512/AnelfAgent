import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, Copy } from "lucide-react";
import type { ProviderMetric } from "@/lib/types";

/**
 * 上下文提供者最近一次注入正文（ProvidersTab / ContextProvidersPanel 共用）。
 * 展示归属会话、注入时间与完整文本，支持一键复制。
 */
export function ProviderContent({ provider }: { provider: ProviderMetric }) {
  const { t } = useTranslation("context");
  const [copied, setCopied] = useState(false);

  if (!provider.last_content) {
    return (
      <p className="mt-1.5 text-[10px] text-muted italic">
        {t("providers.noContent")}
      </p>
    );
  }

  const copy = () => {
    navigator.clipboard
      .writeText(provider.last_content ?? "")
      .then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1200);
      })
      .catch(() => { /* 剪贴板不可用 */ });
  };

  return (
    <div className="mt-1.5 rounded-md border border-border bg-card">
      <div className="flex items-center justify-between px-2.5 py-1.5 border-b border-border/60">
        <span className="text-[10px] text-muted font-mono truncate">
          {t("providers.contentMeta", {
            scope: provider.last_content_scope || "-",
            time: (provider.last_content_at ?? 0) > 0
              ? new Date((provider.last_content_at ?? 0) * 1000).toLocaleString()
              : "-",
          })}
        </span>
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); copy(); }}
          className="ml-2 flex items-center gap-1 text-[10px] text-muted hover:text-foreground transition-colors flex-shrink-0"
        >
          {copied ? <Check className="h-3 w-3 text-ok" /> : <Copy className="h-3 w-3" />}
          {copied ? t("providers.copied") : t("providers.copy")}
        </button>
      </div>
      <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all px-2.5 py-2 text-[11px] leading-relaxed text-foreground">
        {provider.last_content}
      </pre>
    </div>
  );
}
