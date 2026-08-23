import { useTranslation } from "react-i18next";
import type { ConfigMetaItem } from "@/lib/api";
import { Check, Loader2, RotateCcw } from "lucide-react";
import { ModelSelect } from "@/components/models/ModelSelect";
import { ReasoningEffortOptions } from "@/components/common/ReasoningEffortSelect";
import { useConfigSave } from "./useConfigSave";

interface SummaryModelRowProps {
  /** conversation_summary_model（摘要专用模型 ID） */
  modelItem: ConfigMetaItem;
  /** conversation_summary_reasoning_effort（摘要思考档） */
  effortItem: ConfigMetaItem;
}

/** 摘要模型一行配置：折叠/压缩摘要专用模型 + 思考档位（空 = 跟随默认/模型）。 */
export function SummaryModelRow({ modelItem, effortItem }: SummaryModelRowProps) {
  const { t } = useTranslation(["config", "models"]);
  const modelSave = useConfigSave(modelItem.key);
  const effortSave = useConfigSave(effortItem.key);

  const model = String(modelItem.value ?? modelItem.default ?? "");
  const effort = String(effortItem.value ?? effortItem.default ?? "");
  const isDefault = !model && !effort;
  const saving = modelSave.saving || effortSave.saving;
  const saved = modelSave.saved || effortSave.saved;

  const reset = () => {
    modelSave.save(String(modelItem.default ?? ""));
    effortSave.save(String(effortItem.default ?? ""));
  };

  return (
    <div className="p-3 rounded-md border border-border bg-card space-y-2.5">
      <div className="flex items-center justify-between gap-3">
        <div className="flex-1 min-w-0">
          <div className="text-sm text-heading">{t("summaryModel.title")}</div>
          <div className="text-xs text-muted">{t("summaryModel.desc")}</div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {saving && <Loader2 size={14} className="animate-spin text-muted" />}
          {saved && <Check size={15} className="text-ok" />}
          {!isDefault && (
            <button
              type="button"
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
        {/* 专用模型：空 = 跟随默认主模型（失败仍走回退链） */}
        <label className="flex items-center gap-1.5 text-xs text-muted flex-1 min-w-48">
          {t("summaryModel.model")}
          <ModelSelect
            modelType="chat"
            allowEmpty
            allowPin={false}
            showDefaultWhenEmpty={false}
            value={model}
            onChange={(id) => modelSave.save(id)}
            className="flex-1"
          />
        </label>
        {/* 思考档位：空 = 跟随模型自身配置 */}
        <label className="flex items-center gap-1.5 text-xs text-muted">
          {t("summaryModel.effort")}
          <select
            value={effort}
            onChange={(e) => effortSave.save(e.target.value)}
            className="bg-bg border border-input rounded-md px-2.5 py-1.5 text-sm text-foreground outline-none focus:border-ring"
          >
            <option value="">{t("summaryModel.followModel")}</option>
            <ReasoningEffortOptions t={t} />
          </select>
        </label>
      </div>
    </div>
  );
}
