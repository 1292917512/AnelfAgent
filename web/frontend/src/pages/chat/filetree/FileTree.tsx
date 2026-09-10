import { useCallback, useEffect, useRef, useState, type MouseEvent } from "react";
import { useTranslation } from "react-i18next";
import { Tree, type TreeApi } from "react-arborist";
import { Loader2 } from "lucide-react";
import { useIsMobile } from "@/lib/use-media-query";
import type { WorkspaceNode, WorkspaceRoot } from "@/lib/api";
import { useWorkbenchStore } from "@/stores/workbench-store";
import { ConfirmDialog } from "@/components/ui";
import { useFileTreeStore } from "./file-tree-store";
import { treeChildren } from "./file-tree-utils";
import { useElementSize } from "./use-element-size";
import { FileTreeContext, FileTreeNode, FileTreeRow } from "./FileTreeNode";
import { FileTreeDragPreview } from "./FileTreeDragPreview";
import { FileTreeContextMenu, type MenuState } from "./FileTreeContextMenu";

/** 下一渲染周期（setTimeout 实现——部分 WebView 中 requestAnimationFrame 不触发） */
const nextFrame = () => new Promise<void>((r) => setTimeout(r, 50));

interface Props {
  root: WorkspaceRoot;
  onUpload: (dir: string) => void;
}

