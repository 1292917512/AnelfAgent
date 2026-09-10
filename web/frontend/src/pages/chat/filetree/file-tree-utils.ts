import type { LucideIcon } from "lucide-react";
import {
  File as FileIcon,
  FileArchive,
  FileCode2,
  FileImage,
  FileMusic,
  FileSpreadsheet,
  FileText,
  FileVideo,
} from "lucide-react";
import type { WorkspaceNode } from "@/lib/api";

/** 取父目录路径（顶层返回 ""） */
export function parentPath(path: string): string {
  const i = path.lastIndexOf("/");
  return i < 0 ? "" : path.slice(0, i);
}

/** 拼接目录与名称（dir 为 "" 时即根目录） */
export function joinPath(dir: string, name: string): string {
  return dir ? `${dir}/${name}` : name;
}

/** 在同级已有名称中生成不冲突的名称：name.ext → name 2.ext → name 3.ext */
export function uniqueName(existing: string[], name: string): string {
  if (!existing.includes(name)) return name;
  const dot = name.lastIndexOf(".");
  const stem = dot > 0 ? name.slice(0, dot) : name;
  const ext = dot > 0 ? name.slice(dot) : "";
  for (let i = 2; ; i++) {
    const candidate = `${stem} ${i}${ext}`;
    if (!existing.includes(candidate)) return candidate;
  }
}

/** 目录树不可变更新：替换 path 目录的子级（path 为 "" 时替换整棵树） */
export function replaceChildren(
  nodes: WorkspaceNode[],
  path: string,
  children: WorkspaceNode[],
): WorkspaceNode[] {
  return nodes.map((n) => {
    if (n.type !== "dir") return n;
    if (n.path === path) return { ...n, children };
    if (n.children && path.startsWith(n.path + "/")) {
      return { ...n, children: replaceChildren(n.children, path, children) };
    }
    return n;
  });
}

/** 在树中按路径查找节点 */
export function findNode(nodes: WorkspaceNode[], path: string): WorkspaceNode | null {
  for (const n of nodes) {
    if (n.path === path) return n;
    if (n.type === "dir" && n.children && path.startsWith(n.path + "/")) {
      const hit = findNode(n.children, path);
      if (hit) return hit;
    }
  }
  return null;
}

/** 目录是否为内部节点（有展开箭头）：已加载或有可见子项 */
export function isExpandableDir(node: WorkspaceNode): boolean {
  return node.type === "dir" && (node.children !== undefined || node.has_children === true);
}

/** react-arborist childrenAccessor：不可展开的目录返回 null（渲染为叶子，无箭头） */
export function treeChildren(node: WorkspaceNode): readonly WorkspaceNode[] | null {
  if (node.type !== "dir") return null;
  if (node.children) return node.children;
  return node.has_children ? [] : null;
}

const ICON_RULES: [string[], LucideIcon, string][] = [
  [["jpg", "jpeg", "png", "gif", "webp", "bmp", "svg", "ico"], FileImage, "text-emerald-500"],
  [["mp4", "webm", "mov", "mkv", "avi", "flv"], FileVideo, "text-violet-500"],
  [["mp3", "wav", "ogg", "flac", "m4a", "opus"], FileMusic, "text-amber-500"],
  [["csv", "tsv", "xlsx", "xls"], FileSpreadsheet, "text-green-600"],
  [["zip", "tar", "gz", "rar", "7z"], FileArchive, "text-orange-500"],
  [["md", "markdown", "txt", "log", "pdf", "docx", "doc"], FileText, "text-muted"],
  [
    ["py", "js", "jsx", "ts", "tsx", "json", "yaml", "yml", "toml", "sh", "sql", "html", "htm", "css", "xml", "ini", "cfg"],
    FileCode2,
    "text-sky-500",
  ],
];

/** 按扩展名取文件图标与配色（未命中为通用文件图标） */
export function fileIcon(name: string): { Icon: LucideIcon; className: string } {
  const ext = name.split(".").pop()?.toLowerCase() ?? "";
  for (const [exts, Icon, className] of ICON_RULES) {
    if (exts.includes(ext)) return { Icon, className };
  }
  return { Icon: FileIcon, className: "text-muted" };
}
