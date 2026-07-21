import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";

export interface CalendarProps {
  /** 当前选中日期（YYYY-MM-DD） */
  selected: string;
  onSelect: (date: string) => void;
  /** 需要标记圆点的日期集合（YYYY-MM-DD） */
  markedDates?: ReadonlySet<string>;
}

/** 将 Date 格式化为本地 YYYY-MM-DD */
export function formatDateKey(d: Date): string {
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

/** 通用月历：月份切换 / 今天高亮 / 选中态 / 圆点标记，文案随 i18n 语言本地化 */
export function Calendar({ selected, onSelect, markedDates }: CalendarProps) {
  const { i18n } = useTranslation();
  const [viewMonth, setViewMonth] = useState(() => {
    const d = selected ? new Date(`${selected}T00:00:00`) : new Date();
    return new Date(d.getFullYear(), d.getMonth(), 1);
  });

  const locale = i18n.language;
  const todayKey = formatDateKey(new Date());

  const { weeks, monthLabel, weekdays } = useMemo(() => {
    const year = viewMonth.getFullYear();
    const month = viewMonth.getMonth();
    const first = new Date(year, month, 1);
    const daysInMonth = new Date(year, month + 1, 0).getDate();
    // 周一为一周起始
    const leading = (first.getDay() + 6) % 7;
    const cells: (string | null)[] = [];
    for (let i = 0; i < leading; i++) cells.push(null);
    for (let d = 1; d <= daysInMonth; d++) cells.push(formatDateKey(new Date(year, month, d)));
    const rows: (string | null)[][] = [];
    for (let i = 0; i < cells.length; i += 7) rows.push(cells.slice(i, i + 7));

    const label = new Intl.DateTimeFormat(locale, { year: "numeric", month: "long" }).format(viewMonth);
    // 以 2024-01-01（周一）为基准生成周一~周日的星期名
    const wd = Array.from({ length: 7 }, (_, i) =>
      new Intl.DateTimeFormat(locale, { weekday: "short" }).format(new Date(2024, 0, 1 + i)),
    );
    return { weeks: rows, monthLabel: label, weekdays: wd };
  }, [viewMonth, locale]);

  const shiftMonth = (delta: number) =>
    setViewMonth((m) => new Date(m.getFullYear(), m.getMonth() + delta, 1));

  return (
    <div className="select-none">
      <div className="flex items-center justify-between mb-2">
        <button
          type="button"
          onClick={() => shiftMonth(-1)}
          className="p-1.5 rounded-md text-muted hover:text-foreground hover:bg-hover transition-colors"
        >
          <ChevronLeft size={16} />
        </button>
        <span className="text-sm font-medium text-heading">{monthLabel}</span>
        <button
          type="button"
          onClick={() => shiftMonth(1)}
          className="p-1.5 rounded-md text-muted hover:text-foreground hover:bg-hover transition-colors"
        >
          <ChevronRight size={16} />
        </button>
      </div>
      <div className="grid grid-cols-7 gap-0.5 text-center">
        {weekdays.map((w, i) => (
          <div key={i} className="py-1 text-[11px] text-muted">{w}</div>
        ))}
        {weeks.flat().map((key, i) =>
          key === null ? (
            <div key={`e${i}`} />
          ) : (
            <button
              key={key}
              type="button"
              onClick={() => onSelect(key)}
              className={cn(
                "relative py-1.5 text-xs rounded-md transition-colors",
                key === selected
                  ? "bg-accent text-primary-foreground font-medium"
                  : key === todayKey
                    ? "text-accent font-medium hover:bg-accent-subtle"
                    : "text-foreground hover:bg-hover",
              )}
            >
              {Number(key.slice(8))}
              {markedDates?.has(key) && (
                <span
                  className={cn(
                    "absolute bottom-0.5 left-1/2 -translate-x-1/2 w-1 h-1 rounded-full",
                    key === selected ? "bg-primary-foreground" : "bg-accent",
                  )}
                />
              )}
            </button>
          ),
        )}
      </div>
    </div>
  );
}
