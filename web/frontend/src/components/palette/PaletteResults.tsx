import { Command } from "cmdk";
import { useTranslation } from "react-i18next";
import { ArrowRight, Brain, FileText, MessageSquare } from "lucide-react";
import type { GlobalSearchResult } from "@/lib/types";
import { groupCls, itemCls } from "./paletteStyles";

/** 命令面板的全局搜索结果分组（记忆 / 会话 / 文件 + 打开搜索面板） */
export function PaletteResults({
  results,
  query,
  onGo,
  onOpenSearchPanel,
}: {
  results: GlobalSearchResult;
  query: string;
  onGo: (path: string) => void;
  onOpenSearchPanel: (q: string) => void;
}) {
  const { t } = useTranslation("palette");
  return (
    <Command.Group heading={t("group_results")} className={groupCls}>
      {results.memory.slice(0, 3).map((m) => (
        <Command.Item
          key={`mem-${m.id}`}
          value={`memory ${m.snippet.slice(0, 60)}`}
          className={itemCls}
          onSelect={() => onGo("/memory")}
        >
          <Brain size={15} className="shrink-0 text-muted" />
          <span className="truncate">{m.snippet}</span>
          <span className="ml-auto shrink-0 text-[10px] text-muted">
            {t("result_memory")}
          </span>
        </Command.Item>
      ))}
      {results.conversations.slice(0, 3).map((c) => (
        <Command.Item
          key={`conv-${c.id}`}
          value={`conversation ${c.snippet.slice(0, 60)}`}
          className={itemCls}
          onSelect={() => onOpenSearchPanel(query)}
        >
          <MessageSquare size={15} className="shrink-0 text-muted" />
          <span className="truncate">{c.snippet}</span>
          <span className="ml-auto shrink-0 text-[10px] text-muted">
            {t("result_conversation")}
          </span>
        </Command.Item>
      ))}
      {results.files.slice(0, 3).map((f, i) => (
        <Command.Item
          key={`file-${f.path}-${i}`}
          value={`file ${f.path}`}
          className={itemCls}
          onSelect={() => onOpenSearchPanel(query)}
        >
          <FileText size={15} className="shrink-0 text-muted" />
          <span className="truncate">{f.path}</span>
          <span className="ml-auto shrink-0 text-[10px] text-muted">
            {t("result_file")}
          </span>
        </Command.Item>
      ))}
      <Command.Item
        value={`search-all ${query}`}
        className={itemCls}
        onSelect={() => onOpenSearchPanel(query)}
      >
        <ArrowRight size={15} className="shrink-0 text-muted" />
        <span>{t("open_search", { query })}</span>
      </Command.Item>
    </Command.Group>
  );
}
