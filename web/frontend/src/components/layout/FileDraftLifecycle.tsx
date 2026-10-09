import { workspaceFileId } from "@/lib/workspace-file";
import { useCallback, useEffect } from "react";
import { useBeforeUnload } from "react-router-dom";
import { useFileEditorStore } from "@/stores/file-editor-store";
import { useChatStore } from "@/stores/chat-store";
import { useWorkbenchStore } from "@/stores/workbench-store";

export function FileDraftLifecycle() {
  const paths = useWorkbenchStore((state) => state.openFiles);
  const dirty = useFileEditorStore((state) => [...state.tabs.values()].some((tab) => tab.file.content !== tab.draft));
  const chatDirty = useChatStore((state) => Object.values(state.buckets).some((bucket) => !!bucket.inputDraft || bucket.pendingFiles.length > 0 || bucket.submitting));
  useEffect(() => { useFileEditorStore.getState().prune(paths.map(workspaceFileId)); }, [paths]);
  useBeforeUnload(useCallback((event) => {
    if (dirty || chatDirty) { event.preventDefault(); event.returnValue = ""; }
  }, [dirty, chatDirty]));
  return null;
}
