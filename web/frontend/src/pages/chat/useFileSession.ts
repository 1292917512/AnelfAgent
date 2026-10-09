import { useEffect, useRef, useState } from "react";
import axios from "axios";
import { workspaceApi } from "@/lib/api";
import type { WorkspaceFile } from "@/lib/types";
import { workspaceFileId, type WorkspaceFileRef } from "@/lib/workspace-file";
import { useFileEditorStore } from "@/stores/file-editor-store";
import { useChangesStore } from "@/stores/changes-store";

export interface FileConflict {
  id: string; ref: WorkspaceFileRef; remote: WorkspaceFile | null | undefined; error: unknown;
}

export function useFileSession(file: WorkspaceFileRef | undefined) {
  const id = file ? workspaceFileId(file) : null;
  const path = file?.path;
  const root = file?.root ?? "workspace";
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [attempt, retry] = useState(0);
  const [saving, setSaving] = useState(false);
  const [savedId, setSavedId] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<unknown>(null);
  const [conflict, setConflict] = useState<FileConflict | null>(null);
  const saveInFlight = useRef(false);
  const conflictRequest = useRef(0);
  useEffect(() => () => { conflictRequest.current += 1; }, []);
  const revision = useChangesStore((state) => path && root === "workspace" ? state.fileVersions[path] ?? 0 : 0);

  useEffect(() => {
    setLoadError(null);
    setSaveError(null);
    setLoading(false);
    setSavedId(null);
    if (!id || !path) return;
    const current = useFileEditorStore.getState().tabs.get(id);
    if (current && (!revision || current.draft !== current.file.content)) return;
    const controller = new AbortController();
    setLoading(true);
    workspaceApi.read(path, root, controller.signal).then(({ data }) => {
      if (controller.signal.aborted) return;
      useFileEditorStore.getState().setTabs((tabs) => {
        const latest = tabs.get(id);
        if (latest && latest.draft !== latest.file.content) return tabs;
        return new Map(tabs).set(id, { file: data, draft: data.content });
      });
    }).catch((error: unknown) => { if (!controller.signal.aborted) setLoadError(error); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [id, path, root, attempt, revision]);

  async function readConflict(target: FileConflict): Promise<void> {
    const ticket = ++conflictRequest.current;
    setConflict({ ...target, remote: undefined, error: null });
    try {
      const { data } = await workspaceApi.read(target.ref.path, target.ref.root);
      if (ticket !== conflictRequest.current) return;
      setConflict({ ...target, remote: data, error: null });
    } catch (error) {
      if (ticket !== conflictRequest.current) return;
      setConflict({ ...target, remote: axios.isAxiosError(error) && error.response?.status === 404 ? null : undefined,
        error: axios.isAxiosError(error) && error.response?.status === 404 ? null : error });
    }
  }

  async function persist(target: WorkspaceFileRef, expected: string | null): Promise<void> {
    const targetId = workspaceFileId(target);
    const tab = useFileEditorStore.getState().tabs.get(targetId);
    if (!tab || saveInFlight.current) return;
    saveInFlight.current = true;
    setSaving(true);
    setSaveError(null);
    try {
      const { data } = await workspaceApi.write(target.path, tab.draft, target.root, expected);
      useFileEditorStore.getState().acknowledge(targetId, data);
      setSavedId(targetId);
      setConflict(null);
    } catch (error) {
      if (axios.isAxiosError(error) && error.response?.status === 409) {
        await readConflict({ id: targetId, ref: target, remote: undefined, error: null });
      } else setSaveError(error);
    } finally { saveInFlight.current = false; setSaving(false); }
  }

  return {
    loading, loadError, retry: () => retry((value) => value + 1), saving, saveError,
    saved: savedId === id,
    save: () => { const tab = id ? useFileEditorStore.getState().tabs.get(id) : null; if (file && tab && tab.draft !== tab.file.content && !tab.file.binary && !tab.file.truncated) void persist(file, tab.file.version); },
    conflict, closeConflict: () => { conflictRequest.current += 1; setConflict(null); },
    retryConflict: () => { if (conflict) void readConflict(conflict); },
    overwrite: () => { if (conflict && conflict.remote !== undefined) void persist(conflict.ref, conflict.remote?.version ?? null); },
    useRemote: () => {
      if (!conflict?.remote) return;
      const { id: targetId, remote } = conflict;
      useFileEditorStore.getState().setTabs((tabs) => tabs.has(targetId) ? new Map(tabs).set(targetId, { file: remote, draft: remote.content }) : tabs);
      setConflict(null);
    },
  };
}
