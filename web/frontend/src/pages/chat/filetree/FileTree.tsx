import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import { useTranslation } from "react-i18next";
import { Tree, type TreeApi } from "react-arborist";
import { ChevronRight, Loader2 } from "lucide-react";
import { useIsMobile } from "@/lib/use-media-query";
import type { WorkspaceNode, WorkspaceRoot } from "@/lib/types";
import { useWorkbenchStore } from "@/stores/workbench-store";
import { ConfirmDialog } from "@/components/ui";
import { useFileTreeStore } from "./file-tree-store";
import { chainTailPath, compactChains, filterChangedTree, findNode, parentPath, treeChildren } from "./file-tree-utils";
import { useTreeChangesStore } from "@/stores/tree-changes-store";
import { useElementSize } from "./use-element-size";
import { FileTreeContext, FileTreeNode, FileTreeRow } from "./FileTreeNode";
import { registerTreeRoot, sharedDndManager } from "../tree-dnd";
import { FileTreeDragPreview } from "./FileTreeDragPreview";
import { FileTreeContextMenu, type MenuState } from "./FileTreeContextMenu";

/** 下一渲染周期（setTimeout 实现——部分 WebView 中 requestAnimationFrame 不触发） */
const nextFrame = () => new Promise<void>((r) => setTimeout(r, 50));

/** AI 变更触发目录局部刷新的防抖窗口与单次上限（防大批量改动刷爆请求） */
const REFRESH_DEBOUNCE_MS = 600;
const REFRESH_MAX_DIRS = 8;

interface Props {
  root: WorkspaceRoot;
  onUpload: (dir: string) => void;
  /** 只看变更：树数据过滤为改动文件 + 祖先目录 */
  changedOnly: boolean;
}

