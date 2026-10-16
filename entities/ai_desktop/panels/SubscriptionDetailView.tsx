import { useTranslation } from "react-i18next";
import { useState } from "react";
import { ExternalLink, QrCode } from "lucide-react";
import { Button } from "@/components/ui";
import { SubscriptionLoginDialog } from "./SubscriptionLoginDialog";
import type { DesktopModuleInfo, QuotaWindow } from "./types";

function quotaText(window: QuotaWindow): string | null {
  if (window.balance != null) {
    const symbol = { CNY: "¥", USD: "$", EUR: "€" }[window.currency ?? "CNY"] ?? `${window.currency} `;
    return `${symbol}${window.balance.toFixed(2)}`;
  }
  if (window.limit != null && window.remaining != null && window.limit < 10000) {
    return `${window.remaining}/${window.limit}`;
  }
  return window.remaining_percent != null ? `${Math.round(window.remaining_percent)}%` : null;
}

function QuotaRow({ window }: { window: QuotaWindow }) {
  const { t, i18n } = useTranslation("ai_desktop");
  const percent = window.remaining_percent == null ? null : Math.max(0, Math.min(100, window.remaining_percent));
  const color = percent !== null && percent <= 10 ? "bg-danger" : percent !== null && percent <= 30 ? "bg-warn" : "bg-ok";
  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 text-xs">
        <span className="text-muted">{window.label}</span>
        <span className="font-medium tabular-nums text-foreground">
          {window.balance == null && `${t("subscription.remaining")} `}
          {quotaText(window) ?? t("subscription.unknown")}
        </span>
      </div>
      {window.balance == null && percent !== null && (
        <div
          role="progressbar" aria-label={window.label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}
          className="h-1.5 overflow-hidden rounded-full bg-border/50"
        >
          <div className={`h-full rounded-full ${color}`} style={{ width: `${percent}%` }} />
        </div>
      )}
      {window.reset_at != null && (
        <p className="text-[11px] text-muted">
          {t("subscription.resetAt")} {new Date(window.reset_at * 1000).toLocaleString(i18n.language, {
            month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
          })}
        </p>
      )}
    </div>
  );
}

/** 展示额度快照、查询状态与控制台登录入口。 */
export function SubscriptionDetailView({ mod }: { mod: DesktopModuleInfo }) {
  const { t, i18n } = useTranslation("ai_desktop");
  const [loginOpen, setLoginOpen] = useState(false);
  return (
    <div className="space-y-2">
      {!mod.detail.providers?.length && <p className="py-2 text-xs text-muted">{t("subscription.notConfigured")}</p>}
      {(mod.detail.providers ?? []).map((provider) => {
        const needsConsole = provider.state === "no_credential" && provider.auth_type === "console";
        const lowQuota = provider.state === "ok" && provider.windows?.some(
          (window) => window.remaining_percent != null && window.remaining_percent <= 10,
        );
        return (
          <section key={provider.key} aria-label={provider.name} className="space-y-2 rounded-md border border-border/60 bg-elevated p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h4 className="text-[13px] font-medium text-foreground">{provider.name}</h4>
              {provider.plan && <span className="rounded bg-accent/10 px-1.5 py-0.5 text-[10px] text-accent">{provider.plan}</span>}
            </div>
            {needsConsole ? (
              <div className="space-y-2 text-xs text-muted">
                <p>{t(provider.source ? "subscription.consoleRequired" : "subscription.cliRequired")}</p>
                {provider.source ? (
                  <Button size="sm" variant="secondary" disabled={!mod.enabled} onClick={() => setLoginOpen(true)}>
                    <QrCode className="mr-1 h-3.5 w-3.5" />{t("subscription.login")}
                  </Button>
                ) : (
                  <a href="https://bailian.aliyun.com/cli/install.md" target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-accent hover:underline">
                    {t("subscription.installGuide")}<ExternalLink className="h-3 w-3" />
                  </a>
                )}
              </div>
            ) : provider.state !== "ok" ? (
              <p className={`break-words text-xs ${provider.state === "error" ? "text-danger" : "text-muted"}`}>
                {provider.state === "error" ? provider.error : t(`subscription.${provider.state === "pending" ? "pending" : "noCredential"}`)}
              </p>
            ) : (
              <>
                <div className="space-y-3">
                  {provider.windows?.map((window, index) => <QuotaRow key={`${window.label}-${index}`} window={window} />)}
                </div>
                {provider.fetched_at != null && (
                  <p className="text-[10px] text-muted">
                    {t("detail.fetchedAt")} {new Date(provider.fetched_at * 1000).toLocaleString(i18n.language)}
                  </p>
                )}
              </>
            )}
            {lowQuota && <p className="text-[11px] text-danger">{t("subscription.lowQuota")}</p>}
          </section>
        );
      })}
      {loginOpen && <SubscriptionLoginDialog onClose={() => setLoginOpen(false)} />}
    </div>
  );
}
