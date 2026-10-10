/**
 * DiffView — unified diff 渲染（行号槽深半档 + ⋮ hunk 分隔 + 符号对齐）。
 *
 * 排版规则移植自 Codex diff_render：
 * - 行号槽（gutter）底色比行底色深半档，保证行号在 pastel 底色上可读；
 * - hunk 之间用 `⋮` 省略行分隔（而非 `@@` 头直出）；
 * - `+/-` 符号列与内容列定宽对齐，内容换行时续行保持 gutter 缩进。
 */
import { useState } from "react";
import { FileDiff, ChevronDown, ChevronRight, ArrowRight, FileWarning } from "lucide-react";
import { cn } from "@/lib/utils";

/** 单个文件改动行头信息（rename/二进制/普通编辑的统一入口） */
interface DiffEntryMeta {
  path: string;
  move_from?: string;
  binary?: boolean;
}

function FileHeader({ meta, additions, removals, open, onToggle }: {
  meta: DiffEntryMeta;
  additions: number;
  removals: number;
  open: boolean;
  onToggle: () => void;
}) {
  const isRename = Boolean(meta.move_from);
  const fromName = isRename ? (meta.move_from!.split("/").pop() ?? meta.move_from!) : null;
  const toName = meta.path.split("/").pop() ?? meta.path;
  return (
    <button
      onClick={onToggle}
      className="flex items-center gap-2 w-full px-2.5 py-1.5 text-left hover:bg-elevated transition-colors"
    >
      {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
      {meta.binary
        ? <FileWarning className="h-3.5 w-3.5 text-amber-500" />
        : <FileDiff className="h-3.5 w-3.5 text-primary" />}
      {isRename ? (
        <span className="font-mono text-foreground/80 truncate flex items-center gap-1">
          <span className="text-muted">{fromName}</span>
          <ArrowRight className="h-3 w-3 text-muted shrink-0" />
          <span>{toName}</span>
        </span>
      ) : (
        <span className="font-mono text-foreground/80 truncate">{toName}</span>
      )}
      {!isRename && !meta.binary && (
        <>
          <span className="shrink-0 text-green-600">+{additions}</span>
          <span className="shrink-0 text-red-500">-{removals}</span>
        </>
      )}
      {meta.binary && (
        <span className="shrink-0 text-amber-600 dark:text-amber-400 text-[11px]">binary</span>
      )}
    </button>
  );
}

interface DiffLine {
  kind: "add" | "del" | "context" | "hunk";
  text: string;
  oldNo?: number;
  newNo?: number;
}

/** 解析 unified diff 为渲染行（meta/hunk 头转 hunk 分隔行；`\ No newline` 标记跳过不占行号） */
function parseUnifiedDiff(diff: string): DiffLine[] {
  const lines: DiffLine[] = [];
  let oldNo = 0;
  let newNo = 0;
  const raws = diff.split("\n");
  for (let i = 0; i < raws.length; i++) {
    const raw = raws[i]!;
    if (raw.startsWith("---") || raw.startsWith("+++")) continue;
    // "\ No newline at end of file" 是前一行 +/- 的附属标记，不是内容行：
    // 计入 context 会虚增新旧行号（其后全部行号偏移）
    if (raw.startsWith("\\")) continue;
    // 末尾换行 split 出的空串不是内容行（幻影行）
    if (raw === "" && i === raws.length - 1) continue;
    // 生成侧的截断标记原样展示（不能按 context 切首字符）
    if (raw.startsWith("...")) {
      lines.push({ kind: "hunk", text: raw });
      continue;
    }
    const hunk = raw.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
    if (hunk) {
      oldNo = parseInt(hunk[1] ?? "0", 10);
      newNo = parseInt(hunk[2] ?? "0", 10);
      lines.push({ kind: "hunk", text: "⋮" });
      continue;
    }
    if (raw.startsWith("+")) {
      lines.push({ kind: "add", text: raw.slice(1), newNo: newNo++ });
    } else if (raw.startsWith("-")) {
      lines.push({ kind: "del", text: raw.slice(1), oldNo: oldNo++ });
    } else {
      lines.push({ kind: "context", text: raw.slice(1), oldNo: oldNo++, newNo: newNo++ });
    }
  }
  return lines;
}

/** gutter（行号槽）与内容行的配色：gutter 底色比行底色深半档 */
const ROW_BG: Record<string, string> = {
  add: "bg-green-500/10",
  del: "bg-red-500/10",
  context: "",
  hunk: "bg-elevated",
};
const GUTTER_BG: Record<string, string> = {
  add: "bg-green-500/20",
  del: "bg-red-500/20",
  context: "bg-elevated",
  hunk: "bg-elevated",
};
const TEXT_CLS: Record<string, string> = {
  add: "text-green-700 dark:text-green-300",
  del: "text-red-600 dark:text-red-300",
  context: "text-foreground/80",
  hunk: "text-muted",
};

function DiffRow({ line }: { line: DiffLine }) {
  if (line.kind === "hunk") {
    return (
      <tr>
        <td colSpan={3} className={cn("select-none px-2 py-0.5 text-center", GUTTER_BG.hunk, TEXT_CLS.hunk)}>
          ⋮
        </td>
      </tr>
    );
  }
  return (
    <tr className={ROW_BG[line.kind]}>
      {/* gutter：行号槽底色深半档，右对齐等宽 */}
      <td className={cn("select-none w-10 px-1.5 text-right font-mono text-muted/70 align-top", GUTTER_BG[line.kind])}>
        {line.oldNo ?? ""}
      </td>
      <td className={cn("select-none w-10 px-1.5 text-right font-mono text-muted/70 align-top", GUTTER_BG[line.kind])}>
        {line.newNo ?? ""}
      </td>
      <td className={cn("px-2 whitespace-pre-wrap break-all", TEXT_CLS[line.kind])}>
        {line.kind === "add" ? "+ " : line.kind === "del" ? "- " : "  "}
        {line.text}
      </td>
    </tr>
  );
}

export function DiffView({
  path,
  diff,
  additions,
  removals,
  move_from,
  binary,
}: {
  path: string;
  diff: string;
  additions: number;
  removals: number;
  move_from?: string;
  binary?: boolean;
}) {
  const [open, setOpen] = useState(true);
  const lines = parseUnifiedDiff(diff);

  return (
    <div className="rounded border border-border/60 bg-elevated text-xs overflow-hidden">
      <FileHeader
        meta={{ path, move_from, binary }}
        additions={additions}
        removals={removals}
        open={open}
        onToggle={() => setOpen(!open)}
      />
      {open && binary && (
        <div className="border-t border-border/40 px-3 py-2 text-muted text-[11px]">
          {/* 二进制改动：无 unified diff，占位说明（Codex 此处反而没做） */}
          二进制文件已改动（无法按行展示差异）
        </div>
      )}
      {open && !binary && lines.length > 0 && (
        <div className="border-t border-border/40 overflow-x-auto max-h-64 overflow-y-auto">
          <table className="w-full font-mono border-collapse">
            <tbody>
              {lines.map((line, i) => (
                <DiffRow key={i} line={line} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
