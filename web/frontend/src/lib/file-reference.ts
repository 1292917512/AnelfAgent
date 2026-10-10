import type { WorkspaceFileRef } from "./workspace-file";

export interface FileReference extends WorkspaceFileRef { isDir: boolean }

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
