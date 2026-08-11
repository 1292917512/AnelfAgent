import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ConfigMetaItem } from "@/lib/api";
import { Check, Loader2, RotateCcw } from "lucide-react";
import { useConfigSave } from "./useConfigSave";
import { RangeField } from "./fields";

interface ConversationWindowRowProps {
  /** max_conversation_size（总窗口条数 M） */
  sizeItem: ConfigMetaItem;
  /** conversation_raw_keep_percent（折后保留百分比） */
  percentItem: ConfigMetaItem;
}

/** 对话窗口一行配置：总条数 + 保留比例滑条 + 折叠段可视化比例条。 */
export function ConversationWindowRow({ sizeItem, percentItem }: ConversationWindowRowProps) {
  const { t } = useTranslation("config");
  const [size, setSize] = useState(Number(sizeItem.value ?? sizeItem.default) || 30);
  const sizeSave = useConfigSave(sizeItem.key);
  const pctSave = useConfigSave(percentItem.key);
  useEffect(() => setSize(Number(sizeItem.value ?? sizeItem.default) || 30), [sizeItem]);

  const pct = Number(percentItem.value ?? percentItem.default) || 33;
  const keep = Math.max(1, Math.min(size - 1, Math.round((size * pct) / 100)));
  const total = size + keep; // 触发折叠时的窗口（M+x）
  const keepPct = (keep / total) * 100;
  const isDefault =
    size === Number(sizeItem.default) && pct === Number(percentItem.default);
  const saving = sizeSave.saving || pctSave.saving;
  const saved = sizeSave.saved || pctSave.saved;

  const saveSize = (v: number) => {
    setSize(v);
    if (v >= 2) sizeSave.save(v);
  };
  const reset = () => {
    saveSize(Number(sizeItem.default));
    pctSave.save(Number(percentItem.default));
  };

  return (
    <div className="p-3 rounded-md border border-border bg-card space-y-2.5">
      <div className="flex items-center justify-between gap-3">
        <div className="flex-1 min-w-0">
          <div className="text-sm text-heading">{t("window.title")}</div>
          <div className="text-xs text-muted">{t("window.desc")}</div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {saving && <Loader2 size={14} className="animate-spin text-muted" />}
          {saved && <Check size={15} className="text-ok" />}
          {!isDefault && (
            <button
              title={t("resetToDefault")}
              onClick={reset}
              className="p-1.5 rounded-md text-muted hover:text-foreground hover:bg-hover transition-colors"
            >
              <RotateCcw size={14} />
            </button>
          )}
        </div>
      </div>

      <div className="flex items-center gap-4 flex-wrap">
        {/* 总窗口条数 */}
        <label className="flex items-center gap-1.5 text-xs text-muted">
          {t("window.size")}
          <input
            type="number"
            min={2}
            step={1}
            value={size}
            onChange={(e) => saveSize(parseInt(e.target.value, 10) || 2)}
            className="w-20 bg-bg border border-input rounded-md px-2 py-1 text-sm text-foreground outline-none focus:border-ring"
          />
        </label>
        {/* 保留比例滑条（拖动预览、松手提交） */}
        <label className="flex items-center gap-2 flex-1 min-w-48 text-xs text-muted">
          {t("window.keepPercent")}
          <span className="flex-1">
            <RangeField
              value={pct}
              min={5}
              max={90}
              step={1}
              unit="%"
              className="w-full"
              onCommit={(v) => pctSave.save(v)}
            />
          </span>
        </label>
      </div>

      {/* 折叠段比例可视化 */}
      <div>
        <div className="flex h-2.5 rounded-full overflow-hidden bg-secondary">
          <div
            className="bg-accent transition-all"
            style={{ width: `${keepPct}%` }}
            title={t("window.keepSeg", { count: keep })}
          />
          <div
            className="bg-[var(--border-strong)] transition-all"
            style={{ width: `${100 - keepPct}%` }}
            title={t("window.foldSeg", { count: size })}
          />
        </div>
        <div className="flex justify-between mt-1 text-[11px] text-muted">
          <span>{t("window.keepSeg", { count: keep })}</span>
          <span>{t("window.foldSeg", { count: size })}</span>
          <span className="font-mono">{t("window.range", { min: keep, max: total })}</span>
        </div>
      </div>
    </div>
  );
}
