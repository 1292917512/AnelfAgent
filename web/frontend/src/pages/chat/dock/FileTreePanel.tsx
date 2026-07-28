import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, ChevronRight, FileText, FolderClosed, FolderOpen, Loader2, RefreshCw } from "lucide-react";
import { cn } from "@/lib/utils";
import { workspaceApi, workspaceMediaKind, isPreviewableBinary, type WorkspaceNode, type WorkspaceRoot } from "@/lib/api";
import { useWorkbenchStore } from "@/stores/workbench-store";
import { WORKSPACE_FILE_MIME } from "../ChatInput";

interface TreeNodeProps {
  node: WorkspaceNode;
  depth: number;
  root: WorkspaceRoot;
}

function TreeNode({ node, depth, root }: TreeNodeProps) {
  const [expanded, setExpanded] = useState(false);
  const [children, setChildren] = useState<WorkspaceNode[] | null>(node.children ?? null);
  const [loading, setLoading] = useState(false);
  const openFiles = useWorkbenchStore((s) => s.openFiles);
  const openFilePath = useWorkbenchStore((s) => s.openFilePath);
  const openFile = useWorkbenchStore((s) => s.openFile);

  const isDir = node.type === "dir";
  const isActive = openFilePath === node.path;
  // 已在编辑器标签中打开但未激活
  const isOpened = !isActive && openFiles.includes(node.path);
  // 二进制中的图片/音视频/PDF/DOCX/XLSX 可打开预览，其余二进制不可编辑
  const openable = !node.binary || workspaceMediaKind(node.name) !== null || isPreviewableBinary(node.name);
  // 拖拽注入对话仅工作区可用（项目路径相对基准不同，注入后 agent 无法解析）
  const draggable = !isDir && root === "workspace";

  const toggle = useCallback(async () => {
    if (!isDir) {
      if (openable) openFile(node.path, root);
      return;
    }
    if (!expanded && children === null) {
      setLoading(true);
      try {
        const r = await workspaceApi.tree(node.path, 1, root);
        setChildren(r.data.children);
      } catch {
        setChildren([]);
      } finally {
        setLoading(false);
      }
    }
    setExpanded((v) => !v);
  }, [isDir, expanded, children, node.path, openable, openFile, root]);

  return (
    <div>
      <button
        onClick={toggle}
        draggable={draggable}
        onDragStart={(e) => {
          if (!draggable) return;
          e.dataTransfer.setData(WORKSPACE_FILE_MIME, JSON.stringify({ path: node.path, name: node.name }));
          e.dataTransfer.effectAllowed = "copy";
        }}
        className={cn(
          "flex items-center gap-1 w-full px-1.5 py-1 rounded text-left text-xs transition-colors",
          isActive ? "bg-accent-subtle text-accent" : "text-foreground hover:bg-hover",
          !openable && !isDir && "opacity-50",
        )}
        style={{ paddingLeft: `${6 + depth * 12}px` }}
        title={node.path}
      >
        {isDir ? (
          <>
            {loading ? (
              <Loader2 size={11} className="animate-spin shrink-0 text-muted" />
            ) : expanded ? (
              <ChevronDown size={11} className="shrink-0 text-muted" />
            ) : (
              <ChevronRight size={11} className="shrink-0 text-muted" />
            )}
            {expanded ? <FolderOpen size={13} className="shrink-0 text-accent" /> : <FolderClosed size={13} className="shrink-0 text-muted" />}
          </>
        ) : (
          <>
            <span className="w-[11px] shrink-0" />
            <FileText size={13} className="shrink-0 text-muted" />
          </>
        )}
        <span className="truncate">{node.name}</span>
        {isOpened && <span className="w-1 h-1 rounded-full bg-accent shrink-0 ml-auto" aria-label="opened" />}
      </button>
      {isDir && expanded && children && (
        <div>
          {children.map((c) => (
            <TreeNode key={c.path} node={c} depth={depth + 1} root={root} />
          ))}
          {children.length === 0 && (
            <div className="text-[10px] text-muted" style={{ paddingLeft: `${6 + (depth + 1) * 12}px` }}>—</div>
          )}
        </div>
      )}
    </div>
  );
}

/** 左侧文件树：懒加载目录 + 点击编辑 + 拖拽注入对话。根目录可在工作区 / 项目间切换，渲染规则完全一致。 */
export function FileTreePanel() {
  const { t } = useTranslation("workbench");
  const [root, setRoot] = useState<WorkspaceRoot>("workspace");
  const [roots, setRoots] = useState<WorkspaceNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const fileTreeFocus = useWorkbenchStore((s) => s.fileTreeFocus);
  const setFileTreeFocus = useWorkbenchStore((s) => s.setFileTreeFocus);

  const load = useCallback(async (r: WorkspaceRoot) => {
    setLoading(true);
    setError(false);
    try {
      const res = await workspaceApi.tree("", 2, r);
      setRoots(res.data.children);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(root); }, [load, root]);

  // AI ui_open_panel(files, path) 定位后清除 focus 标记
  useEffect(() => {
    if (fileTreeFocus) setFileTreeFocus(null);
  }, [fileTreeFocus, setFileTreeFocus]);

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-3 py-2 border-b border-border shrink-0 gap-2">
        {/* 根目录切换：工作区 / 项目，仅基准目录不同 */}
        <div className="flex items-center rounded-md border border-border overflow-hidden">
          {(["workspace", "project"] as WorkspaceRoot[]).map((r) => (
            <button
              key={r}
              onClick={() => setRoot(r)}
              className={cn(
                "px-2 py-1 text-[10px] font-medium transition-colors",
                root === r ? "bg-accent-subtle text-accent" : "text-muted hover:text-foreground",
              )}
            >
              {t(r === "workspace" ? "files.rootWorkspace" : "files.rootProject")}
            </button>
          ))}
        </div>
        <button onClick={() => load(root)} className="p-1 rounded text-muted hover:text-foreground transition-colors" title={t("files.refresh")}>
          <RefreshCw size={13} />
        </button>
      </div>
      <div className="flex-1 overflow-y-auto p-1.5">
        {loading && (
          <div className="flex items-center gap-2 px-2 py-3 text-xs text-muted">
            <Loader2 size={13} className="animate-spin" /> {t("files.loading")}
          </div>
        )}
        {error && <p className="px-2 py-3 text-xs text-danger">{t("files.loadFailed")}</p>}
        {!loading && !error && roots.length === 0 && (
          <p className="px-2 py-3 text-xs text-muted">{t("files.empty")}</p>
        )}
        {!loading && !error && roots.map((n) => (
          <TreeNode key={n.path} node={n} depth={0} root={root} />
        ))}
      </div>
      <div className="px-3 py-2 border-t border-border text-[10px] text-muted shrink-0">
        {t("files.dragHint")}
      </div>
    </div>
  );
}