/** 文件树主体：虚拟化懒加载 + 目录链压缩 + 变更装饰联动 + 吸顶定位 */
export function FileTree({ root, onUpload, changedOnly }: Props) {
  const { t } = useTranslation("workbench");
  const isMobile = useIsMobile();
  // 登记当前根目录（拖起节点时对话区据此解析引用归属）
  useEffect(() => { registerTreeRoot(root); }, [root]);
  const treeRef = useRef<TreeApi<WorkspaceNode>>(null);
  const { ref: boxRef, size } = useElementSize<HTMLDivElement>();
  const maskRef = useRef<HTMLDivElement>(null);
  const tree = useFileTreeStore((s) => s.trees[root]);
  const pendingEdit = useFileTreeStore((s) => s.pendingEdit);
  const collapseSeq = useFileTreeStore((s) => s.collapseSeq);
  const fileTreeFocus = useWorkbenchStore((s) => s.fileTreeFocus);
  const changeEntries = useTreeChangesStore((s) => s.entries);
  const [menu, setMenu] = useState<MenuState | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<WorkspaceNode | null>(null);
  const [deleting, setDeleting] = useState(false);

  const store = useFileTreeStore.getState();
  const rowHeight = isMobile ? 32 : 26;

  useEffect(() => {
    void store.loadRoot(root);
    // 仅在挂载 / 切换根时加载
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [root]);

  // 展示数据：原始树 →（只看变更过滤）→ 目录链压缩（纯函数派生，store 保持真实结构）
  const displayData = useMemo(() => {
    if (tree.children === null) return null;
    const filtered = changedOnly ? filterChangedTree(tree.children, changeEntries) : tree.children;
    return compactChains(filtered);
  }, [tree.children, changedOnly, changeEntries]);

  // ------------------------------------------------------------------
  // AI 变更 → 已加载目录的局部刷新（新建/删除的文件不靠手动刷新出现）
  // ------------------------------------------------------------------
  const seenChangeTs = useRef<Record<string, number>>({});
  const pendingRefresh = useRef<Set<string>>(new Set());
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    // AI 编辑只落在工作区根，project 根不做变更联动刷新
    if (root !== "workspace") return;
    let hasFresh = false;
    for (const [p, e] of Object.entries(changeEntries)) {
      if (e.ts > (seenChangeTs.current[p] ?? 0)) {
        seenChangeTs.current[p] = e.ts;
        pendingRefresh.current.add(p);
        hasFresh = true;
      }
    }
    if (!hasFresh) return;
    if (refreshTimer.current) clearTimeout(refreshTimer.current);
    refreshTimer.current = setTimeout(() => {
      refreshTimer.current = null;
      // 取累积批次并清空（防抖窗口内多批到达合并为一次刷新）；
      // rename 的源父目录一并刷新（否则旧文件在原地滞留）
      const paths = [...pendingRefresh.current];
      pendingRefresh.current.clear();
      const children = useFileTreeStore.getState().trees[root].children;
      if (children === null) return;
      const dirs = [...new Set(paths.flatMap((p) => {
        const parents = [parentPath(p)];
        const moveFrom = changeEntries[p]?.move_from;
        if (moveFrom) parents.push(parentPath(moveFrom));
        return parents;
      }))].slice(0, REFRESH_MAX_DIRS);
      for (const dir of dirs) {
        if (dir === "") {
          void store.refreshDir(root, "");
          continue;
        }
        const node = findNode(children, dir);
        // 仅刷新已加载目录（未加载目录展开时自会拉取新数据）
        if (node?.children !== undefined) void store.refreshDir(root, dir);
      }
    }, REFRESH_DEBOUNCE_MS);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [changeEntries, root]);

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
        // 目录链压缩后展示树中不存在中间目录 id：展开链尾（最深目录）节点
        const rawNode = findNode(useFileTreeStore.getState().trees[root].children ?? [], cur);
        const openId = rawNode?.type === "dir" ? chainTailPath(rawNode) : cur;
        if (treeRef.current?.get(openId)) treeRef.current?.open(openId);
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
        if (node.data.type === "dir") node.open();
        // 直接按行号换算偏移滚动（scrollTo 内部 waitFor 在部分 WebView 不可靠）
        const api = treeRef.current;
        if (api && node.rowIndex !== null) {
          api.scrollToOffset(Math.max(0, node.rowIndex * rowHeight - api.height / 2));
        }
      }
      useWorkbenchStore.getState().setFileTreeFocus(null);
    });
    return () => {
      cancelled = true;
    };
  }, [fileTreeFocus, root, revealPath, rowHeight]);

  // ------------------------------------------------------------------
  // 吸顶面包屑 + 滚动渐隐（直写 DOM/CSS 变量，滚动路径零 React 重渲染）
  // ------------------------------------------------------------------
  const [crumbs, setCrumbs] = useState<string[]>([]);
  const chromeRaf = useRef(0);

  const updateChrome = useCallback(() => {
    const api = treeRef.current;
    const mask = maskRef.current;
    const listEl = api?.listEl.current ?? null;
    if (!api || !listEl) {
      setCrumbs((c) => (c.length ? [] : c));
      return;
    }
    // 渐隐：顶部滚离 4px 才出现，底部接近末尾即消隐
    const offset = listEl.scrollTop;
    const remaining = listEl.scrollHeight - listEl.clientHeight - offset;
    if (mask) {
      mask.style.setProperty("--tree-fade-top", offset > 4 ? "14px" : "0px");
      mask.style.setProperty("--tree-fade-bottom", remaining > 4 ? "14px" : "0px");
    }
    // 吸顶面包屑：首个可见行的已展开祖先中滚出视口的部分
    const firstIndex = Math.max(0, Math.floor((offset - 6) / rowHeight));
    const first = api.visibleNodes[firstIndex];
    if (!first || first.level <= 0) {
      setCrumbs((c) => (c.length ? [] : c));
      return;
    }
    const segs = first.id.split("/").slice(0, -1);
    const out: string[] = [];
    let cur = "";
    for (const seg of segs) {
      cur = cur ? `${cur}/${seg}` : seg;
      const idx = api.idToIndex[cur];
      // 祖先行仍可见时无需吸顶；未展开（不在可见列表）的祖先跳过
      if (idx !== undefined && idx < firstIndex) out.push(cur);
    }
    setCrumbs((c) => (c.join() === out.join() ? c : out));
  }, [rowHeight]);

  const scheduleChromeUpdate = useCallback(() => {
    if (chromeRaf.current) return;
    chromeRaf.current = window.setTimeout(() => {
      chromeRaf.current = 0;
      updateChrome();
    }, 50);
  }, [updateChrome]);

  // 数据/尺寸变化同样影响可见行与滚动区间
  useEffect(() => {
    scheduleChromeUpdate();
  }, [displayData, size, scheduleChromeUpdate]);

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
        className="relative flex-1 min-h-0"
        onContextMenu={(e) => {
          // 行内右键已 stopPropagation，这里只处理空白区域
          e.preventDefault();
          openContextMenu(e, null);
        }}
      >
        {/* 吸顶面包屑：滚出视口的祖先目录，点击回跳 */}
        {crumbs.length > 0 && (
          <div className="absolute top-0 inset-x-0 z-10 flex items-center gap-0.5 h-6 px-2 bg-panel/90 backdrop-blur-sm border-b border-border text-[10px] text-muted overflow-hidden whitespace-nowrap">
            {crumbs.map((seg, i) => (
              <span key={seg} className="flex items-center gap-0.5 min-w-0">
                {i > 0 && <ChevronRight size={9} className="shrink-0 opacity-60" />}
                <button
                  className="hover:text-foreground transition-colors truncate max-w-24"
                  onClick={() => {
                    const api = treeRef.current;
                    const idx = api?.idToIndex[seg];
                    if (api && idx !== undefined) api.scrollToOffset(Math.max(0, idx * rowHeight - 6));
                  }}
                >
                  {seg.split("/").pop()}
                </button>
              </span>
            ))}
          </div>
        )}

        <div ref={maskRef} className="anelf-tree-mask h-full min-h-0">
          {tree.error && <p className="px-2 py-3 text-xs text-danger">{t("files.loadFailed")}</p>}
          {!tree.error && displayData === null && (
            <div className="flex items-center gap-2 px-2 py-3 text-xs text-muted">
              <Loader2 size={13} className="animate-spin" /> {t("files.loading")}
            </div>
          )}
          {!tree.error && displayData !== null && displayData.length === 0 && (
            <p className="px-2 py-3 text-xs text-muted">
              {changedOnly ? t("files.noChanges") : t("files.empty")}
            </p>
          )}
          {!tree.error && displayData !== null && size && (
            <Tree
              ref={treeRef}
              dndManager={sharedDndManager}
              data={displayData}
              idAccessor="path"
              childrenAccessor={treeChildren}
              renderRow={FileTreeRow}
              renderDragPreview={FileTreeDragPreview}
              openByDefault={false}
              width={size.width}
              height={size.height}
              rowHeight={rowHeight}
              indent={14}
              padding={6}
              onScroll={scheduleChromeUpdate}
              disableDrop={({ parentNode, dragNodes }) =>
                parentNode !== null &&
                (parentNode.isLeaf || dragNodes.some((d) => d.id === parentNode.id || d.isAncestorOf(parentNode)))
              }
              onToggle={(id) => {
                const node = treeRef.current?.get(id);
                if (!node || node.data.type !== "dir" || !node.isOpen) return;
                if (node.data.children?.length || node.data.has_children === false) return;
                void store.loadChildren(root, id).then(async () => {
                  // 链压缩：子级到位后链条可能延伸（新链尾 id 变更），跟随展开新链尾
                  await nextFrame();
                  const raw = findNode(useFileTreeStore.getState().trees[root].children ?? [], id);
                  if (!raw) return;
                  const tail = chainTailPath(raw);
                  if (tail !== id) treeRef.current?.open(tail);
                });
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
      </div>

      {menu && (
        <FileTreeContextMenu
          menu={menu}
          root={root}
          containerRef={boxRef}
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
