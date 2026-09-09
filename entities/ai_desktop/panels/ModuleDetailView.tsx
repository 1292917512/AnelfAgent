import { useTranslation } from "react-i18next";
import { CalendarClock, Clock3, Globe2, PartyPopper } from "lucide-react";
import type { DesktopModuleInfo } from "./types";

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

/**
 * 组件当前状态详情（按组件 key 特化；weather 的地区状态由
 * WeatherLocations 管理器呈现，此处不重复渲染）。
 */
export function ModuleDetailView({ mod }: { mod: DesktopModuleInfo }) {
  const { t } = useTranslation("ai_desktop");
  if (!mod.enabled) {
    return <p className="text-xs text-muted py-1">{t("detail.disabled")}</p>;
  }
  if (mod.key === "datetime") return <DatetimeDetailView mod={mod} />;
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
