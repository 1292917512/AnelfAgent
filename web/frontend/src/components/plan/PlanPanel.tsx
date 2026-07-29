/**
 * PlanPanel — 对话窗口内嵌入式悬浮计划窗（可拖拽）。
 *
 * 设计要点：
 * - 不再 fixed 到视口右上角（避免遮挡右侧 Dock / 左侧文件树），
 *   而是 absolute 定位在 **中栏对话容器内**（Chat.tsx 渲染时传入相对定位父容器）。
 * - 头部为拖拽把手，可在对话窗口内任意移动；位置持久化到 localStorage。
 * - 折叠态：icon-only 徽标（仅显示 🎯 + done/total + 旋转指示）。
 * - 展开态：完整卡片（goal / 进度条 / 步骤 / files / risks / 取消按钮）。
 * - 数据来自 plan-store（按 activeChatId 过滤），与 SSE plan_* 事件实时同步。
 */
import { useEffect, useRef, useState, useCallback } from "react";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle, CheckCircle2, ChevronUp, FileText, GripVertical, Loader2, Target, X, XCircle,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { usePlanStore } from "@/stores/plan-store";
import { useChatStore } from "@/stores/chat-store";
import { PlanStepRow } from "./PlanStepRow";
import type { PlanRecord } from "@/lib/types";

const STORAGE_KEY = "anelf:planPanelPos";
const DEFAULT_POS = { x: 16, y: 16 };  // 相对对话容器左上角的偏移

interface DragPos {
  x: number;
  y: number;
}

function loadPos(): DragPos {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw) as DragPos;
  } catch { /* ignore */ }
  return DEFAULT_POS;
}

function persistPos(pos: DragPos) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(pos));
  } catch { /* ignore */ }
}

