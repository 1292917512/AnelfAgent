import { useState, useCallback } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { modelsApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import {
  Star, Eye, Wrench, Server, Brain, ChevronsUp, GripVertical, Layers,
} from "lucide-react";
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";

const TYPE_ORDER = ["chat", "vision", "embedding", "asr", "tts", "video", "rerank", "image_gen", "image_edit"];

interface PriorityItem {
  id: string; model: string; provider_id: string; provider_name: string;
  is_default: boolean; supports_vision: boolean; supports_tools: boolean;
  supports_reasoning: boolean; api_type: string;
  input_cost: number | null; output_cost: number | null;
  context_window: number | null;
}

function SortableItem({
  item, index, activeType, onSetDefault,
}: {
  item: PriorityItem;
  index: number;
  activeType: string;
  onSetDefault: (id: string) => void;
}) {
  const { t } = useTranslation(["models"]);
  const {
    attributes, listeners, setNodeRef, transform, transition, isDragging,
  } = useSortable({ id: item.id });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    zIndex: isDragging ? 10 : undefined,
    opacity: isDragging ? 0.85 : 1,
  };

  return (
    <div ref={setNodeRef} style={style}
      className={cn(
        "flex items-center justify-between p-4 rounded-[var(--radius-md)] border transition-all bg-[var(--card)]",
        item.is_default ? "border-[var(--warn)] shadow-[0_0_0_1px_var(--warn)]" : "border-[var(--border)]",
        isDragging && "shadow-lg ring-2 ring-[var(--accent)]",
      )}>
      <div className="flex items-center gap-3">
        <button {...attributes} {...listeners}
          className="p-1 cursor-grab active:cursor-grabbing text-[var(--muted)] hover:text-[var(--text)] touch-none">
          <GripVertical size={16} />
        </button>
        <span className="w-7 h-7 flex items-center justify-center rounded-full bg-[var(--secondary)] text-xs font-bold text-[var(--muted)]">
          {index + 1}
        </span>
        <div>
          <div className="flex items-center gap-2">
            {item.is_default && <Star size={14} className="text-[var(--warn)] fill-[var(--warn)]" />}
            <span className="font-medium text-[var(--text-strong)]">{item.id}</span>
          </div>
          <div className="flex items-center gap-2 mt-0.5 flex-wrap">
            <span className="text-xs text-[var(--muted)]">{item.model}</span>
            {item.provider_name && (
              <span className="inline-flex items-center gap-1 text-[10px] text-[var(--muted)]">
                <Server size={9} /> {item.provider_name}
              </span>
            )}
            {item.api_type && (
              <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-[var(--secondary)] text-[var(--muted)] border border-[var(--border)]">
                {item.api_type}
              </span>
            )}
            {item.supports_vision && (
              <span className="inline-flex items-center gap-0.5 text-[10px] px-1.5 py-0.5 rounded-full bg-[var(--accent-2-subtle)] text-[var(--accent-2)] border border-[rgba(20,184,166,0.3)]">
                <Eye size={9} /> {t("vision")}
              </span>
            )}
            {item.supports_tools && (
              <span className="inline-flex items-center gap-0.5 text-[10px] px-1.5 py-0.5 rounded-full bg-[var(--accent-subtle)] text-[var(--accent)] border border-[rgba(74,144,217,0.3)]">
                <Wrench size={9} /> {t("toolCall")}
              </span>
            )}
            {item.supports_reasoning && (
              <span className="inline-flex items-center gap-0.5 text-[10px] px-1.5 py-0.5 rounded-full bg-[rgba(168,85,247,0.1)] text-[rgb(168,85,247)] border border-[rgba(168,85,247,0.3)]">
                <Brain size={9} /> {t("deepThinking")}
              </span>
            )}
            {item.context_window != null && (
              <span title={t("contextWindowLabel")}
                className="inline-flex items-center gap-0.5 text-[10px] px-1.5 py-0.5 rounded-full bg-[rgba(234,179,8,0.1)] text-[rgb(180,140,20)] border border-[rgba(234,179,8,0.25)]">
                <Layers size={9} /> {item.context_window >= 1000 ? `${Math.round(item.context_window / 1000)}K` : item.context_window}
              </span>
            )}
            {(item.input_cost != null || item.output_cost != null) && (
              <span title={t("costPerMillion")}
                className="text-[10px] px-1.5 py-0.5 rounded-full bg-[rgba(34,197,94,0.1)] text-[rgb(22,163,74)] border border-[rgba(34,197,94,0.25)]">
                ${item.input_cost ?? "?"}/{item.output_cost ?? "?"}
              </span>
            )}
          </div>
        </div>
      </div>
      <div className="flex items-center gap-1">
        {activeType === "chat" && !item.is_default && index !== 0 && (
          <button onClick={() => onSetDefault(item.id)}
            className="p-1.5 rounded text-[var(--muted)] hover:text-[var(--warn)] transition-colors"
            title={t("setDefault")}>
            <ChevronsUp size={14} />
          </button>
        )}
      </div>
    </div>
  );
}

