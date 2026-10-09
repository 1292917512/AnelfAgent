import type { WorkspaceRoot } from "./types";

export interface WorkspaceFileRef { path: string; root: WorkspaceRoot }

export function workspaceFileId(file: WorkspaceFileRef): string {
  return JSON.stringify([file.root, file.path]);
}

export function workspaceFileLabel(file: WorkspaceFileRef): string {
  return file.root === "project" ? `project:${file.path}` : file.path;
}

export function isFileUnder(file: WorkspaceFileRef, path: string, root: WorkspaceRoot): boolean {
  return file.root === root && (file.path === path || file.path.startsWith(path + "/"));
}
