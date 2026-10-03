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
import type { WorkspaceNode } from "@/lib/types";

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

/**
 * 单子目录链压缩（compacted folders）：a/b/c 折成一行。
 *
 * 仅当目录已加载且恰有唯一子目录时合并：节点取最深目录的路径（懒加载/
 * 右键/拖拽语义全部指向最深目录），显示名合并为 "a/b/c"。未加载的目录
 * 不合并（展开后数据到位自然延伸链条）。
 */
export function compactChains(nodes: WorkspaceNode[]): WorkspaceNode[] {
  return nodes.map((node) => {
    if (node.type !== "dir") return node;
    let cur = node;
    const names = [node.name];
    let children = node.children;
    while (children?.length === 1 && children[0]?.type === "dir") {
      const child = children[0];
      names.push(child.name);
      cur = child;
      children = child.children;
    }
    if (cur === node) {
      // 无链合并，但子级内部可能有链
      return node.children ? { ...node, children: compactChains(node.children) } : node;
    }
    return {
      ...cur,
      name: names.join("/"),
      children: children ? compactChains(children) : children,
    };
  });
}

/**
 * 目录链的链尾路径：沿「唯一子目录」下潜到最深（与 compactChains 的合并
 * 规则一致）。展示树中链节点的 id 即链尾路径——reveal/懒加载展开时
 * 据此把「打开中间目录」翻译成「打开链尾节点」。
 */
export function chainTailPath(node: WorkspaceNode): string {
  let cur = node;
  while (cur.children?.length === 1 && cur.children[0]?.type === "dir") {
    cur = cur.children[0];
  }
  return cur.path;
}

/**
 * 「只看变更」过滤：保留有改动的文件与其祖先目录。
 * 目录的命中判定以变更路径前缀为准（未加载的目录也能正确保留）。
 */
export function filterChangedTree(
  nodes: WorkspaceNode[],
  changed: Record<string, unknown>,
): WorkspaceNode[] {
  const dirHasChange = (p: string) => Object.keys(changed).some((k) => k.startsWith(p + "/"));
  const walk = (list: WorkspaceNode[]): WorkspaceNode[] =>
    list.flatMap((n) => {
      if (n.type === "dir") {
        if (!dirHasChange(n.path)) return [];
        return [{ ...n, children: n.children ? walk(n.children) : n.children }];
      }
      return changed[n.path] !== undefined ? [n] : [];
    });
  return walk(nodes);
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
