import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import CodeMirror from "@uiw/react-codemirror";
import type { Extension } from "@codemirror/state";
import { python } from "@codemirror/lang-python";
import { javascript } from "@codemirror/lang-javascript";
import { json } from "@codemirror/lang-json";
import { markdown } from "@codemirror/lang-markdown";
import { yaml } from "@codemirror/lang-yaml";
import { html } from "@codemirror/lang-html";
import { css } from "@codemirror/lang-css";
import { Loader2, Save } from "lucide-react";
import { workspaceApi, workspaceMediaKind, type WorkspaceFile } from "@/lib/api";
import { useAppStore } from "@/stores/app-store";
import { useWorkbenchStore } from "@/stores/workbench-store";
import { Button } from "@/components/ui";
import { Drawer } from "@/components/common/Drawer";
import { Lightbox } from "./render/Lightbox";

/** 按扩展名映射 CodeMirror 语言包 */
function langExtension(path: string): Extension[] {
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

/** 工作区文件编辑器：CodeMirror + 保存 + 脏标记 */
export function FileEditor() {
  const { t } = useTranslation("workbench");
  const theme = useAppStore((s) => s.theme);
  const openFilePath = useWorkbenchStore((s) => s.openFilePath);
  const closeFile = useWorkbenchStore((s) => s.closeFile);

  const [file, setFile] = useState<WorkspaceFile | null>(null);
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savedTick, setSavedTick] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [lightboxOpen, setLightboxOpen] = useState(false);

  // 二进制媒体（图片/视频/音频）走预览而非文本编辑
  const mediaKind = file?.binary ? workspaceMediaKind(file.name) : null;
  const rawUrl = file ? workspaceApi.rawUrl(file.path) : "";

  useEffect(() => {
    if (!openFilePath) {
      setFile(null);
      setDraft("");
      return;
    }
    setLoading(true);
    setLoadError(false);
    workspaceApi.read(openFilePath).then((r) => {
      setFile(r.data);
      setDraft(r.data.content);
    }).catch(() => {
      setFile(null);
      setLoadError(true);
    }).finally(() => setLoading(false));
  }, [openFilePath]);

  const dirty = file !== null && draft !== file.content;

  const save = useCallback(async () => {
    if (!file || !dirty) return;
    setSaving(true);
    try {
      await workspaceApi.write(file.path, draft);
      setFile({ ...file, content: draft });
      setSavedTick(true);
      setTimeout(() => setSavedTick(false), 1500);
    } catch { /* 保存失败保留脏状态 */ }
    finally { setSaving(false); }
  }, [file, dirty, draft]);

  // Ctrl/Cmd+S 保存
  useEffect(() => {
    if (!openFilePath) return;
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "s") {
        e.preventDefault();
        save();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [openFilePath, save]);

  return (
    <Drawer
      open={openFilePath !== null}
      onClose={closeFile}
      width="max-w-3xl"
      title={
        <span className="flex items-center gap-2">
          <span className="truncate">{openFilePath}</span>
          {dirty && <span className="w-1.5 h-1.5 rounded-full bg-warn shrink-0" aria-label="dirty" />}
        </span>
      }
      footer={
        <>
          <span className="text-[11px] text-muted mr-auto">
            {file && `${(file.size / 1024).toFixed(1)} KB`}
            {savedTick && <span className="text-ok ml-2">{t("editor.saved")}</span>}
          </span>
          <Button variant="secondary" size="sm" onClick={closeFile}>
            {t("editor.close")}
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={save}
            disabled={!dirty || saving || !file || file.binary || file.truncated}
          >
            {saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
            {t("editor.save")}
          </Button>
        </>
      }
    >
      {loading && (
        <div className="flex items-center gap-2 py-8 justify-center text-sm text-muted">
          <Loader2 size={16} className="animate-spin" /> {t("editor.loading")}
        </div>
      )}
      {loadError && <p className="py-8 text-center text-sm text-danger">{t("editor.loadFailed")}</p>}
      {file && file.binary && mediaKind === "image" && (
        <div className="flex items-center justify-center py-4">
          <img
            src={rawUrl}
            alt={file.name}
            onClick={() => setLightboxOpen(true)}
            className="max-w-full max-h-[70vh] rounded-md border border-border cursor-zoom-in hover:opacity-90 transition-opacity"
          />
          {lightboxOpen && <Lightbox src={rawUrl} alt={file.name} onClose={() => setLightboxOpen(false)} />}
        </div>
      )}
      {file && file.binary && mediaKind === "video" && (
        <div className="flex items-center justify-center py-4">
          <video controls src={rawUrl} className="max-w-full max-h-[70vh] rounded-md border border-border" />
        </div>
      )}
      {file && file.binary && mediaKind === "audio" && (
        <div className="flex items-center justify-center py-12">
          <audio controls src={rawUrl} className="w-full max-w-md" />
        </div>
      )}
      {file && file.binary && !mediaKind && (
        <p className="py-8 text-center text-sm text-muted">{t("editor.binaryFile")}</p>
      )}
      {file && file.truncated && (
        <p className="py-8 text-center text-sm text-muted">{t("editor.tooLarge")}</p>
      )}
      {file && !file.binary && !file.truncated && (
        <CodeMirror
          value={draft}
          onChange={setDraft}
          extensions={langExtension(file.path)}
          theme={theme}
          height="100%"
          style={{ height: "calc(100vh - 220px)", fontSize: 13 }}
          basicSetup={{ lineNumbers: true, foldGutter: true, highlightActiveLine: true }}
        />
      )}
    </Drawer>
  );
}