export function PrioritiesPanel() {
  const { t } = useTranslation(["models", "common"]);
  const qc = useQueryClient();
  const [activeType, setActiveType] = useState("chat");

  const { data: priorities = {} } = useQuery<Record<string, PriorityItem[]>>({
    queryKey: ["priorities"],
    queryFn: () => modelsApi.priorities().then(r => r.data),
  });

  const setDefaultMut = useMutation({
    mutationFn: (modelId: string) => modelsApi.setDefault(modelId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["priorities"] }),
  });

  const setPriorityMut = useMutation({
    mutationFn: ({ modelType, modelIds }: { modelType: string; modelIds: string[] }) =>
      modelsApi.setPriority(modelType, modelIds),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["priorities"] }),
  });

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const availableTypes = TYPE_ORDER.filter(tp => (priorities[tp]?.length ?? 0) > 0);
  const currentItems = priorities[activeType] ?? [];

  const handleDragEnd = useCallback((event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;

    const oldIndex = currentItems.findIndex(item => item.id === active.id);
    const newIndex = currentItems.findIndex(item => item.id === over.id);
    if (oldIndex === -1 || newIndex === -1) return;

    const reordered = arrayMove(currentItems, oldIndex, newIndex);
    qc.setQueryData<Record<string, PriorityItem[]>>(["priorities"], old => {
      if (!old) return old;
      return { ...old, [activeType]: reordered };
    });

    setPriorityMut.mutate({
      modelType: activeType,
      modelIds: reordered.map(item => item.id),
    });
  }, [currentItems, activeType, qc, setPriorityMut]);

  return (
    <div className="space-y-4">
      <p className="text-sm text-[var(--muted)]">{t("priorityDesc")}</p>

      <div className="flex flex-wrap gap-1.5">
        {availableTypes.map(mt => (
          <button key={mt} onClick={() => setActiveType(mt)}
            className={cn("px-3 py-1.5 text-sm font-medium rounded-[var(--radius-md)] border transition-all",
              activeType === mt
                ? "border-[var(--accent)] text-[var(--accent)] bg-[var(--accent-subtle)]"
                : "border-[var(--border)] text-[var(--muted)] hover:text-[var(--text)] hover:bg-[var(--bg-hover)]",
            )}>
            {t(`modelTypeLabels.${mt}`, { defaultValue: mt })}
            <span className="ml-1.5 text-xs opacity-70">({priorities[mt]?.length ?? 0})</span>
          </button>
        ))}
        {availableTypes.length === 0 && <p className="text-sm text-[var(--muted)] py-2">{t("noConfiguredModels")}</p>}
      </div>

      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
        <SortableContext items={currentItems.map(i => i.id)} strategy={verticalListSortingStrategy}>
          <div className="space-y-2">
            {currentItems.map((item, idx) => (
              <SortableItem
                key={item.id}
                item={item}
                index={idx}
                activeType={activeType}
                onSetDefault={(id) => setDefaultMut.mutate(id)}
              />
            ))}
            {currentItems.length === 0 && availableTypes.length > 0 && (
              <p className="text-sm text-[var(--muted)] py-8 text-center">{t("noModelsOfType")}</p>
            )}
          </div>
        </SortableContext>
      </DndContext>

      {currentItems.length > 0 && (
        <p className="text-xs text-[var(--muted)]">
          {t("priorityNote", { type: t(`modelTypeLabels.${activeType}`, { defaultValue: activeType }) })}
        </p>
      )}
    </div>
  );
}
