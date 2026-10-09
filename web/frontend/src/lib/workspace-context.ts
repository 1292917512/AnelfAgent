import type { WorkspaceContext } from "@/lib/types";
import { useWorkbenchStore } from "@/stores/workbench-store";
import { workspaceFileId, workspaceFileLabel } from "./workspace-file";

export function captureWorkspaceContext(): WorkspaceContext {
  const state = useWorkbenchStore.getState();
  const active = state.openFiles.find((file) => workspaceFileId(file) === state.activeFileId);
  const selection = state.selection && active && workspaceFileId(state.selection) === workspaceFileId(active) ? state.selection : null;
  return {
    active_file: active ? workspaceFileLabel(active) : null,
    selection: selection ? {
      path: workspaceFileLabel(selection), ranges: selection.ranges.slice(0, 20),
      content: selection.content.slice(0, 40_000),
    } : null,
    open_tabs: state.openFiles.slice(0, 100).map((file) => ({ label: file.path.split("/").pop() ?? file.path, path: workspaceFileLabel(file) })),
  };
}
