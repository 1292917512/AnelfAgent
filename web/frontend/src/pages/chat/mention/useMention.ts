/** 提及面板状态（输入框 @ 触发，防抖搜索 workspace 文件） */

import { useEffect, useRef, useState } from "react";
import { workspaceApi } from "@/lib/api";
import type { WorkspaceSearchHit } from "@/lib/types";

export interface MentionState {
  /** 面板是否打开 */
  open: boolean;
  /** @ 后的查询词（不含 @） */
  query: string;
  /** @ 在文本中的起始下标（选中后按 [start, cursor) 替换） */
  start: number;
  /** 候选列表 */
  items: WorkspaceSearchHit[];
  loading: boolean;
  /** 键盘高亮下标 */
  activeIndex: number;
}

const DEBOUNCE_MS = 200;
const MAX_ITEMS = 30;

/** 从光标位置向前找最近的 @ 触发词（@ 前须是行首/空白，防邮箱等误触发） */
export function detectMention(text: string, cursor: number): { query: string; start: number } | null {
  const head = text.slice(0, cursor);
  const at = head.lastIndexOf("@");
  if (at < 0) return null;
  const prev = at > 0 ? head[at - 1] : "";
  if (prev && !/\s/.test(prev)) return null;
  const query = head.slice(at + 1);
  // 查询词里出现空白/换行即退出提及态
  if (/[\s]/.test(query)) return null;
  return { query, start: at };
}

/** 提及搜索 Hook：query 变化防抖调用 workspace 搜索，丢弃陈旧结果 */
export function useMentionSearch(query: string, open: boolean) {
  const [items, setItems] = useState<WorkspaceSearchHit[]>([]);
  const [loading, setLoading] = useState(false);
  const pendingRef = useRef("");

  useEffect(() => {
    if (!open) {
      setItems([]);
      setLoading(false);
      return;
    }
    pendingRef.current = query;
    setLoading(true);
    const timer = setTimeout(() => {
      workspaceApi.search(query, MAX_ITEMS).then((r) => {
        // 丢弃陈旧结果（query 已变）
        if (pendingRef.current !== query) return;
        setItems(r.data.files ?? []);
        setLoading(false);
      }).catch(() => setLoading(false));
    }, DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [query, open]);

  return { items, loading };
}
