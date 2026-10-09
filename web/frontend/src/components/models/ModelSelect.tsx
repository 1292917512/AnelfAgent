import { useState } from "react";
import * as Popover from "@radix-ui/react-popover";
import { Command } from "cmdk";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Brain, Check, ChevronsUpDown, Eye, Pin, Search, TriangleAlert, Wrench } from "lucide-react";
import { modelsApi } from "@/lib/api";
import type { ModelPriorityItem } from "@/lib/types";
import { cn } from "@/lib/utils";
import { QueryError } from "@/components/common/AsyncState";
import { Button } from "@/components/ui/Button";

const priorityOptions = {
  queryKey: ["priorities"],
  queryFn: () => modelsApi.priorities().then((response) => response.data),
};

export function usePriorities() {
  return useQuery({ ...priorityOptions, throwOnError: false });
}

export function useModelPin() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({ modelType, modelId }: { modelType: string; modelId: string }) => {
      if (modelType === "chat") { await modelsApi.setDefault(modelId); return; }
      const priorities = await client.fetchQuery(priorityOptions);
      const rest = (priorities[modelType] ?? []).filter((item) => item.id !== modelId).map((item) => item.id);
      await modelsApi.setPriority(modelType, [modelId, ...rest]);
    },
    onSuccess: () => client.invalidateQueries({ queryKey: ["priorities"] }),
  });
}

function CapabilityIcons({ item }: { item: ModelPriorityItem }) {
  const { t } = useTranslation("models");
  return <span className="inline-flex shrink-0 items-center gap-1 text-muted">
    {item.supports_vision && <span title={t("capVision")}><Eye size={12} /></span>}
    {item.supports_tools && <span title={t("capTools")}><Wrench size={12} /></span>}
    {item.supports_reasoning && <span title={t("capReasoning")}><Brain size={12} /></span>}
  </span>;
}

export interface ModelSelectProps {
  modelType?: string;
  value?: string;
  onChange?: (modelId: string) => void;
  allowEmpty?: boolean;
  allowPin?: boolean;
  compact?: boolean;
  placeholder?: string;
  showDefaultWhenEmpty?: boolean;
  className?: string;
  disabled?: boolean;
  id?: string;
  label?: string;
}

const optionValue = (id: string) => JSON.stringify(["model", id]);
const DEFAULT_OPTION = JSON.stringify(["default"]);