/** 文件树主体：react-arborist 虚拟化渲染 + 懒加载 + 重命名/移动/删除 + AI 焦点定位 */
export function FileTree({ root, onUpload }: Props) {
  const { t } = useTranslation("workbench");
  const isMobile = useIsMobile();
  const treeRef = useRef<TreeApi<WorkspaceNode>>(null);
  const { ref: boxRef, size } = useElementSize<HTMLDivElement>();
  const tree = useFileTreeStore((s) => s.trees[root]);
  const pendingEdit = useFileTreeStore((s) => s.pendingEdit);
  const collapseSeq = useFileTreeStore((s) => s.collapseSeq);
  const fileTreeFocus = useWorkbenchStore((s) => s.fileTreeFocus);
  const [menu, setMenu] = useState<MenuState | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<WorkspaceNode | null>(null);
  const [deleting, setDeleting] = useState(false);

  const store = useFileTreeStore.getState();

  useEffect(() => {
    void store.loadRoot(root);
    // 仅在挂载 / 切换根时加载
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [root]);

  /** 沿路径逐段加载并展开祖先目录，等待渲染窗口刷新后返回目标节点 */
  const revealPath = useCallback(
    async (path: string) => {
      // 树组件要等容器尺寸测量后才挂载（首帧 treeRef 为空），先等它就绪
      for (let i = 0; i < 10 && !treeRef.current; i++) await nextFrame();
      const segs = path.split("/").slice(0, -1);
      let cur = "";
      for (const seg of segs) {
        cur = cur ? `${cur}/${seg}` : seg;
        await store.loadChildren(root, cur);
        treeRef.current?.open(cur);
        // 等一帧让新子级进入渲染数据，下一段才拿得到节点
        await nextFrame();
      }
      // 数据刷新与虚拟列表渲染存在帧差，轮询等目标行真正渲染出来（rowIndex 非空）
      for (let i = 0; i < 12; i++) {
        const node = treeRef.current?.get(path);
        if (node && node.rowIndex !== null) return node;
        await nextFrame();
      }
      return null;
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [root],
  );

  // 新建/重命名请求：展开祖先后进入内联编辑
  useEffect(() => {
    if (!pendingEdit || pendingEdit.root !== root) return;
    let cancelled = false;
    void revealPath(pendingEdit.path).then((node) => {
      if (!cancelled && node) void treeRef.current?.edit(pendingEdit.path);
      if (!cancelled) store.clearPendingEdit();
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingEdit, root, revealPath]);

  // 全部折叠信号
  useEffect(() => {
    if (collapseSeq > 0) treeRef.current?.closeAll();
  }, [collapseSeq]);

  // AI ui_open_panel(files, path) 焦点定位：展开祖先 + 选中 + 滚动到位
  useEffect(() => {
    if (!fileTreeFocus) return;
    let cancelled = false;
    void revealPath(fileTreeFocus).then((node) => {
      if (cancelled) return;
      if (node) {
        node.select();
        // 直接按行号换算偏移滚动（scrollTo 内部 waitFor 在部分 WebView 不可靠）
        const rowH = isMobile ? 32 : 26;
        const api = treeRef.current;
        if (api && node.rowIndex !== null) {
          api.scrollToOffset(Math.max(0, node.rowIndex * rowH - api.height / 2));
        }
      }
      useWorkbenchStore.getState().setFileTreeFocus(null);
    });
    return () => {
      cancelled = true;
    };
  }, [fileTreeFocus, root, revealPath, isMobile]);

  const openContextMenu = useCallback(
    (e: MouseEvent, node: WorkspaceNode | null) => {
      setMenu({ x: e.clientX, y: e.clientY, node });
    },
    [],
  );

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    const ok = await store.removeEntry(root, deleteTarget.path);
    setDeleting(false);
    if (ok) setDeleteTarget(null);
  };

  return (
    <FileTreeContext.Provider value={{ root, onContextMenu: openContextMenu }}>
      <div
        ref={boxRef}
        className="flex-1 min-h-0"
        onContextMenu={(e) => {
          // 行内右键已 stopPropagation，这里只处理空白区域
          e.preventDefault();
          openContextMenu(e, null);
        }}
      >
        {tree.error && <p className="px-2 py-3 text-xs text-danger">{t("files.loadFailed")}</p>}
        {!tree.error && tree.children === null && (
          <div className="flex items-center gap-2 px-2 py-3 text-xs text-muted">
            <Loader2 size={13} className="animate-spin" /> {t("files.loading")}
          </div>
        )}
        {!tree.error && tree.children !== null && tree.children.length === 0 && (
          <p className="px-2 py-3 text-xs text-muted">{t("files.empty")}</p>
        )}
        {!tree.error && tree.children !== null && size && (
          <Tree
            ref={treeRef}
            data={tree.children}
            idAccessor="path"
            childrenAccessor={treeChildren}
            renderRow={FileTreeRow}
            renderDragPreview={FileTreeDragPreview}
            openByDefault={false}
            width={size.width}
            height={size.height}
            rowHeight={isMobile ? 32 : 26}
            indent={14}
            padding={6}
            disableDrop={({ parentNode, dragNodes }) =>
              parentNode !== null &&
              (parentNode.isLeaf || dragNodes.some((d) => d.id === parentNode.id || d.isAncestorOf(parentNode)))
            }
            onToggle={(id) => {
              const node = treeRef.current?.get(id);
              if (node?.data.type === "dir" && node.data.children === undefined) {
                void store.loadChildren(root, id);
              }
            }}
            onSelect={(nodes) => store.setSelection(root, nodes.length ? (nodes[nodes.length - 1]?.id ?? null) : null)}
            onActivate={(node) => {
              if (node.data.type === "dir") node.toggle();
              else useWorkbenchStore.getState().openFile(node.id, root);
            }}
            onRename={({ id, name }) => void store.renameEntry(root, id, name)}
            onMove={({ dragIds, parentId }) => void store.moveEntries(root, dragIds, parentId ?? "")}
          >
            {FileTreeNode}
          </Tree>
        )}
      </div>

      {menu && (
        <FileTreeContextMenu
          menu={menu}
          root={root}
          onClose={() => setMenu(null)}
          onDelete={setDeleteTarget}
          onUpload={onUpload}
        />
      )}

      <ConfirmDialog
        open={deleteTarget !== null}
        onClose={() => setDeleteTarget(null)}
        onConfirm={() => void confirmDelete()}
        title={t("files.deleteTitle")}
        message={t("files.deleteConfirm", { name: deleteTarget?.name ?? "" })}
        confirmText={t("files.delete")}
        danger
        loading={deleting}
      />
    </FileTreeContext.Provider>
  );
}
