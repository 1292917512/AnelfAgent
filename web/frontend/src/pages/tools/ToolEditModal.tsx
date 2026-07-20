import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Search, Tag, X } from "lucide-react";
import { tagsApi } from "@/lib/api";
import type { UnifiedTag } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Button, Input, Modal, Textarea } from "@/components/ui";
import type { EditState } from "./types";

/** 工具属性编辑弹窗：标签选择 + 描述 */
export function ToolEditModal({
  editing,
  onChange,
  onClose,
  onSave,
  isPending,
}: {
  editing: EditState;
  onChange: (state: EditState) => void;
  onClose: () => void;
  onSave: () => void;
  isPending: boolean;
}) {
  const { t } = useTranslation(["tools", "common", "tags"]);
  const [pickerSearch, setPickerSearch] = useState("");

  const { data: unifiedTags = [] } = useQuery<UnifiedTag[]>({
    queryKey: ["unified-tags"],
    queryFn: () => tagsApi.unified().then((r) => r.data),
  });

  // 可选标签：未选中且匹配搜索
  const pickerAvailable = useMemo(() => {
    const kw = pickerSearch.toLowerCase();
    return unifiedTags.filter(
      (tag) => !editing.tags.includes(tag.name) && (!kw || tag.name.includes(kw)),
    );
  }, [unifiedTags, editing.tags, pickerSearch]);

  // 工具上已存在但不在统一注册表中的标签
  const unknownSelectedTags = useMemo(() => {
    const known = new Set(unifiedTags.map((tag) => tag.name));
    return editing.tags.filter((tag) => !known.has(tag));
  }, [unifiedTags, editing.tags]);

  return (
    <Modal
      open
      onClose={onClose}
      title={
        <span className="flex items-center gap-2">
          <Tag size={18} className="text-accent" />
          {t("editToolProps")}
        </span>
      }
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onClose}>{t("common:cancel")}</Button>
          <Button variant="primary" size="sm" onClick={onSave} loading={isPending}>
            {isPending ? t("common:saving") : t("common:save")}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {/* 工具名 */}
        <div>
          <label className="block text-xs font-medium text-muted mb-1">{t("toolName")}</label>
          <div className="text-sm font-mono text-heading px-3 py-2 rounded-md bg-secondary">
            {editing.name}
          </div>
        </div>

        {/* 标签选择 */}
        <div>
          <label className="block text-xs font-medium text-muted mb-1">{t("tagsLabel")}</label>

          <div className="min-h-[36px] p-2 rounded-t-md border border-b-0 border-border bg-secondary flex flex-wrap gap-1.5">
            {editing.tags.length === 0 ? (
              <span className="text-[11px] text-muted self-center px-1">
                {t("tags:selectedTags")}…
              </span>
            ) : (
              editing.tags.map((tag) => (
                <span
                  key={tag}
                  className={cn(
                    "inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full border",
                    unknownSelectedTags.includes(tag)
                      ? "bg-warn-subtle text-warn border-warn/30"
                      : "bg-accent-subtle text-accent border-accent/30",
                  )}
                >
                  {tag}
                  <button
                    onClick={() => onChange({ ...editing, tags: editing.tags.filter((x) => x !== tag) })}
                    className="hover:text-danger transition-colors"
                  >
                    <X size={9} />
                  </button>
                </span>
              ))
            )}
          </div>

          <div className="border border-border rounded-b-md bg-elevated overflow-hidden">
            <div className="relative border-b border-border">
              <Search size={12} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" />
              <Input
                value={pickerSearch}
                onChange={(e) => setPickerSearch(e.target.value)}
                placeholder={t("tags:filterPlaceholder")}
                className="!h-8 pl-7 !border-0 !bg-transparent text-xs !ring-0 focus:!ring-0"
              />
            </div>
            <div className="p-2 flex flex-wrap gap-1.5 max-h-32 overflow-y-auto">
              {pickerAvailable.length === 0 ? (
                <span className="text-[11px] text-muted px-1">
                  {t("tags:noAvailableTags")}
                </span>
              ) : (
                pickerAvailable.map((tag) => (
                  <button
                    key={tag.name}
                    onClick={() => !editing.tags.includes(tag.name) && onChange({ ...editing, tags: [...editing.tags, tag.name] })}
                    className="text-[11px] px-2.5 py-0.5 rounded-full border border-border
                      bg-secondary text-muted
                      hover:border-accent hover:text-accent hover:bg-accent/5
                      transition-all"
                  >
                    {tag.name}
                  </button>
                ))
              )}
            </div>
          </div>
        </div>

        {/* 描述 */}
        <div>
          <label className="block text-xs font-medium text-muted mb-1">{t("toolDescription")}</label>
          <Textarea
            value={editing.description}
            onChange={(e) => onChange({ ...editing, description: e.target.value })}
            rows={3}
            className="resize-none"
          />
        </div>
      </div>
    </Modal>
  );
}
