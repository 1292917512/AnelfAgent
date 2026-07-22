/**
 * 加载活动行 — 对齐 Claude Code SpinnerWithVerb 的对话窗口加载态：
 * 随机动词 + 耗时计时 + 当前工具活动（来自 thinking SSE 的运行中节点）。
 *
 * 设计为瞬时指示器（处理期间显示），不向聊天历史写入任何条目，
 * 符合 Anelf「记忆模式、条目受限」的对话窗口约束。
 */
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useChatStore } from "@/stores/chat-store";
import { useThinkingStore } from "@/stores/thinking-store";

const VERBS = [
  "思考中", "编织中", "酝酿中", "打磨中", "检索中",
  "推演中", "组装中", "联想中", "沉淀中", "校对中",
];

function pickVerb(seed: number) {
  return VERBS[seed % VERBS.length];
}

export function ActivityRow() {
  const { t } = useTranslation("chat");
  const sendingSince = useChatStore((s) => s.sendingSince);
  const activeSession = useThinkingStore((s) => s.activeSession);

  const [elapsed, setElapsed] = useState(0);
  const [verbSeed] = useState(() => Math.floor(Math.random() * 1000));

  useEffect(() => {
    if (!sendingSince) return;
    const tick = () => setElapsed(Math.floor((Date.now() - sendingSince) / 1000));
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, [sendingSince]);

  // 当前运行中的工具节点（thinking SSE 实时事件，可能未启用则为空）
  const currentTool = useMemo(() => {
    const nodes = activeSession?.nodes ?? [];
    for (let i = nodes.length - 1; i >= 0; i--) {
      const node = nodes[i];
      if (node && node.status === "running" && node.type.includes("tool")) {
        return node.label;
      }
    }
    return "";
  }, [activeSession]);

  const verb = pickVerb(verbSeed + Math.floor(elapsed / 3));
  // 卡死提示：8 秒无进展时变暗红（对齐 Claude Code stalled 动画）
  const stalled = elapsed >= 8 && !currentTool;

  return (
    <div className="flex justify-start">
      <div className="bg-secondary rounded-lg px-4 py-2.5 flex items-center gap-2.5 text-sm">
        <span className="flex items-center gap-1">
          <span className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse-subtle" />
          <span className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse-subtle [animation-delay:0.15s]" />
          <span className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse-subtle [animation-delay:0.3s]" />
        </span>
        <span className={stalled ? "text-red-400" : "text-muted-foreground"}>
          {currentTool
            ? t("activity.usingTool", { tool: currentTool, defaultValue: `正在使用工具: ${currentTool}` })
            : `${verb}…`}
        </span>
        <span className="text-xs text-muted">{elapsed}s</span>
      </div>
    </div>
  );
}
