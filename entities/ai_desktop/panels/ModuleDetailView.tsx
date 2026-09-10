import { useTranslation } from "react-i18next";
import { CalendarClock, CalendarDays, Clock3, Globe2, PartyPopper } from "lucide-react";
import type { DesktopModuleInfo, SubscriptionProvider } from "./types";

interface DetailCellProps {
  icon: typeof Clock3;
  label: string;
  value: string;
}

function DetailCell({ icon: Icon, label, value }: DetailCellProps) {
  return (
    <div className="rounded-md bg-elevated border border-border/60 px-3 py-2">
      <div className="flex items-center gap-1 text-[10px] text-muted">
        <Icon className="h-3 w-3" />
        {label}
      </div>
      <div className="mt-0.5 text-[13px] font-medium text-foreground break-all">
        {value}
      </div>
    </div>
  );
}

/** 日期时间组件的实时详情（主时区时间/时区/节日/假日倒计时） */
function DatetimeDetailView({ mod }: { mod: DesktopModuleInfo }) {
  const { t } = useTranslation("ai_desktop");
  const d = mod.detail;
  const festivals = d.festivals_today ?? [];
  const next = d.next_holiday;
  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
      <DetailCell
        icon={Clock3}
        label={t("detail.now")}
        value={d.datetime ? `${d.datetime} ${d.weekday ?? ""}`.trim() : "-"}
      />
      <DetailCell icon={Globe2} label={t("detail.timezone")} value={d.timezone || "-"} />
      <DetailCell
        icon={PartyPopper}
        label={t("detail.festivalsToday")}
        value={festivals.length > 0 ? festivals.join("、") : t("detail.none")}
      />
      <DetailCell
        icon={CalendarClock}
        label={t("detail.nextHoliday")}
        value={next
          ? `${next.name} ${next.date}（${t("detail.daysLater", { count: next.days })}）`
          : t("detail.noneInWindow")}
      />
    </div>
  );
}

/** 订阅额度组件的实时详情（逐供应商窗口余量条 + 重置时间） */
function SubscriptionDetailView({ mod }: { mod: DesktopModuleInfo }) {
  const { t } = useTranslation("ai_desktop");
  const providers = mod.detail.providers ?? [];

  const formatReset = (resetAt?: number | null): string => {
    if (!resetAt) return "-";
    const d = new Date(resetAt * 1000);
    const hm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
    return `${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")} ${hm}`;
  };

  const quotaText = (w: NonNullable<SubscriptionProvider["windows"]>[number]): string => {
    if (w.limit != null && w.remaining != null && w.limit < 10000) {
      return `${w.remaining}/${w.limit}`;
    }
    if (w.remaining_percent != null) return `${Math.round(w.remaining_percent)}%`;
    return "-";
  };

  const stateText = (p: SubscriptionProvider): string => {
    if (p.state === "no_credential") return t("subscription.noCredential");
    if (p.state === "pending") return t("subscription.pending");
    return p.error ?? "";
  };

  return (
    <div className="space-y-2">
      {providers.map((p) => {
        const pct = p.state === "ok"
          ? Math.min(...(p.windows ?? []).map((w) => w.remaining_percent ?? 100))
          : null;
        return (
          <div
            key={p.key}
            className="rounded-md bg-elevated border border-border/60 px-3 py-2"
          >
            <div className="flex items-center gap-2">
              <span className="text-[13px] font-medium text-foreground">{p.name}</span>
              {p.plan && (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-accent/10 text-accent">
                  {p.plan}
                </span>
              )}
              <span className="flex-1" />
              {p.state !== "ok" && (
                <span className={`text-[11px] ${p.state === "error" ? "text-danger" : "text-muted"}`}>
                  {stateText(p)}
                </span>
              )}
            </div>
            {p.state === "ok" && (
              <div className="mt-2 space-y-1.5">
                {(p.windows ?? []).map((w, i) => (
                  <div key={`${w.label}-${i}`} className="flex items-center gap-2">
                    <span className="w-16 text-[11px] text-muted flex-shrink-0">{w.label}</span>
                    <div className="flex-1 h-1.5 rounded-full bg-border/50 overflow-hidden">
                      <div
                        className={`h-full rounded-full ${
                          (w.remaining_percent ?? 100) <= 10
                            ? "bg-danger"
                            : (w.remaining_percent ?? 100) <= 30
                              ? "bg-warn"
                              : "bg-ok"
                        }`}
                        style={{ width: `${Math.max(0, Math.min(100, w.remaining_percent ?? 0))}%` }}
                      />
                    </div>
                    <span className="w-20 text-right text-[11px] font-medium text-foreground flex-shrink-0">
                      {quotaText(w)}
                    </span>
                    <span className="w-24 text-right text-[10px] text-muted flex-shrink-0">
                      {t("subscription.resetAt")} {formatReset(w.reset_at)}
                    </span>
                  </div>
                ))}
              </div>
            )}
            {pct !== null && pct <= 10 && (
              <p className="mt-1.5 text-[11px] text-danger">{t("subscription.lowQuota")}</p>
            )}
          </div>
        );
      })}
    </div>
  );
}

