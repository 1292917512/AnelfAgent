/** 改动集卡片 — 一轮回复的全部文件改动聚合展示（点击单文件跳编辑器对应位置）。 */

import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, ChevronRight, FileDiff } from "lucide-react";
import { useWorkbenchStore } from "@/stores/workbench-store";
import type { ChatStreamingDiff } from "@/lib/types";
import { cn } from "@/lib/utils";
import { DiffView } from "../DiffView";

/** 单个文件改动行（可点击跳编辑器） */
function ChangeRow({ entry }: { entry: ChatStreamingDiff }) {
  const openFile = useWorkbenchStore((s) => s.openFile);
  const setFileTreeFocus = useWorkbenchStore((s) => s.setFileTreeFocus);
  const fileName = entry.path.split("/").pop() ?? entry.path;
  const [open, setOpen] = useState(false);

  return (
    <div className="rounded border border-border/60 overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left hover:bg-muted/60 transition-colors"
      >
        {open ? <ChevronDown className="h-3.5 w-3.5 shrink-0" /> : <ChevronRight className="h-3.5 w-3.5 shrink-0" />}
        <span className="font-mono text-xs text-foreground/80 truncate flex-1">{fileName}</span>
        <span className="shrink-0 text-[11px] text-green-600">+{entry.additions}</span>
        <span className="shrink-0 text-[11px] text-red-500">-{entry.removals}</span>
        <span
          role="link"
          tabIndex={0}
          title={entry.path}
          onClick={(e) => {
            e.stopPropagation();
            openFile(entry.path);
            setFileTreeFocus(entry.path);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.stopPropagation();
              openFile(entry.path);
              setFileTreeFocus(entry.path);
            }
          }}
          className="shrink-0 text-[11px] text-accent hover:underline"
        >
          open
        </span>
      </button>
      {open && (
        <div className="border-t border-border/40">
          <DiffView path={entry.path} diff={entry.diff} additions={entry.additions} removals={entry.removals} />
        </div>
      )}
    </div>
  );
}

/** 本轮改动集卡片（消息内默认折叠，标题为改动文件计数） */
export function ChangesCard({ changes }: { changes: ChatStreamingDiff[] }) {
  const { t } = useTranslation("chat");
  const [open, setOpen] = useState(false);
  if (!changes.length) return null;

  return (
    <div className="mb-2 rounded-lg border border-border bg-card overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-muted/40 transition-colors"
      >
        {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
        <FileDiff className="h-3.5 w-3.5 text-primary" />
        <span className="text-xs font-medium text-heading">
          {t("changes.title", { count: changes.length })}
        </span>
        <span className={cn("ml-auto text-[11px] text-muted")}>
          {t("changes.hint")}
        </span>
      </button>
      {open && (
        <div className="border-t border-border/40 p-2 space-y-1">
          {changes.map((entry, i) => (
            <ChangeRow key={`${entry.path}-${i}`} entry={entry} />
          ))}
        </div>
      )}
    </div>
  );
}
