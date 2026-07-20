import { useTranslation } from "react-i18next";
import { ChevronDown, Package, Pencil, ToggleLeft, ToggleRight, Wrench } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ToolGroup, ToolItem } from "./types";

/** 工具分组卡片：手风琴 + 工具行 */
export function ToolGroupCard({
  group,
  isOpen,
  onToggle,
  onToggleGroup,
  onToggleTool,
  onEditTool,
}: {
  group: ToolGroup;
  isOpen: boolean;
  onToggle: () => void;
  onToggleGroup: () => void;
  onToggleTool: (name: string) => void;
  onEditTool: (tool: ToolItem) => void;
}) {
  const { t } = useTranslation("tools");

  return (
    <div
      className={cn(
        "rounded-md border transition-all bg-card",
        isOpen
          ? "border-accent shadow-[0_0_0_1px_var(--ring)]"
          : "border-border hover:border-border-strong",
      )}
    >
      {/* 分组头 */}
      <div
        className="flex items-center justify-between px-4 py-3 cursor-pointer select-none"
        onClick={onToggle}
      >
        <div className="flex items-center gap-3 min-w-0">
          <ChevronDown
            size={16}
            className={cn("text-muted transition-transform flex-shrink-0", isOpen && "rotate-180")}
          />
          <Package size={16} className="text-accent flex-shrink-0" />
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold text-heading">
                {t(`groups.${group.group}`, { defaultValue: group.group })}
              </span>
              <span className="text-[11px] px-1.5 py-0.5 rounded bg-secondary text-muted">
                {group.enabled_count}/{group.total_count}
              </span>
            </div>
            {group.description && (
              <p className="text-[11px] text-muted truncate mt-0.5">{group.description}</p>
            )}
          </div>
        </div>

        <div className="flex items-center gap-2 flex-shrink-0" onClick={(e) => e.stopPropagation()}>
          <button
            onClick={onToggleGroup}
            className={cn(
              "relative inline-flex h-5 w-9 items-center rounded-full transition-colors",
              group.any_enabled
                ? group.all_enabled
                  ? "bg-accent"
                  : "bg-accent opacity-60"
                : "bg-secondary border border-border",
            )}
            title={group.all_enabled ? t("disableGroup") : t("enableGroup")}
          >
            <span
              className={cn(
                "inline-block h-3.5 w-3.5 rounded-full bg-white shadow-sm transition-transform",
                group.any_enabled ? "translate-x-[18px]" : "translate-x-[3px]",
              )}
            />
          </button>
        </div>
      </div>

      {/* 工具列表 */}
      {isOpen && (
        <div className="border-t border-border">
          {group.tools.map((tool, idx) => (
            <div
              key={tool.name}
              className={cn(
                "flex items-center justify-between px-4 py-2.5 hover:bg-hover transition-colors",
                idx < group.tools.length - 1 && "border-b border-border",
              )}
            >
              <div className="flex items-center gap-3 min-w-0 mr-3 flex-1">
                <Wrench
                  size={13}
                  className={cn("flex-shrink-0", tool.enabled ? "text-ok" : "text-muted")}
                />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm font-medium text-heading font-mono">{tool.name}</span>
                    {tool.tags.map((tag) => (
                      <span
                        key={tag}
                        className="text-[10px] px-1.5 py-0.5 rounded-full bg-accent-subtle text-accent border border-accent/30"
                      >
                        {tag}
                      </span>
                    ))}
                  </div>
                  {tool.description && (
                    <p className="text-[11px] text-muted truncate mt-0.5">{tool.description}</p>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-2 flex-shrink-0">
                <button
                  onClick={() => onEditTool(tool)}
                  className="text-muted hover:text-foreground transition-colors p-1"
                  title={t("editProperties")}
                >
                  <Pencil size={14} />
                </button>
                <button
                  onClick={() => onToggleTool(tool.name)}
                  className={cn("transition-colors", tool.enabled ? "text-ok" : "text-muted")}
                  title={tool.enabled ? t("disable") : t("enable")}
                >
                  {tool.enabled ? <ToggleRight size={22} /> : <ToggleLeft size={22} />}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