/** 日历日程组件的实时详情（未来 14 天日程/标注 + 订阅源同步状态） */
function CalendarDetailView({ mod }: { mod: DesktopModuleInfo }) {
  const { t } = useTranslation("ai_desktop");
  const upcoming = mod.detail.upcoming ?? [];
  const subscriptions = mod.detail.subscriptions ?? [];

  const dayLabel = (iso: string): string => {
    const local = (d: Date) =>
      `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    const today = new Date();
    if (iso === local(today)) return t("calendar.today");
    if (iso === local(new Date(today.getTime() + 86400000))) return t("calendar.tomorrow");
    return iso.slice(5);
  };

  return (
    <div className="space-y-2">
      {upcoming.length === 0 && subscriptions.length === 0 && (
        <p className="text-xs text-muted py-1">{t("calendar.empty")}</p>
      )}
      {upcoming.length > 0 && (
        <div className="space-y-1">
          {upcoming.map((e) => (
            <div
              key={e.id}
              className="flex items-center gap-2 rounded-md bg-elevated border border-border/60 px-3 py-1.5"
            >
              <span className="w-14 text-[11px] text-muted flex-shrink-0">
                {dayLabel(e.date)}
              </span>
              <span className="w-24 text-[11px] font-mono text-muted flex-shrink-0">
                {e.time ? `${e.time}${e.end_time ? `-${e.end_time}` : ""}` : t("calendar.allDay")}
              </span>
              <span className="text-[13px] text-foreground truncate">
                {e.title}
                {e.kind === "note" && e.note ? `｜${e.note}` : ""}
              </span>
              {e.remind_minutes != null && e.remind_minutes > 0 && (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-accent/10 text-accent flex-shrink-0">
                  {t("calendar.remind", { minutes: e.remind_minutes })}
                </span>
              )}
              {e.source?.startsWith("ics:") && (
                <span className="text-[10px] text-muted flex-shrink-0">
                  [{e.source.slice(4)}]
                </span>
              )}
            </div>
          ))}
        </div>
      )}
      {subscriptions.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 pt-1">
          <CalendarDays className="h-3.5 w-3.5 text-muted" />
          {subscriptions.map((s) => (
            <span
              key={s.name}
              className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-[11px] ${
                !s.enabled
                  ? "border-border bg-elevated text-muted opacity-60"
                  : s.ok
                    ? "border-ok/40 bg-ok-subtle text-ok"
                    : s.error
                      ? "border-danger/40 bg-danger-subtle text-danger"
                      : "border-border bg-elevated text-muted"
              }`}
              title={s.error || undefined}
            >
              {s.name}
              {s.ok && s.count != null && ` ${t("calendar.subCount", { count: s.count })}`}
              {s.error && ` ${t("calendar.subFailed")}`}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export function ModuleDetailView({ mod }: { mod: DesktopModuleInfo }) {
  const { t } = useTranslation("ai_desktop");
  if (!mod.enabled) {
    return <p className="text-xs text-muted py-1">{t("detail.disabled")}</p>;
  }
  if (mod.key === "datetime") return <DatetimeDetailView mod={mod} />;
  if (mod.key === "subscription") return <SubscriptionDetailView mod={mod} />;
  if (mod.key === "calendar") return <CalendarDetailView mod={mod} />;
  if (mod.key === "weather") return null;
  if (mod.current_content) {
    return (
      <pre className="whitespace-pre-wrap break-all rounded-md bg-elevated border border-border/60 px-3 py-2 text-xs text-foreground">
        {mod.current_content}
      </pre>
    );
  }
  return <p className="text-xs text-muted py-1">{t("detail.noContent")}</p>;
}