/** Searchable model picker with explicit unavailable values and a separate default-model action. */
export function ModelSelect({
  modelType = "chat", value, onChange, allowEmpty = false, allowPin = true, compact = false,
  placeholder, showDefaultWhenEmpty = true, className, disabled = false, id, label,
}: ModelSelectProps) {
  const { t } = useTranslation("models");
  const { t: tc } = useTranslation("common");
  const [open, setOpen] = useState(false);
  const [highlighted, setHighlighted] = useState("");
  const query = usePriorities();
  const pin = useModelPin();
  const items = (query.data?.[modelType] ?? []).filter((item) => item.enabled !== false);
  const defaultItem = items.find((item) => item.is_default) ?? items[0];
  const selected = value ? items.find((item) => item.id === value) : !allowEmpty && showDefaultWhenEmpty ? defaultItem : undefined;
  const unavailable = !!value && !!query.data && !items.some((item) => item.id === value);
  const display = value || selected?.id || (allowEmpty ? t("followDefault") : placeholder ?? t("selectModel"));
  const focused = items.find((item) => optionValue(item.id) === highlighted);
  const focusedDefault = focused && (modelType === "chat" ? focused.is_default : focused.id === items[0]?.id);
  const select = (modelId: string) => {
    if (onChange) { onChange(modelId); setOpen(false); }
    else if (modelId) pin.mutate({ modelType, modelId }, { onSuccess: () => setOpen(false) });
  };

  return <Popover.Root open={open && !disabled} onOpenChange={(next) => {
    setOpen(next);
    if (next) setHighlighted(value ? optionValue(value) : allowEmpty ? DEFAULT_OPTION : selected ? optionValue(selected.id) : "");
  }}>
    <Popover.Trigger asChild>
      <button type="button" id={id} disabled={disabled || pin.isPending} aria-label={label ?? t("selectModel")}
        className={cn("flex min-w-0 items-center gap-2 rounded-lg border bg-elevated text-sm transition-colors hover:bg-hover disabled:cursor-not-allowed disabled:opacity-50",
          unavailable ? "border-warn/60" : "border-border hover:border-border-strong",
          compact ? "h-8 max-w-52 px-2.5 text-xs" : "h-9 w-full px-3", className)}>
        <span className="truncate" title={display}>{display}</span>
        {unavailable ? <TriangleAlert size={13} className="shrink-0 text-warn" aria-label={t("unavailableModel")} />
          : selected && <CapabilityIcons item={selected} />}
        <ChevronsUpDown size={13} className="ml-auto shrink-0 text-muted" />
      </button>
    </Popover.Trigger>
    <Popover.Portal>
      <Popover.Content aria-label={label ?? t("selectModel")} align="start" sideOffset={6} collisionPadding={12}
        onKeyDown={(event) => { if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); setOpen(false); } }}
        className="z-[120] flex max-h-[var(--radix-popover-content-available-height)] w-[360px] max-w-[calc(100vw-24px)] flex-col overflow-hidden rounded-xl border border-border bg-card text-foreground shadow-lg outline-none">
        <Command label={t("searchModels")} value={highlighted} onValueChange={setHighlighted} loop className="flex min-h-0 flex-col">
          <div className="flex shrink-0 items-center gap-2 border-b border-border px-3">
            <Search size={15} className="shrink-0 text-muted" />
            <Command.Input aria-label={t("searchModels")} placeholder={t("searchModels")}
              className="h-11 min-w-0 flex-1 bg-transparent text-sm outline-none" />
          </div>
          {unavailable && <p className="px-3 py-2 text-xs text-warn">{t("unavailableHint", { model: value })}</p>}
          {query.error && <div className="p-2"><QueryError compact error={query.error} retry={() => void query.refetch()} /></div>}
          {query.isPending && <p role="status" className="p-4 text-sm text-muted">{tc("loading")}</p>}
          <Command.List aria-label={t("selectModel")} className="max-h-72 min-h-0 overflow-y-auto overscroll-contain p-1.5">
            {!query.isPending && !query.error && <Command.Empty className="px-3 py-6 text-center text-xs text-muted">{t("noMatchingModels")}</Command.Empty>}
            {allowEmpty && <Command.Item value={DEFAULT_OPTION} keywords={[t("followDefault")]} onSelect={() => select("")}
              className="flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-2 text-sm data-[selected=true]:bg-hover">
              <Check size={14} className={!value ? "text-accent" : "opacity-0"} />{t("followDefault")}
            </Command.Item>}
            {items.map((item, index) => <Command.Item key={item.id} value={optionValue(item.id)}
              disabled={pin.isPending} keywords={[item.id, item.model, item.provider_name]}
              onSelect={() => select(item.id)}
              className="flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-2.5 data-[selected=true]:bg-accent-subtle data-[disabled=true]:opacity-50">
              <Check size={14} className={selected?.id === item.id ? "shrink-0 text-accent" : "shrink-0 opacity-0"} />
              <span className="min-w-0 flex-1">
                <span className="block break-words text-sm font-medium">{item.id}</span>
                <span className="mt-0.5 block truncate text-[11px] text-muted">{item.provider_name}</span>
              </span>
              <CapabilityIcons item={item} />
              {(modelType === "chat" ? item.is_default : index === 0) && <Pin size={12} className="shrink-0 text-warn" aria-label={t("defaultModel")} />}
            </Command.Item>)}
          </Command.List>
        </Command>
        {allowPin && focused && <div className="shrink-0 border-t border-border p-2">
          <Button className="w-full" size="sm" loading={pin.isPending} disabled={!!focusedDefault}
            title={focused.id} onClick={() => pin.mutate({ modelType, modelId: focused.id })}>
            <Pin size={13} /><span className="truncate">{t(focusedDefault ? "alreadyDefault" : "pinSelected", { model: focused.id })}</span>
          </Button>
        </div>}
      </Popover.Content>
    </Popover.Portal>
  </Popover.Root>;
}
