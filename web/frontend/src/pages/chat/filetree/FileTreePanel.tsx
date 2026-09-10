import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  FilePlus2,
  FolderPlus,
  ListCollapse,
  Loader2,
  RefreshCw,
  Search,
  Upload,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { workspaceApi, type WorkspaceRoot, type WorkspaceSearchHit } from "@/lib/api";
import { useWorkbenchStore } from "@/stores/workbench-store";
import { useFileTreeStore } from "./file-tree-store";
import { fileIcon } from "./file-tree-utils";
import { FileTree } from "./FileTree";

/** 工具栏图标按钮 */
function ToolButton({
  title,
  onClick,
  disabled,
  children,
}: {
  title: string;
  onClick: () => void;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      className="p-1.5 rounded text-muted hover:text-foreground hover:bg-hover transition-colors disabled:opacity-40 disabled:hover:bg-transparent disabled:hover:text-muted"
    >
      {children}
    </button>
  );
}

/** 文件搜索结果列表（仅工作区根支持） */
function SearchResults({ root, onDone }: { root: WorkspaceRoot; onDone: () => void }) {
  const { t } = useTranslation("workbench");
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<WorkspaceSearchHit[] | null>(null);
  const [searching, setSearching] = useState(false);
  const openFile = useWorkbenchStore((s) => s.openFile);
  const setFileTreeFocus = useWorkbenchStore((s) => s.setFileTreeFocus);

  useEffect(() => {
    const q = query.trim();
    if (!q) {
      setHits(null);
      return;
    }
    setSearching(true);
    const timer = setTimeout(() => {
      workspaceApi
        .search(q)
        .then((r) => setHits(r.data.files))
        .catch(() => setHits([]))
        .finally(() => setSearching(false));
    }, 300);
    return () => clearTimeout(timer);
  }, [query]);

  return (
    <div className="flex flex-col h-full">
      <div className="px-2 py-1.5 border-b border-border shrink-0">
        <input
          autoFocus
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t("files.searchPlaceholder")}
          className="w-full px-2 py-1.5 text-xs bg-card border border-border rounded outline-none focus:border-accent"
        />
      </div>
      <div className="flex-1 overflow-y-auto p-1.5">
        {searching && (
          <div className="flex items-center gap-2 px-2 py-3 text-xs text-muted">
            <Loader2 size={13} className="animate-spin" /> {t("files.loading")}
          </div>
        )}
        {!searching && hits !== null && hits.length === 0 && (
          <p className="px-2 py-3 text-xs text-muted">{t("files.searchEmpty")}</p>
        )}
        {!searching && hits === null && (
          <p className="px-2 py-3 text-[11px] text-muted">{t("files.searchHint")}</p>
        )}
        {hits?.map((hit) => {
          const { Icon, className } = fileIcon(hit.name);
          return (
            <button
              key={`${hit.match}:${hit.path}`}
              className="flex flex-col gap-0.5 w-full px-2 py-1.5 rounded text-left hover:bg-hover transition-colors"
              onClick={() => {
                openFile(hit.path, root);
                setFileTreeFocus(hit.path);
                onDone();
              }}
            >
              <span className="flex items-center gap-1.5 text-xs text-foreground">
                <Icon size={13} className={cn("shrink-0", className)} />
                <span className="truncate">{hit.name}</span>
                {hit.match === "content" && (
                  <span className="ml-auto text-[9px] px-1 rounded bg-accent-subtle text-accent shrink-0">
                    {t("files.matchContent")}
                  </span>
                )}
              </span>
              <span className="text-[10px] text-muted truncate pl-[18px]">{hit.snippet ?? hit.path}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

/** 左侧文件树面板：根切换 + 操作工具栏 + 搜索 + 虚拟化懒加载树 */
export function FileTreePanel() {
  const { t } = useTranslation("workbench");
  const [root, setRoot] = useState<WorkspaceRoot>("workspace");
  const [searchMode, setSearchMode] = useState(false);
  const uploadInputRef = useRef<HTMLInputElement>(null);
  const uploadDirRef = useRef("");
  const truncated = useFileTreeStore((s) => s.trees[root].truncated);
  const store = useFileTreeStore.getState();

  // 切换根时退出搜索（后端搜索仅支持工作区）
  useEffect(() => {
    if (root !== "workspace") setSearchMode(false);
  }, [root]);

  const createEntry = async (kind: "file" | "folder") => {
    const dir = store.selectedDir(root);
    // 目标目录可能尚未加载，先确保子级就绪（命名去重依据）
    if (dir) await store.loadChildren(root, dir);
    const path = await store.createEntry(
      root,
      dir,
      kind,
      t(kind === "file" ? "files.untitledFile" : "files.untitledFolder"),
    );
    if (!path) return;
    store.requestEdit(root, path);
    if (kind === "file") useWorkbenchStore.getState().openFile(path, root);
  };

  const pickUpload = (dir: string) => {
    uploadDirRef.current = dir;
    uploadInputRef.current?.click();
  };

  const handleUploadFiles = (files: FileList | null) => {
    if (!files?.length) return;
    void store.uploadFiles(root, uploadDirRef.current, Array.from(files));
  };

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-2 py-1.5 border-b border-border shrink-0 gap-1">
        {/* 根目录切换：工作区 / 项目，仅基准目录不同 */}
        <div className="flex items-center rounded-md border border-border overflow-hidden shrink-0">
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
        <div className="flex items-center shrink-0">
          <ToolButton title={t("files.newFile")} onClick={() => void createEntry("file")}>
            <FilePlus2 size={14} />
          </ToolButton>
          <ToolButton title={t("files.newFolder")} onClick={() => void createEntry("folder")}>
            <FolderPlus size={14} />
          </ToolButton>
          <ToolButton title={t("files.upload")} onClick={() => pickUpload(store.selectedDir(root))}>
            <Upload size={14} />
          </ToolButton>
          <ToolButton title={t("files.refresh")} onClick={() => void store.refreshDir(root, "")}>
            <RefreshCw size={14} />
          </ToolButton>
          <ToolButton title={t("files.collapseAll")} onClick={() => store.collapseAll()}>
            <ListCollapse size={14} />
          </ToolButton>
          <ToolButton
            title={t("files.search")}
            disabled={root !== "workspace"}
            onClick={() => setSearchMode((v) => !v)}
          >
            {searchMode ? <X size={14} /> : <Search size={14} />}
          </ToolButton>
        </div>
      </div>

      {searchMode ? (
        <SearchResults root={root} onDone={() => setSearchMode(false)} />
      ) : (
        <FileTree root={root} onUpload={pickUpload} />
      )}

      <div className="px-3 py-1.5 border-t border-border text-[10px] text-muted shrink-0 truncate">
        {truncated ? t("files.truncated") : t("files.dragHint")}
      </div>

      <input
        ref={uploadInputRef}
        type="file"
        multiple
        className="hidden"
        onChange={(e) => {
          handleUploadFiles(e.target.files);
          e.target.value = "";
        }}
      />
    </div>
  );
}