export function PlanPanel() {
  const { t } = useTranslation("plan");
  const activeChatId = useChatStore((s) => s.activeChatId);
  const activePlan = usePlanStore((s) => s.getActivePlan(activeChatId));
  const panelCollapsed = usePlanStore((s) => s.panelCollapsed);
  const panelHidden = usePlanStore((s) => s.panelHidden);
  const setPanelCollapsed = usePlanStore((s) => s.setPanelCollapsed);
  const setPanelHidden = usePlanStore((s) => s.setPanelHidden);
  const updatePlanStatus = usePlanStore((s) => s.updatePlanStatus);

  // 拖拽状态
  const [pos, setPos] = useState<DragPos>(loadPos);
  const [dragging, setDragging] = useState(false);
  const dragRef = useRef<{ startX: number; startY: number; posX: number; posY: number } | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const handleDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    setDragging(true);
    dragRef.current = {
      startX: e.clientX,
      startY: e.clientY,
      posX: pos.x,
      posY: pos.y,
    };
  }, [pos]);

  useEffect(() => {
    if (!dragging) return;
    const onMove = (e: MouseEvent) => {
      if (!dragRef.current || !containerRef.current) return;
      const dx = e.clientX - dragRef.current.startX;
      const dy = e.clientY - dragRef.current.startY;
      let newX = dragRef.current.posX + dx;
      let newY = dragRef.current.posY + dy;
      // 边界约束：限制在父容器内
      const parent = containerRef.current.offsetParent as HTMLElement | null;
      if (parent) {
        const pw = parent.clientWidth;
        const ph = parent.clientHeight;
        const w = containerRef.current.offsetWidth;
        const h = containerRef.current.offsetHeight;
        newX = Math.max(0, Math.min(newX, pw - w));
        newY = Math.max(0, Math.min(newY, ph - h));
      }
      setPos({ x: newX, y: newY });
    };
    const onUp = () => {
      setDragging(false);
      if (dragRef.current) {
        persistPos(pos);
        dragRef.current = null;
      }
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [dragging, pos]);

  // 处理 plan 不存在 / 用户主动隐藏
  if (!activePlan || panelHidden) return null;

  const total = activePlan.steps.length;
  const done = activePlan.steps.filter((s) => s.status === "completed").length;
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;

  // 折叠态：icon-only 徽标（也可拖拽）
  if (panelCollapsed) {
    return (
      <div
        ref={containerRef}
        className={cn(
          "absolute z-30 flex items-center gap-1.5 pl-1 pr-2.5 py-1.5 rounded-full shadow-lg border backdrop-blur-sm transition-shadow select-none",
          dragging ? "shadow-2xl cursor-grabbing" : "cursor-grab",
          activePlan.status === "cancelled"
            ? "bg-red-50 dark:bg-red-950/80 border-red-400/40"
            : activePlan.status === "completed"
              ? "bg-green-50 dark:bg-green-950/80 border-green-400/40"
              : "bg-accent-subtle/95 dark:bg-accent/20 border-accent/40 hover:shadow-xl",
        )}
        style={{ left: `${pos.x}px`, top: `${pos.y}px` }}
        onMouseDown={handleDragStart}
        title={activePlan.goal}
      >
        <GripVertical size={11} className="text-muted shrink-0" />
        <button
          onClick={() => setPanelCollapsed(false)}
          onMouseDown={(e) => e.stopPropagation()}
          className="flex items-center gap-1.5"
        >
          <Target size={13} className={cn(
            activePlan.status === "cancelled" ? "text-red-500"
              : activePlan.status === "completed" ? "text-green-500"
                : "text-accent",
          )} />
          <span className="text-xs font-mono font-medium">
            {done}/{total}
          </span>
          {activePlan.status === "executing" && (
            <Loader2 size={11} className="animate-spin text-accent" />
          )}
        </button>
      </div>
    );
  }

  // 展开态：完整卡片（头部可拖拽）
  return (
    <div
      ref={containerRef}
      className={cn(
        "absolute z-30 w-80 max-w-[85%] rounded-lg border shadow-xl backdrop-blur-sm",
        dragging && "shadow-2xl",
        activePlan.status === "cancelled"
          ? "bg-red-50/95 dark:bg-red-950/90 border-red-400/40"
          : activePlan.status === "completed"
            ? "bg-green-50/95 dark:bg-green-950/90 border-green-400/40"
            : "bg-card/95 dark:bg-card/95 border-accent/40",
      )}
      style={{ left: `${pos.x}px`, top: `${pos.y}px` }}
    >
      {/* 头部（拖拽把手） */}
      <div
        className={cn(
          "flex items-center gap-2 px-3 py-2 border-b border-border/50 select-none",
          dragging ? "cursor-grabbing" : "cursor-grab",
        )}
        onMouseDown={handleDragStart}
      >
        <GripVertical size={12} className="text-muted shrink-0" />
        <Target size={14} className={cn("shrink-0",
          activePlan.status === "cancelled" ? "text-red-500"
            : activePlan.status === "completed" ? "text-green-500"
              : "text-accent",
        )} />
        <span className="text-sm font-medium text-foreground flex-1 min-w-0 truncate">
          {activePlan.goal || t("panel.untitled", { defaultValue: "执行计划" })}
        </span>
        <span className="text-xs text-muted font-mono shrink-0">{done}/{total}</span>
        <button
          onClick={() => setPanelCollapsed(true)}
          onMouseDown={(e) => e.stopPropagation()}
          className="p-1 text-muted hover:text-foreground transition-colors"
          title={t("panel.collapse", { defaultValue: "折叠" })}
        >
          <ChevronUp size={13} />
        </button>
        <button
          onClick={() => setPanelHidden(true)}
          onMouseDown={(e) => e.stopPropagation()}
          className="p-1 text-muted hover:text-foreground transition-colors"
          title={t("panel.close", { defaultValue: "关闭" })}
        >
          <X size={13} />
        </button>
      </div>

      {/* 进度条 */}
      {total > 0 && (
        <div className="px-3 pt-2">
          <div className="h-1 bg-border rounded-full overflow-hidden">
            <div
              className={cn(
                "h-full transition-all duration-300",
                activePlan.status === "cancelled" ? "bg-red-400"
                  : activePlan.status === "completed" ? "bg-green-500"
                    : "bg-accent",
              )}
              style={{ width: `${pct}%` }}
            />
          </div>
          <div className="text-[10px] text-muted mt-1 font-mono">{pct}%</div>
        </div>
      )}

      {/* 步骤列表 */}
      <div className="px-3 py-2 max-h-[280px] overflow-y-auto space-y-0.5">
        {activePlan.steps.map((step) => (
          <PlanStepRow key={step.index} step={step} compact />
        ))}
      </div>

      {/* 文件 / 风险 */}
      {(activePlan.files || activePlan.risks) && (
        <div className="px-3 pb-2 space-y-1 border-t border-border/50">
          {activePlan.files && (
            <div className="flex items-start gap-2 text-xs pt-1.5">
              <FileText size={11} className="text-muted shrink-0 mt-0.5" />
              <div className="flex-1 min-w-0">
                <span className="text-muted mr-1">{t("card.files", { defaultValue: "涉及" })}</span>
                <span className="text-foreground/80 break-words text-[11px]">{activePlan.files}</span>
              </div>
            </div>
          )}
          {activePlan.risks && (
            <div className="flex items-start gap-2 text-xs">
              <AlertTriangle size={11} className="text-yellow-500 shrink-0 mt-0.5" />
              <div className="flex-1 min-w-0">
                <span className="text-muted mr-1">{t("card.risks", { defaultValue: "风险" })}</span>
                <span className="text-yellow-600 dark:text-yellow-400 break-words text-[11px]">
                  {activePlan.risks}
                </span>
              </div>
            </div>
          )}
        </div>
      )}

      {/* 底部：状态 + 取消按钮 */}
      <div className="flex items-center gap-2 px-3 py-2 border-t border-border/50">
        <PlanStatusBadge plan={activePlan} />
        {activePlan.cancel_reason && (
          <span className="text-[10px] text-red-500 truncate flex-1 min-w-0" title={activePlan.cancel_reason}>
            {activePlan.cancel_reason}
          </span>
        )}
        <div className="flex-1" />
        {activePlan.status === "executing" && (
          <CancelPlanButton
            planId={activePlan.plan_id}
            chatId={activeChatId}
            onCancel={(reason) => updatePlanStatus(activeChatId, activePlan.plan_id, "cancelled", reason)}
          />
        )}
      </div>
    </div>
  );
}

function PlanStatusBadge({ plan }: { plan: PlanRecord }) {
  const { t } = useTranslation("plan");
  if (plan.status === "cancelled") {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-red-100 dark:bg-red-900/40 text-red-600 dark:text-red-300 text-[11px]">
        <XCircle size={10} />
        {t("status.cancelled", { defaultValue: "已取消" })}
      </span>
    );
  }
  if (plan.status === "completed") {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-green-100 dark:bg-green-900/40 text-green-600 dark:text-green-300 text-[11px]">
        <CheckCircle2 size={10} />
        {t("status.completed", { defaultValue: "已完成" })}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-accent-subtle text-accent text-[11px]">
      <Loader2 size={10} className="animate-spin" />
      {t("status.executing", { defaultValue: "执行中" })}
    </span>
  );
}

function CancelPlanButton({
  planId,
  chatId,
  onCancel,
}: {
  planId: string;
  chatId: string;
  onCancel: (reason?: string) => void;
}) {
  const { t } = useTranslation("plan");
  const [busy, setBusy] = useState(false);

  const handleCancel = async () => {
    if (busy) return;
    setBusy(true);
    try {
      onCancel(t("status.cancelledByUser", { defaultValue: "用户取消" }));
      try {
        await fetch("/api/chat/cancel-plan", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ chat_id: chatId, plan_id: planId }),
        });
      } catch {
        // 后端接口未实现时静默失败（前端已 optimistic 标记）
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <button
      onClick={handleCancel}
      disabled={busy}
      className={cn(
        "px-2 py-1 text-[11px] rounded border transition-colors shrink-0",
        "border-red-400/50 text-red-600 dark:text-red-300 hover:bg-red-50 dark:hover:bg-red-950/40",
        busy && "opacity-50 cursor-not-allowed",
      )}
    >
      {busy ? t("panel.cancelling", { defaultValue: "取消中…" }) : t("panel.cancel", { defaultValue: "取消" })}
    </button>
  );
}
