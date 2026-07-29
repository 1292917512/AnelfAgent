import type { Extension } from "@codemirror/state";
import { python } from "@codemirror/lang-python";
import { javascript } from "@codemirror/lang-javascript";
import { json } from "@codemirror/lang-json";
import { markdown } from "@codemirror/lang-markdown";
import { yaml } from "@codemirror/lang-yaml";
import { html } from "@codemirror/lang-html";
import { css } from "@codemirror/lang-css";
import { workspaceFileKind, type WorkspaceFile } from "@/lib/api";

/** 按扩展名映射 CodeMirror 语言包 */
export function langExtension(path: string): Extension[] {
  const ext = path.split(".").pop()?.toLowerCase() || "";
  switch (ext) {
    case "py": return [python()];
    case "js": case "jsx": case "mjs": case "cjs":
      return [javascript({ jsx: true })];
    case "ts": case "tsx":
      return [javascript({ jsx: true, typescript: true })];
    case "json": return [json()];
    case "md": case "markdown": return [markdown()];
    case "yaml": case "yml": return [yaml()];
    case "html": case "htm": case "xml": case "svg": return [html()];
    case "css": return [css()];
    default: return [];
  }
}

/** 单个标签页的编辑状态（file 为已保存内容，draft 为当前草稿） */
export interface TabState {
  file: WorkspaceFile;
  draft: string;
}

export type ViewMode = "edit" | "preview" | "split";

/** 文本类文件打开时的默认视图：可渲染格式（md/html/csv）默认预览，其余默认编辑 */
export function defaultViewMode(path: string): ViewMode {
  const kind = workspaceFileKind(path);
  return kind === "markdown" || kind === "html" || kind === "csv" ? "preview" : "edit";
}
