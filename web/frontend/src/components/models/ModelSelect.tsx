import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Brain, Check, ChevronsUp, ChevronsUpDown, Eye, Pin, Server, Wrench } from "lucide-react";
import { modelsApi } from "@/lib/api";
import type { ModelPriorityItem } from "@/lib/types";
import { cn } from "@/lib/utils";

/** 模型优先级数据（全类型） */
export function usePriorities() {
  return useQuery<Record<string, ModelPriorityItem[]>>({
    queryKey: ["priorities"],
    queryFn: () => modelsApi.priorities().then((r) => r.data),
  });
}

/**
 * 模型置顶：chat 类型 = 设为全局默认（后端置顶语义）；
 * 其他类型 = 移到该类型优先级列表首位。
 */
export function useModelPin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ modelType, modelId }: { modelType: string; modelId: string }) => {
      if (modelType === "chat") {
        await modelsApi.setDefault(modelId);
        return;
      }
      const priorities = qc.getQueryData<Record<string, ModelPriorityItem[]>>(["priorities"]);
      const items = priorities?.[modelType] ?? [];
      const rest = items.filter((i) => i.id !== modelId).map((i) => i.id);
      await modelsApi.setPriority(modelType, [modelId, ...rest]);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["priorities"] }),
  });
}

/** 模型能力小图标 */
function CapabilityIcons({ item }: { item: ModelPriorityItem }) {
  return (
    <span className="inline-flex items-center gap-1 text-muted shrink-0">
      {item.supports_vision && <Eye size={11} className="text-accent2" />}
      {item.supports_tools && <Wrench size={11} className="text-accent" />}
      {item.supports_reasoning && <Brain size={11} className="text-[rgb(168,85,247)]" />}
    </span>
  );
}

export interface ModelSelectProps {
  /** 模型类型（chat / vision / embedding / asr / tts ...） */
  modelType?: string;
  /** 受控选中值；空串 = 跟随全局默认（需 allowEmpty） */
  value?: string;
  /** 选择回调；不传则选择即置顶（切换默认） */
  onChange?: (modelId: string) => void;
  /** 是否提供「跟随全局默认」空选项 */
  allowEmpty?: boolean;
  /** 是否显示置顶按钮 */
  allowPin?: boolean;
  /** 紧凑模式（页头内嵌） */
  compact?: boolean;
  /** 无选中时的占位文案 */
  placeholder?: string;
  /** value 为空时是否回显全局默认模型（默认 true；表单场景传 false 以显示占位文案） */
  showDefaultWhenEmpty?: boolean;
  className?: string;
  disabled?: boolean;
}

/** 统一模型选择器：列表 + 能力标识 + 置顶 */
export function ModelSelect({
  modelType = "chat",
  value,
  onChange,
  allowEmpty = false,
  allowPin = true,
  compact = false,
  placeholder,
  showDefaultWhenEmpty = true,
  className,
  disabled = false,
}: ModelSelectProps) {
  const { t } = useTranslation("models");
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const { data: priorities = {} } = usePriorities();
  const pinMut = useModelPin();

  const items = priorities[modelType] ?? [];
  const defaultItem = items.find((i) => i.is_default) ?? items[0];
  const selected = value ? items.find((i) => i.id === value) : undefined;
  // 未显式选择时：表单场景显示占位文案，否则回显全局默认
  const display = selected ?? (showDefaultWhenEmpty && !allowEmpty ? defaultItem : undefined);
  const isTop = (item: ModelPriorityItem, idx: number) =>
    modelType === "chat" ? !!item.is_default : idx === 0;

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const handleSelect = (id: string) => {
    setOpen(false);
    if (onChange) onChange(id);
    else if (id) pinMut.mutate({ modelType, modelId: id });
  };

  return (
    <div ref={rootRef} className={cn("relative", className)}>
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "flex items-center gap-1.5 rounded-md border border-border bg-elevated text-sm transition-colors",
          "hover:border-border-strong hover:bg-hover disabled:opacity-50 disabled:cursor-not-allowed",
          compact ? "h-8 px-2.5 text-xs max-w-44" : "h-9 px-3 w-full",
        )}
      >
        <span className={cn("truncate", display ? "text-foreground" : "text-muted")}>
          {display ? display.id : allowEmpty ? t("followDefault") : (placeholder ?? t("selectModel"))}
        </span>
        {display && <CapabilityIcons item={display} />}
        <ChevronsUpDown size={13} className="text-muted shrink-0 ml-auto" />
      </button>

      {open && (
        <div
          className={cn(
            "absolute z-50 mt-1 min-w-64 max-w-[calc(100vw-2rem)] rounded-md border border-border bg-card shadow-lg animate-fade-in",
            "right-0 sm:left-0 sm:right-auto",
          )}
        >
          <div className="max-h-72 overflow-y-auto p-1">
            {allowEmpty && (
              <button
                type="button"
                onClick={() => handleSelect("")}
                className="flex w-full items-center gap-2 rounded-sm px-2.5 py-2 text-left text-sm text-muted hover:bg-hover"
              >
                <Check size={14} className={cn("shrink-0", !value ? "text-accent" : "opacity-0")} />
                {t("followDefault")}
              </button>
            )}
            {items.map((item, idx) => {
              const active = display?.id === item.id && (!allowEmpty || !!value || display === selected);
              return (
                <div
                  key={item.id}
                  className={cn(
                    "flex items-center gap-2 rounded-sm px-2.5 py-2 hover:bg-hover group",
                    active && "bg-accent-subtle",
                  )}
                >
                  <button
                    type="button"
                    onClick={() => handleSelect(item.id)}
                    className="flex min-w-0 flex-1 items-center gap-2 text-left"
                  >
                    <Check size={14} className={cn("shrink-0", active ? "text-accent" : "opacity-0")} />
                    <span className="min-w-0">
                      <span className="flex items-center gap-1.5">
                        <span className="truncate text-sm text-foreground">{item.id}</span>
                        <CapabilityIcons item={item} />
                        {isTop(item, idx) && (
                          <Pin size={11} className="text-warn fill-warn shrink-0" />
                        )}
                      </span>
                      {item.provider_name && (
                        <span className="flex items-center gap-1 text-[10px] text-muted">
                          <Server size={9} /> {item.provider_name}
                        </span>
                      )}
                    </span>
                  </button>
                  {allowPin && !isTop(item, idx) && (
                    <button
                      type="button"
                      title={t("pinToTop")}
                      disabled={pinMut.isPending}
                      onClick={() => pinMut.mutate({ modelType, modelId: item.id })}
                      className="shrink-0 rounded-sm p-1 text-muted transition-colors hover:text-warn disabled:opacity-40"
                    >
                      <ChevronsUp size={14} />
                    </button>
                  )}
                </div>
              );
            })}
            {items.length === 0 && (
              <p className="px-2.5 py-4 text-center text-xs text-muted">{t("noModelsOfType")}</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
