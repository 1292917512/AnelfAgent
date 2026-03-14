import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { memoryApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { cn } from "@/lib/utils";
import { Trash2, Save, Plus, Pencil, X, Search, Merge } from "lucide-react";

const LTM_PAGE_SIZE = 50;

export function LTMPanel() {
  const { t } = useTranslation("memory");
  const queryClient = useQueryClient();
  const [memType, setMemType] = useState("");
  const [page, setPage] = useState(1);
  const [searchQuery, setSearchQuery] = useState("");
  const [isSearching, setIsSearching] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editContent, setEditContent] = useState("");
  const [editImportance, setEditImportance] = useState(0.5);
  const [showCreate, setShowCreate] = useState(false);
  const [newContent, setNewContent] = useState("");
  const [newType, setNewType] = useState("semantic");
  const [newImportance, setNewImportance] = useState(0.5);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [mergeContent, setMergeContent] = useState("");
  const [showMerge, setShowMerge] = useState(false);
  const { data: paginated } = useQuery({
    queryKey: ["ltmPaginated", page, memType],
    queryFn: () => memoryApi.ltm.paginated(page, LTM_PAGE_SIZE, memType || undefined).then((r) => r.data),
    enabled: !isSearching,
  });

  const { data: searchResults } = useQuery({
    queryKey: ["ltmSearch", searchQuery],
    queryFn: () => memoryApi.ltm.search(searchQuery).then((r) => r.data),
    enabled: isSearching && searchQuery.length > 0,
  });

  const items: Record<string, unknown>[] = isSearching ? (searchResults || []) : (paginated?.items || []);
  const totalPages = paginated?.pages || 1;

  const deleteMutation = useMutation({ mutationFn: (id: number) => memoryApi.ltm.delete(id), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["ltmPaginated"] }) });
  const updateMutation = useMutation({
    mutationFn: ({ id, content, importance }: { id: number; content: string; importance: number }) => memoryApi.ltm.update(id, content, importance),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["ltmPaginated"] }); setEditingId(null); },
  });
  const createMutation = useMutation({
    mutationFn: () => memoryApi.ltm.create(newContent, newType, newImportance),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["ltmPaginated"] }); setShowCreate(false); setNewContent(""); },
  });
  const mergeMutation = useMutation({
    mutationFn: () => memoryApi.ltm.merge(Array.from(selectedIds), mergeContent),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["ltmPaginated"] }); setSelectedIds(new Set()); setShowMerge(false); setMergeContent(""); },
  });

  const toggleSelect = (id: number) => {
    const next = new Set(selectedIds);
    if (next.has(id)) next.delete(id); else next.add(id);
    setSelectedIds(next);
  };

  const handleSearch = () => {
    if (searchQuery.trim()) { setIsSearching(true); } else { setIsSearching(false); }
  };

  return (
    <div className="space-y-4">
      <Card title={t("ltmTitle")} subtitle={isSearching ? `${t("searchResults")}: ${items.length}` : `${paginated?.total || 0} · ${page}/${totalPages} ${t("pages")}`} actions={
        <div className="flex gap-2 items-center">
          <select value={memType} onChange={(e) => { setMemType(e.target.value); setPage(1); setIsSearching(false); }}
            className="bg-[var(--bg-elevated)] border border-[var(--input)] rounded-[var(--radius-md)] px-2 py-1 text-xs text-[var(--text)] outline-none">
            <option value="">{t("allTypes")}</option>
            <option value="episodic">{t("episodic")}</option>
            <option value="semantic">{t("semantic")}</option>
            <option value="entity">{t("entity")}</option>
            <option value="reflection">{t("reflection")}</option>
            <option value="permanent">{t("permanent")}</option>
          </select>
          {selectedIds.size >= 2 && (
            <button onClick={() => setShowMerge(true)} className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)] bg-[var(--ok-subtle)] text-[var(--ok)] border border-[var(--ok)]">
              <Merge size={14} /> {t("mergeSelected")} ({selectedIds.size})
            </button>
          )}
          <button onClick={() => setShowCreate(!showCreate)} className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)] bg-[var(--accent)] text-[var(--primary-foreground)] hover:bg-[var(--accent-hover)] transition-all"><Plus size={14} /> {t("common:create")}</button>
        </div>
      }>
        {/* Search bar */}
        <div className="flex gap-2 mb-3">
          <input value={searchQuery} onChange={(e) => { setSearchQuery(e.target.value); if (!e.target.value) setIsSearching(false); }}
            onKeyDown={(e) => e.key === "Enter" && handleSearch()}
            placeholder={t("searchMemory")}
            className="flex-1 bg-[var(--bg-elevated)] border border-[var(--input)] rounded-[var(--radius-md)] px-3 py-1.5 text-sm text-[var(--text)] outline-none focus:border-[var(--ring)]" />
          <button onClick={handleSearch} className="px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--bg-elevated)] text-[var(--muted)] hover:bg-[var(--bg-hover)]"><Search size={14} /></button>
          {isSearching && <button onClick={() => { setIsSearching(false); setSearchQuery(""); }} className="px-3 py-1.5 text-xs text-[var(--muted)]">{t("clearSearch")}</button>}
        </div>

        {/* Merge dialog */}
        {showMerge && (
          <div className="mb-4 p-3 rounded-[var(--radius-md)] bg-[var(--bg-elevated)] border border-[var(--ok)] space-y-2">
            <p className="text-xs text-[var(--muted)]">{t("mergePrompt", { count: selectedIds.size })} (ID: {Array.from(selectedIds).join(", ")})</p>
            <textarea value={mergeContent} onChange={(e) => setMergeContent(e.target.value)} rows={3} placeholder={t("mergeInputPlaceholder")}
              className="w-full bg-[var(--card)] border border-[var(--input)] rounded-[var(--radius-md)] px-3 py-2 text-sm text-[var(--text)] outline-none focus:border-[var(--ring)] resize-y" />
            <div className="flex justify-end gap-2">
              <button onClick={() => setShowMerge(false)} className="px-3 py-1 text-xs text-[var(--muted)]">{t("common:cancel")}</button>
              <button onClick={() => mergeContent && mergeMutation.mutate()} disabled={!mergeContent}
                className="px-3 py-1 text-xs font-medium rounded-[var(--radius-md)] bg-[var(--ok)] text-white disabled:opacity-50">{t("common:merge")}</button>
            </div>
          </div>
        )}

        {/* Create form */}
        {showCreate && (
          <div className="mb-4 p-3 rounded-[var(--radius-md)] bg-[var(--bg-elevated)] border border-[var(--accent)] space-y-2">
            <div className="flex gap-2">
              <select value={newType} onChange={(e) => setNewType(e.target.value)} className="bg-[var(--card)] border border-[var(--input)] rounded-[var(--radius-md)] px-2 py-1 text-xs text-[var(--text)] outline-none">
                <option value="semantic">{t("semantic")}</option><option value="episodic">{t("episodic")}</option><option value="permanent">{t("permanent")}</option><option value="reflection">{t("reflection")}</option>
              </select>
              <input type="number" step="0.1" min="0" max="1" value={newImportance} onChange={(e) => setNewImportance(Number(e.target.value))}
                className="w-20 bg-[var(--card)] border border-[var(--input)] rounded-[var(--radius-md)] px-2 py-1 text-xs text-[var(--text)] outline-none" placeholder={t("importanceLabel")} />
            </div>
            <textarea value={newContent} onChange={(e) => setNewContent(e.target.value)} rows={3} placeholder={t("memoryContent")}
              className="w-full bg-[var(--card)] border border-[var(--input)] rounded-[var(--radius-md)] px-3 py-2 text-sm text-[var(--text)] outline-none focus:border-[var(--ring)] resize-y" />
            <div className="flex justify-end gap-2">
              <button onClick={() => setShowCreate(false)} className="px-3 py-1 text-xs text-[var(--muted)]">{t("common:cancel")}</button>
              <button onClick={() => newContent && createMutation.mutate()} disabled={!newContent}
                className="px-3 py-1 text-xs font-medium rounded-[var(--radius-md)] bg-[var(--accent)] text-[var(--primary-foreground)] disabled:opacity-50">{t("common:create")}</button>
            </div>
          </div>
        )}

        {/* Memory list */}
        <div className="space-y-2 max-h-[500px] overflow-y-auto">
          {items.length === 0 && <p className="text-sm text-[var(--muted)]">{t("noMemory")}</p>}
          {items.map((item) => {
            const id = Number(item.id);
            const isEditing = editingId === id;
            const isSelected = selectedIds.has(id);
            const tags = (item.tags as string[]) || [];
            const accessCount = Number(item.access_count || 0);
            return (
              <div key={id} className={cn("p-3 rounded-[var(--radius-md)] border transition-all", isSelected ? "bg-[var(--ok-subtle)] border-[var(--ok)]" : "bg-[var(--bg-elevated)] border-[var(--border)]")}>
                <div className="flex items-start justify-between gap-2 mb-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <input type="checkbox" checked={isSelected} onChange={() => toggleSelect(id)} className="rounded" />
                    <span className="text-[11px] px-2 py-0.5 rounded-full border border-[var(--border)] text-[var(--muted)]">{String(item.type ?? "unknown")}</span>
                    <span className="text-[11px] text-[var(--muted)]">#{id}</span>
                    <span className="text-[11px] text-[var(--muted)]">{t("importanceLabel")}: {String(item.importance ?? "—")}</span>
                    {accessCount > 0 && <span className="text-[11px] text-[var(--muted)]">{t("accessLabel")}: {accessCount}</span>}
                    {!!item.source && <span className="text-[10px] px-1.5 py-0.5 rounded bg-[var(--secondary)] text-[var(--muted)]">{String(item.source)}</span>}
                  </div>
                  <div className="flex gap-1">
                    <button onClick={() => { if (isEditing) { setEditingId(null); } else { setEditingId(id); setEditContent(String(item.content ?? "")); setEditImportance(Number(item.importance ?? 0.5)); } }}
                      className="p-1 text-[var(--muted)] hover:text-[var(--accent)] transition-colors">{isEditing ? <X size={14} /> : <Pencil size={14} />}</button>
                    <button onClick={() => deleteMutation.mutate(id)} className="p-1 text-[var(--muted)] hover:text-[var(--danger)] transition-colors"><Trash2 size={14} /></button>
                  </div>
                </div>
                {tags.length > 0 && (
                  <div className="flex gap-1 flex-wrap mb-1">
                    {tags.map((tg) => <span key={tg} className="text-[10px] px-1.5 py-0.5 rounded-full bg-[var(--accent-subtle)] text-[var(--accent)]">{tg}</span>)}
                  </div>
                )}
                {isEditing ? (
                  <div className="space-y-2 mt-2">
                    <textarea value={editContent} onChange={(e) => setEditContent(e.target.value)} rows={3}
                      className="w-full bg-[var(--card)] border border-[var(--input)] rounded-[var(--radius-md)] px-3 py-2 text-sm text-[var(--text)] outline-none focus:border-[var(--ring)] resize-y" />
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-[var(--muted)]">{t("importanceLabel")}:</span>
                      <input type="number" step="0.1" min="0" max="1" value={editImportance} onChange={(e) => setEditImportance(Number(e.target.value))}
                        className="w-20 bg-[var(--card)] border border-[var(--input)] rounded-[var(--radius-md)] px-2 py-1 text-xs text-[var(--text)] outline-none" />
                      <button onClick={() => updateMutation.mutate({ id, content: editContent, importance: editImportance })}
                        className="ml-auto flex items-center gap-1 px-3 py-1 text-xs font-medium rounded-[var(--radius-md)] bg-[var(--accent)] text-[var(--primary-foreground)]"><Save size={12} /> {t("common:save")}</button>
                    </div>
                  </div>
                ) : (
                  <p className="text-sm text-[var(--text)]">{String(item.content ?? item.snippet ?? "")}</p>
                )}
              </div>
            );
          })}
        </div>

        {/* Pagination */}
        {!isSearching && totalPages > 1 && (
          <div className="flex items-center justify-center gap-2 mt-4">
            <button onClick={() => setPage(Math.max(1, page - 1))} disabled={page <= 1}
              className="px-3 py-1 text-xs rounded-[var(--radius-md)] border border-[var(--border)] text-[var(--muted)] disabled:opacity-30">{t("common:prev")}</button>
            <span className="text-xs text-[var(--muted)]">{page} / {totalPages}</span>
            <button onClick={() => setPage(Math.min(totalPages, page + 1))} disabled={page >= totalPages}
              className="px-3 py-1 text-xs rounded-[var(--radius-md)] border border-[var(--border)] text-[var(--muted)] disabled:opacity-30">{t("common:next")}</button>
          </div>
        )}
      </Card>
    </div>
  );
}
