import type { WorkspaceFileRef } from "./workspace-file";

export interface FileReference extends WorkspaceFileRef { isDir: boolean }

/** 仅识别技能目录与记忆索引；普通 Markdown 文件保持文件样式。 */
export function fileReferenceKind(reference: FileReference, index?: string): "skill" | "memory" | "file" {
  if (reference.isDir) return "file";
  if (reference.root === "workspace" && /^skills\/[^/]+\//i.test(reference.path)) return "skill";
  if (reference.root === "project" && (/^(?:config\/)?memory\/.+\.md$/i.test(reference.path)
    || /^memory\/.+\.md$/i.test(index ?? ""))) return "memory";
  return "file";
}

/** 将文档内相对链接解析到所属目录，显式项目引用仍保持原根。 */
export function resolveDocumentReference(href: string, base?: WorkspaceFileRef): FileReference | null {
  if (!base) return parseFileReference(href);
  if (!href || /^[#/]|^[a-z][a-z\d+.-]*:/i.test(href)) return null;
  let path: string;
  try { path = decodeURIComponent(href.split(/[?#]/, 1)[0] ?? ""); } catch { return null; }
  if (path.startsWith("./project:")) return parseFileReference(href);
  if (!path || /^[\\/]|^[a-z][a-z\d+.-]*:/i.test(path)) return null;
  const parts = base.path.split("/");
  for (const part of path.replace(/\\/g, "/").split("/")) {
    if (!part || part === ".") continue;
    if (part === "..") { if (!parts.length) return null; parts.pop(); }
    else parts.push(part);
  }
  return parseFileReference(`./${base.root === "project" ? "project:" : ""}${encodeURIComponent(parts.join("/"))}`);
}

export function fileReferenceMarkdown(reference: FileReference, name: string): string {
  const label = name.replace(/\\/g, "\\\\").replace(/[[\]]/g, "\\$&").replace(/[\r\n]/g, " ");
  const path = `${reference.root === "project" ? "project:" : ""}${reference.isDir ? "dir:" : ""}${reference.path}`;
  const encoded = encodeURIComponent(path).replace(/%2F/gi, "/").replace(/[()]/g, (char) => `%${char.charCodeAt(0).toString(16)}`);
  return `[${label}](./${encoded})`;
}

export function parseFileReference(href: string): FileReference | null {
  if (!href.startsWith("./")) return null;
  let path: string;
  try { path = decodeURIComponent(href.slice(2)); } catch { return null; }
  const root = path.startsWith("project:") ? "project" : "workspace";
  if (root === "project") path = path.slice(8);
  const isDir = path.startsWith("dir:");
  if (isDir) path = path.slice(4);
  if (!path || /^[\\/]|^[a-z]:/i.test(path) || [...path].some((char) => char.charCodeAt(0) < 32) || path.split(/[\\/]/).some((part) => part === "..")) return null;
  return { path: path.replace(/\\/g, "/"), root, isDir };
}
