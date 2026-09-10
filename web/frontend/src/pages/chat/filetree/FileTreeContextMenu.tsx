import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import {
  Copy,
  Download,
  FilePlus2,
  FolderPlus,
  FolderSync,
  ListCollapse,
  Pencil,
  RefreshCw,
  Trash2,
  Upload,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { workspaceApi, type WorkspaceNode, type WorkspaceRoot } from "@/lib/api";
import { toast } from "@/stores/toast-store";
import { useWorkbenchStore } from "@/stores/workbench-store";
import { useFileTreeStore } from "./file-tree-store";
import { parentPath } from "./file-tree-utils";

export interface MenuState {
  x: number;
  y: number;
  /** null 表示空白区域（根级操作） */
  node: WorkspaceNode | null;
}

interface MenuItem {
  key: string;
  label: string;
  icon: LucideIcon;
  danger?: boolean;
  dividerBefore?: boolean;
  onClick: () => void;
}

interface Props {
  menu: MenuState;
  root: WorkspaceRoot;
  onClose: () => void;
  onDelete: (node: WorkspaceNode) => void;
  onUpload: (dir: string) => void;
}

/** 文件树右键菜单：文件/目录/空白区域三套条目，视口内自动收拢定位 */
export function FileTreeContextMenu({ menu, root, onClose, onDelete, onUpload }: Props) {
  const { t } = useTranslation("workbench");
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ x: menu.x, y: menu.y });
  const store = useFileTreeStore.getState();
  const { node } = menu;
  const dir = node ? (node.type === "dir" ? node.path : parentPath(node.path)) : "";

  // 视口收拢：菜单超出右/下边界时翻转贴边
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    setPos({
      x: Math.min(menu.x, window.innerWidth - rect.width - 8),
      y: Math.min(menu.y, window.innerHeight - rect.height - 8),
    });
  }, [menu]);

  useEffect(() => {
    const close = () => onClose();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", onKey);
    document.addEventListener("scroll", close, true);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("scroll", close, true);
    };
  }, [onClose]);

  const createEntry = async (kind: "file" | "folder") => {
    onClose();
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

  const copyPath = () => {
    onClose();
    if (!node) return;
    void navigator.clipboard.writeText(node.path).then(() => toast.success(t("files.copied")));
  };

  const items: MenuItem[] = node === null
    ? [
        { key: "new-file", label: t("files.newFile"), icon: FilePlus2, onClick: () => void createEntry("file") },
        { key: "new-folder", label: t("files.newFolder"), icon: FolderPlus, onClick: () => void createEntry("folder") },
        { key: "upload", label: t("files.upload"), icon: Upload, onClick: () => { onClose(); onUpload(""); } },
        { key: "refresh", label: t("files.refresh"), icon: RefreshCw, dividerBefore: true, onClick: () => { onClose(); void store.refreshDir(root, ""); } },
        { key: "collapse", label: t("files.collapseAll"), icon: ListCollapse, onClick: () => { onClose(); store.collapseAll(); } },
      ]
    : node.type === "dir"
      ? [
          { key: "new-file", label: t("files.newFile"), icon: FilePlus2, onClick: () => void createEntry("file") },
          { key: "new-folder", label: t("files.newFolder"), icon: FolderPlus, onClick: () => void createEntry("folder") },
          { key: "upload", label: t("files.uploadHere"), icon: Upload, onClick: () => { onClose(); onUpload(node.path); } },
          { key: "rename", label: t("files.rename"), icon: Pencil, dividerBefore: true, onClick: () => { onClose(); store.requestEdit(root, node.path); } },
          { key: "refresh-dir", label: t("files.refreshDir"), icon: FolderSync, onClick: () => { onClose(); void store.refreshDir(root, node.path); } },
          { key: "copy-path", label: t("files.copyPath"), icon: Copy, onClick: copyPath },
          { key: "delete", label: t("files.delete"), icon: Trash2, danger: true, dividerBefore: true, onClick: () => { onClose(); onDelete(node); } },
        ]
      : [
          { key: "open", label: t("files.open"), icon: FilePlus2, onClick: () => { onClose(); useWorkbenchStore.getState().openFile(node.path, root); } },
          { key: "rename", label: t("files.rename"), icon: Pencil, onClick: () => { onClose(); store.requestEdit(root, node.path); } },
          {
            key: "download",
            label: t("editor.download"),
            icon: Download,
            onClick: () => {
              onClose();
              const a = document.createElement("a");
              a.href = workspaceApi.rawUrl(node.path, false, root);
              a.download = node.name;
              a.click();
            },
          },
          { key: "copy-path", label: t("files.copyPath"), icon: Copy, onClick: copyPath },
          { key: "delete", label: t("files.delete"), icon: Trash2, danger: true, dividerBefore: true, onClick: () => { onClose(); onDelete(node); } },
        ];

  return createPortal(
    <div
      ref={ref}
      className="fixed z-[120] min-w-40 py-1 rounded-lg border border-border bg-card shadow-xl"
      style={{ left: pos.x, top: pos.y }}
      onMouseDown={(e) => e.stopPropagation()}
    >
      {items.map((item) => (
        <div key={item.key}>
          {item.dividerBefore && <div className="my-1 border-t border-border" />}
          <button
            className={cn(
              "flex items-center gap-2 w-full px-3 py-1.5 text-xs text-left transition-colors",
              item.danger ? "text-danger hover:bg-danger/10" : "text-foreground hover:bg-hover",
            )}
            onClick={item.onClick}
          >
            <item.icon size={13} className="shrink-0" />
            {item.label}
          </button>
        </div>
      ))}
    </div>,
    document.body,
  );
}
