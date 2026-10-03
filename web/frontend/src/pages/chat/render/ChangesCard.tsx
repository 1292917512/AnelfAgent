/** 改动集卡片 — 一轮回复的全部文件改动聚合展示（点击单文件跳编辑器对应位置）。 */

import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, ChevronRight, FileDiff } from "lucide-react";
import { useWorkbenchStore } from "@/stores/workbench-store";
import type { ChatStreamingDiff } from "@/lib/types";
import { cn } from "@/lib/utils";
import { fileIcon } from "../filetree/file-tree-utils";
import { DiffView } from "../DiffView";

/** 折叠头部最多平铺的文件 chip 数，溢出收敛为 +N（ZCode changes-group 形态） */
const MAX_HEADER_CHIPS = 3;

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
          <DiffView
            path={entry.path}
            diff={entry.diff}
            additions={entry.additions}
            removals={entry.removals}
            move_from={entry.move_from}
            binary={entry.binary}
          />
        </div>
      )}
    </div>
  );
}

/** 本轮改动集卡片（消息内默认折叠，标题为改动文件计数 + 平铺文件 chips） */
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
        {open ? <ChevronDown className="h-3.5 w-3.5 shrink-0" /> : <ChevronRight className="h-3.5 w-3.5 shrink-0" />}
        <FileDiff className="h-3.5 w-3.5 text-primary shrink-0" />
        <span className="text-xs font-medium text-heading shrink-0">
          {t("changes.title", { count: changes.length })}
        </span>
        {/* 平铺文件 chips（溢出 +N）：折叠态也能一眼看到改了哪些文件 */}
        <span className="flex items-center gap-1 min-w-0 overflow-hidden">
          {changes.slice(0, MAX_HEADER_CHIPS).map((entry, i) => {
            const name = entry.path.split("/").pop() ?? entry.path;
            const { Icon, className } = fileIcon(name);
            return (
              <span
                key={`${entry.path}-${i}`}
                title={entry.path}
                className="flex items-center gap-1 px-1.5 py-0.5 rounded bg-muted text-[10px] text-muted min-w-0"
              >
                <Icon size={10} className={cn("shrink-0", className)} />
                <span className="truncate max-w-24">{name}</span>
              </span>
            );
          })}
          {changes.length > MAX_HEADER_CHIPS && (
            <span className="text-[10px] text-muted shrink-0">+{changes.length - MAX_HEADER_CHIPS}</span>
          )}
        </span>
        <span className={cn("ml-auto text-[11px] text-muted shrink-0")}>
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
