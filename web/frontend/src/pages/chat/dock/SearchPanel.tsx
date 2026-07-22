import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Brain, FileText, MessagesSquare, ScrollText, Search } from "lucide-react";
import { searchApi, type GlobalSearchResult } from "@/lib/api";
import { Input } from "@/components/ui";
import { useWorkbenchStore } from "@/stores/workbench-store";

type ResultGroup = "memory" | "logs" | "files" | "conversations";

const GROUP_ICONS: Record<ResultGroup, typeof Brain> = {
  memory: Brain,
  logs: ScrollText,
  files: FileText,
  conversations: MessagesSquare,
};

/** 全局搜索面板：聚合记忆 / 日志 / 文件 / 会话 */
export function SearchPanel() {
  const { t } = useTranslation("workbench");
  const searchSeed = useWorkbenchStore((s) => s.searchSeed);
  const setSearchSeed = useWorkbenchStore((s) => s.setSearchSeed);
  const openFile = useWorkbenchStore((s) => s.openFile);
  const [query, setQuery] = useState(searchSeed);
  const [result, setResult] = useState<GlobalSearchResult | null>(null);
  const [loading, setLoading] = useState(false);

  const runSearch = async (q: string) => {
    if (!q.trim()) return;
    setLoading(true);
    try {
      const r = await searchApi.global(q.trim());
      setResult(r.data);
    } catch {
      setResult(null);
    } finally {
      setLoading(false);
    }
  };

  // AI ui_open_panel(search, payload) 预填搜索词
  useEffect(() => {
    if (searchSeed) {
      setQuery(searchSeed);
      setSearchSeed("");
      runSearch(searchSeed);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchSeed]);

  const groups: { key: ResultGroup; count: number }[] = [
    { key: "memory", count: result?.memory.length ?? 0 },
    { key: "conversations", count: result?.conversations.length ?? 0 },
    { key: "logs", count: result?.logs.length ?? 0 },
    { key: "files", count: result?.files.length ?? 0 },
  ];

  return (
    <div className="flex flex-col h-full">
      <div className="p-3 border-b border-border shrink-0">
        <form
          onSubmit={(e) => { e.preventDefault(); runSearch(query); }}
          className="relative"
        >
          <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("search.placeholder")}
            className="pl-8"
          />
        </form>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-4">
        {loading && <p className="text-xs text-muted">{t("search.searching")}</p>}
        {!loading && !result && <p className="text-xs text-muted">{t("search.hint")}</p>}
        {!loading && result && groups.every((g) => g.count === 0) && (
          <p className="text-xs text-muted">{t("search.noResults")}</p>
        )}

        {result && groups.filter((g) => g.count > 0).map(({ key }) => {
          const Icon = GROUP_ICONS[key];
          return (
            <section key={key} className="space-y-1.5">
              <h4 className="flex items-center gap-1.5 text-[11px] font-semibold text-heading">
                <Icon size={12} /> {t(`search.groups.${key}`)}
              </h4>
              <div className="space-y-1">
                {key === "memory" && result.memory.map((m) => (
                  <div key={m.id} className="rounded-md border border-border bg-card px-2.5 py-1.5">
                    <div className="text-[11px] text-foreground line-clamp-2 break-all">{m.snippet}</div>
                    <div className="text-[10px] text-muted mt-0.5">
                      {m.memory_type}{m.tags.length > 0 && ` · ${m.tags.join(", ")}`}
                    </div>
                  </div>
                ))}
                {key === "conversations" && result.conversations.map((c) => (
                  <div key={c.id} className="rounded-md border border-border bg-card px-2.5 py-1.5">
                    <div className="text-[11px] text-foreground line-clamp-2 break-all">{c.snippet}</div>
                    <div className="text-[10px] text-muted mt-0.5">{c.scope} · {c.role} · {c.time}</div>
                  </div>
                ))}
                {key === "logs" && result.logs.map((l, i) => (
                  <div key={`${l.time}-${i}`} className="rounded-md border border-border bg-card px-2.5 py-1.5">
                    <div className="text-[11px] text-foreground line-clamp-2 break-all">{l.message}</div>
                    <div className="text-[10px] text-muted mt-0.5">{l.level}{l.tag && ` · ${l.tag}`} · {l.time}</div>
                  </div>
                ))}
                {key === "files" && result.files.map((f) => (
                  <button
                    key={f.path}
                    onClick={() => openFile(f.path)}
                    className="w-full text-left rounded-md border border-border bg-card px-2.5 py-1.5 hover:border-accent/50 transition-colors"
                  >
                    <div className="text-[11px] text-accent truncate">{f.path}</div>
                    {f.snippet && (
                      <div className="text-[10px] text-muted line-clamp-1 break-all mt-0.5">{f.snippet}</div>
                    )}
                  </button>
                ))}
              </div>
            </section>
          );
        })}
      </div>
    </div>
  );
}
