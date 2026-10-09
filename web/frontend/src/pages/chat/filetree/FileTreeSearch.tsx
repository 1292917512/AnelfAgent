import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { workspaceApi } from "@/lib/api";
import type { WorkspaceRoot } from "@/lib/types";
import { useWorkbenchStore } from "@/stores/workbench-store";
import { QueryError } from "@/components/common/AsyncState";
import { cn } from "@/lib/utils";
import { fileIcon } from "./file-tree-utils";

export function FileTreeSearch({ root, onDone }: { root: WorkspaceRoot; onDone: () => void }) {
  const { t } = useTranslation("workbench");
  const [input, setInput] = useState("");
  const [query, setQuery] = useState("");
  useEffect(() => {
    const timer = setTimeout(() => setQuery(input.trim()), 300);
    return () => clearTimeout(timer);
  }, [input]);
  const search = useQuery({
    queryKey: ["workspace-search", root, query],
    queryFn: ({ signal }) => workspaceApi.search(query, 30, root, signal).then((response) => response.data.files),
    enabled: !!query && query === input.trim(), throwOnError: false,
  });
  const waiting = input.trim() !== query || search.isFetching;
  const hits = !waiting && query ? search.data : undefined;
  return <div className="flex h-full min-h-0 flex-col">
    <div className="shrink-0 border-b border-border px-2 py-2">
      <input autoFocus value={input} onChange={(event) => setInput(event.target.value)}
        aria-label={t("files.searchPlaceholder")} placeholder={t("files.searchPlaceholder")}
        className="w-full rounded-lg border border-input bg-card px-2.5 py-2 text-xs outline-none focus:border-accent" />
    </div>
    <div className="min-h-0 flex-1 overflow-y-auto p-2">
      {waiting && <p role="status" className="flex items-center gap-2 px-2 py-3 text-xs text-muted"><Loader2 size={13} className="animate-spin" />{t("files.loading")}</p>}
      {!waiting && search.error && <QueryError compact error={search.error} retry={() => void search.refetch()} />}
      {!query && !waiting && <p className="p-2 text-xs text-muted">{t("files.searchHint")}</p>}
      {hits?.length === 0 && <p className="p-2 text-xs text-muted">{t("files.searchEmpty")}</p>}
      {hits?.map((hit) => {
        const { Icon, className } = fileIcon(hit.name);
        return <button key={`${hit.match}:${hit.path}`} className="flex w-full min-w-0 flex-col gap-1 rounded-lg p-2 text-left hover:bg-hover"
          onClick={() => { const state = useWorkbenchStore.getState(); state.openFile(hit.path, root); state.setFileTreeFocus(hit.path); onDone(); }}>
          <span className="flex w-full items-center gap-2 text-xs"><Icon size={14} className={cn("shrink-0", className)} /><span className="truncate">{hit.name}</span></span>
          <span className="w-full truncate pl-5 text-[11px] text-muted">{hit.snippet ?? hit.path}</span>
        </button>;
      })}
    </div>
  </div>;
}
