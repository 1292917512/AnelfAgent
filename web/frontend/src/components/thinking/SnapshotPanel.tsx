import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { ContextSnapshotData } from "@/lib/types";
import { SnapshotSectionBlock } from "@/components/common/SnapshotBlocks";
import { ChevronDown, ChevronRight, X } from "lucide-react";

interface SnapshotPanelProps {
  snapshot: ContextSnapshotData;
  onClose: () => void;
}

export function SnapshotPanel({ snapshot, onClose }: SnapshotPanelProps) {
  const { t } = useTranslation("thinking");
  const [showTools, setShowTools] = useState(false);

  return (
    <div className="h-full flex flex-col">
      {/* 头部 */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-border">
        <div>
          <span className="text-xs font-semibold text-heading">{t("snapshot.title")}</span>
          <div className="flex items-center gap-2 mt-0.5 text-[10px] text-muted">
            <span className="font-mono">{snapshot.model}</span>
            <span>·</span>
            <span>{snapshot.message_count} msgs</span>
            <span>·</span>
            <span>{snapshot.tool_count} tools</span>
            <span>·</span>
            <span>{new Date(snapshot.captured_at * 1000).toLocaleTimeString()}</span>
          </div>
        </div>
        <button
          onClick={onClose}
          className="p-1 rounded-sm text-muted hover:text-foreground hover:bg-hover"
          aria-label={t("common:close")}
        >
          <X size={14} />
        </button>
      </div>

      {/* 工具列表折叠 */}
      <div className="px-3 py-1.5 border-b border-border">
        <button
          onClick={() => setShowTools(!showTools)}
          className="flex items-center gap-2 text-[11px] text-muted hover:text-foreground"
        >
          {showTools ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
          <span>{t("snapshot.tools")} ({snapshot.tool_count})</span>
        </button>
        {showTools && (
          <div className="mt-1.5 flex flex-wrap gap-1">
            {snapshot.tool_names.map((name) => (
              <span key={name} className="px-1.5 py-0.5 rounded bg-elevated border border-border text-[10px] font-mono text-foreground/70">
                {name}
              </span>
            ))}
          </div>
        )}
      </div>

      {/* 分类 sections */}
      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {snapshot.sections.map((section) => (
          <SnapshotSectionBlock
            key={section.layer}
            section={section}
            defaultOpen={section.layer === "conversation" || section.layer === "tool_chain"}
          />
        ))}
      </div>
    </div>
  );
}
