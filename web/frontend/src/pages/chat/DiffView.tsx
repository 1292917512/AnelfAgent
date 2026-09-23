/**
 * DiffView — unified diff 渲染（行号槽深半档 + ⋮ hunk 分隔 + 符号对齐）。
 *
 * 排版规则移植自 Codex diff_render：
 * - 行号槽（gutter）底色比行底色深半档，保证行号在 pastel 底色上可读；
 * - hunk 之间用 `⋮` 省略行分隔（而非 `@@` 头直出）；
 * - `+/-` 符号列与内容列定宽对齐，内容换行时续行保持 gutter 缩进。
 */
import { useState } from "react";
import { FileDiff, ChevronDown, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";

interface DiffLine {
  kind: "add" | "del" | "context" | "hunk";
  text: string;
  oldNo?: number;
  newNo?: number;
}

/** 解析 unified diff 为渲染行（meta/hunk 头转 hunk 分隔行） */
function parseUnifiedDiff(diff: string): DiffLine[] {
  const lines: DiffLine[] = [];
  let oldNo = 0;
  let newNo = 0;
  for (const raw of diff.split("\n")) {
    if (raw.startsWith("---") || raw.startsWith("+++")) continue;
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
  hunk: "bg-muted/40",
};
const GUTTER_BG: Record<string, string> = {
  add: "bg-green-500/20",
  del: "bg-red-500/20",
  context: "bg-muted/30",
  hunk: "bg-muted/60",
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
}: {
  path: string;
  diff: string;
  additions: number;
  removals: number;
}) {
  const [open, setOpen] = useState(true);
  const lines = parseUnifiedDiff(diff);
  const fileName = path.split("/").pop() ?? path;

  return (
    <div className="rounded border border-border/60 bg-muted/40 text-xs overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-2 w-full px-2.5 py-1.5 text-left hover:bg-muted/60 transition-colors"
      >
        {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
        <FileDiff className="h-3.5 w-3.5 text-primary" />
        <span className="font-mono text-foreground/80 truncate">{fileName}</span>
        <span className="shrink-0 text-green-600">+{additions}</span>
        <span className="shrink-0 text-red-500">-{removals}</span>
      </button>
      {open && (
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
