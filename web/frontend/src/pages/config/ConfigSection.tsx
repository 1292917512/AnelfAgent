import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { ConfigMetaItem } from "@/lib/api";
import { ConfigItemRow } from "./ConfigItemRow";

interface ConfigSectionProps {
  items: ConfigMetaItem[];
  /** 需要强制展开高级区并高亮的 key（深链/搜索定位） */
  focusKey?: string | null;
  /** 高级区默认展开（搜索结果场景） */
  expandAdvanced?: boolean;
  /** 行渲染插槽：复合行（如对话窗口）可替换默认渲染 */
  renderRow?: (item: ConfigMetaItem, defaultRow: React.ReactNode) => React.ReactNode;
  onOpenDetail: (item: ConfigMetaItem) => void;
}

/** 配置分组内容区：基础项直接列出，高级项折叠展示。 */
export function ConfigSection({ items, focusKey, expandAdvanced, renderRow, onOpenDetail }: ConfigSectionProps) {
  const { t } = useTranslation("config");
  const basic = useMemo(() => items.filter((i) => !i.advanced), [items]);
  const advanced = useMemo(() => items.filter((i) => i.advanced), [items]);
  const focusInAdvanced = !!focusKey && advanced.some((i) => i.key === focusKey);
  const [expanded, setExpanded] = useState(expandAdvanced || focusInAdvanced);
  const showAdvanced = expanded || focusInAdvanced;

  const renderItem = (item: ConfigMetaItem) => {
    const row = (
      <ConfigItemRow
        item={item}
        highlight={item.key === focusKey}
        onOpenDetail={onOpenDetail}
      />
    );
    return <div key={item.key}>{renderRow ? renderRow(item, row) : row}</div>;
  };

  return (
    <div className="grid gap-2.5">
      {basic.map(renderItem)}

      {advanced.length > 0 && (
        <div className="rounded-md border border-border bg-card">
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="flex w-full items-center gap-1.5 px-3 py-2.5 text-sm text-muted hover:text-foreground transition-colors"
          >
            {showAdvanced ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
            {t("advancedToggle", { count: advanced.length })}
          </button>
          {showAdvanced && (
            <div className="grid gap-2.5 px-3 pb-3">
              {advanced.map(renderItem)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
