import { useState, useMemo } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { tagsApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { UnifiedTag } from "@/lib/types";
import { Tag, Plus, Trash2, Lock, Search, X, MessageSquare, Wrench } from "lucide-react";

interface CreateForm {
  name: string;
  description: string;
}

function SourceBadge({ source, t }: { source: "message" | "tool"; t: (k: string) => string }) {
  if (source === "message") {
    return (
      <span
        title={t("sourceMessageTooltip")}
        className="inline-flex items-center gap-0.5 text-[9px] px-1.5 py-0.5 rounded-full
          bg-blue-500/10 text-blue-400 border border-blue-500/20 cursor-help"
      >
        <MessageSquare size={8} />
        {t("sourceMessage")}
      </span>
    );
  }
  return (
    <span
      title={t("sourceToolTooltip")}
      className="inline-flex items-center gap-0.5 text-[9px] px-1.5 py-0.5 rounded-full
        bg-[var(--secondary)] text-[var(--muted)] border border-[var(--border)] cursor-help"
    >
      <Wrench size={8} />
      {t("sourceTool")}
    </span>
  );
}

function TagCard({
  tag,
  onDelete,
  t,
}: {
  tag: UnifiedTag;
  onDelete?: () => void;
  t: (k: string, opts?: Record<string, unknown>) => string;
}) {
  return (
    <div
      className={cn(
        "group flex flex-col gap-1.5 p-3 rounded-[var(--radius-md)] border transition-colors",
        tag.builtin
          ? "border-[var(--border)] bg-[var(--secondary)] hover:border-[var(--border-strong)]"
          : "border-[var(--accent)]/30 bg-[var(--accent)]/5 hover:border-[var(--accent)]/50",
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          {/* Name + badges */}
          <div className="flex items-center gap-1.5 flex-wrap">
            <code className="text-xs font-mono font-semibold text-[var(--text-strong)]">
              [{tag.name}]
            </code>
            {tag.builtin ? (
              <span
                title={t("builtinTooltip")}
                className="inline-flex items-center gap-0.5 text-[9px] px-1.5 py-0.5 rounded-full
                  bg-[var(--secondary)] text-[var(--muted)] border border-[var(--border)] cursor-help"
              >
                <Lock size={8} />
                {t("builtin")}
              </span>
            ) : (
              <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-[var(--accent)]/10 text-[var(--accent)] border border-[var(--accent)]/20">
                {t("custom")}
              </span>
            )}
            {tag.sources
              .filter((s) => s !== "custom")
              .map((s) => (
                <SourceBadge key={s} source={s as "message" | "tool"} t={t} />
              ))}
          </div>
          {/* Description */}
          {tag.description ? (
            <p className="text-[11px] text-[var(--muted)] mt-1 leading-relaxed">
              {tag.description}
            </p>
          ) : (
            <p className="text-[11px] text-[var(--muted)]/50 mt-1 italic">—</p>
          )}
        </div>
        {/* Delete button for custom tags */}
        {!tag.builtin && onDelete && (
          <button
            onClick={onDelete}
            className="opacity-0 group-hover:opacity-100 flex-shrink-0 p-1 rounded
              text-[var(--muted)] hover:text-red-400 hover:bg-red-400/10 transition-all"
            title={t("common:delete")}
          >
            <Trash2 size={13} />
          </button>
        )}
      </div>
    </div>
  );
}

export default function Tags() {
  const { t } = useTranslation(["tags", "common"]);
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState<CreateForm>({ name: "", description: "" });
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);

  const { data: allTags = [], isLoading } = useQuery<UnifiedTag[]>({
    queryKey: ["unified-tags"],
    queryFn: () => tagsApi.unified().then((r) => r.data),
  });

  const createMut = useMutation({
    mutationFn: () =>
      tagsApi.createMessageTag(form.name.trim(), form.description.trim()),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["unified-tags"] });
      setShowCreate(false);
      setForm({ name: "", description: "" });
    },
  });

  const deleteMut = useMutation({
    mutationFn: (name: string) => tagsApi.deleteMessageTag(name),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["unified-tags"] });
      setDeleteConfirm(null);
    },
  });

  const kw = search.toLowerCase();
  const filtered = useMemo(() => {
    if (!kw) return allTags;
    return allTags.filter(
      (tag) =>
        tag.name.toLowerCase().includes(kw) ||
        tag.description.toLowerCase().includes(kw),
    );
  }, [allTags, kw]);

  const builtinTags = filtered.filter((t) => t.builtin);
  const customTags = filtered.filter((t) => !t.builtin);
  const totalBuiltin = allTags.filter((t) => t.builtin).length;
  const totalCustom = allTags.filter((t) => !t.builtin).length;

  return (
    <div className="space-y-6 max-w-5xl">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-1.5">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs px-2 py-0.5 rounded-full bg-[var(--secondary)] text-[var(--muted)] border border-[var(--border)]">
              {t("statsTotal", { count: allTags.length })}
            </span>
            <span className="text-xs px-2 py-0.5 rounded-full bg-[var(--secondary)] text-[var(--muted)] border border-[var(--border)]">
              {t("statsBuiltin", { count: totalBuiltin })}
            </span>
            {totalCustom > 0 && (
              <span className="text-xs px-2 py-0.5 rounded-full bg-[var(--accent)]/10 text-[var(--accent)] border border-[var(--accent)]/20">
                {t("statsCustom", { count: totalCustom })}
              </span>
            )}
          </div>
          <p className="text-xs text-[var(--muted)] max-w-xl">{t("subtitle")}</p>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)]
            bg-[var(--accent)] text-white hover:opacity-90 transition-all flex-shrink-0"
        >
          <Plus size={14} />
          {t("createTag")}
        </button>
      </div>

      {/* Search */}
      <div className="relative">
        <Search
          size={16}
          className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--muted)]"
        />
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={t("searchPlaceholder")}
          className="w-full pl-9 pr-3 py-2 text-sm rounded-[var(--radius-md)]
            border border-[var(--border)] bg-[var(--bg-elevated)] text-[var(--text)]
            placeholder:text-[var(--muted)] focus:outline-none focus:border-[var(--accent)]"
        />
      </div>

      {isLoading ? (
        <p className="text-sm text-[var(--muted)]">{t("common:loading")}</p>
      ) : filtered.length === 0 ? (
        <p className="text-sm text-[var(--muted)] text-center py-10">
          {search ? t("noMatch") : t("noTags")}
        </p>
      ) : (
        <div className="space-y-6">
          {/* Builtin tags */}
          {builtinTags.length > 0 && (
            <section className="space-y-2">
              <div className="flex items-center gap-2">
                <Lock size={13} className="text-[var(--muted)]" />
                <h2 className="text-xs font-semibold uppercase tracking-wider text-[var(--muted-strong)]">
                  {t("sectionBuiltin")}
                </h2>
                <span className="text-[11px] text-[var(--muted)]">
                  — {t("sectionBuiltinDesc")}
                </span>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
                {builtinTags.map((tag) => (
                  <TagCard key={tag.name} tag={tag} t={t} />
                ))}
              </div>
            </section>
          )}

          {/* Custom tags */}
          <section className="space-y-2">
            <div className="flex items-center gap-2">
              <Tag size={13} className="text-[var(--accent)]" />
              <h2 className="text-xs font-semibold uppercase tracking-wider text-[var(--muted-strong)]">
                {t("sectionCustom")}
              </h2>
              <span className="text-[11px] text-[var(--muted)]">
                — {t("sectionCustomDesc")}
              </span>
            </div>
            {customTags.length === 0 ? (
              <div className="rounded-[var(--radius-md)] border border-dashed border-[var(--border)] p-6 text-center">
                <p className="text-xs text-[var(--muted)]">{t("noCustomTags")}</p>
              </div>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
                {customTags.map((tag) => (
                  <TagCard
                    key={tag.name}
                    tag={tag}
                    onDelete={() => setDeleteConfirm(tag.name)}
                    t={t}
                  />
                ))}
              </div>
            )}
          </section>
        </div>
      )}

      {/* Create Modal */}
      {showCreate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="bg-[var(--card)] rounded-[var(--radius-lg,12px)] border border-[var(--border)] shadow-xl w-full max-w-md mx-4 p-6">
            <div className="flex items-center justify-between mb-5">
              <div className="flex items-center gap-2">
                <Plus size={18} className="text-[var(--accent)]" />
                <h3 className="text-base font-semibold text-[var(--text-strong)]">
                  {t("createTagTitle")}
                </h3>
              </div>
              <button
                onClick={() => { setShowCreate(false); setForm({ name: "", description: "" }); }}
                className="text-[var(--muted)] hover:text-[var(--text)] transition-colors"
              >
                <X size={18} />
              </button>
            </div>

            <div className="space-y-4">
              <div>
                <label className="block text-xs font-medium text-[var(--muted)] mb-1">
                  {t("tagName")}
                </label>
                <input
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  placeholder={t("tagNamePlaceholder")}
                  className="w-full px-3 py-2 text-sm font-mono rounded-[var(--radius-md)]
                    border border-[var(--border)] bg-[var(--bg-elevated)] text-[var(--text)]
                    placeholder:text-[var(--muted)] focus:outline-none focus:border-[var(--accent)]"
                />
                <p className="text-[10px] text-[var(--muted)] mt-1">{t("tagNameHint")}</p>
              </div>
              <div>
                <label className="block text-xs font-medium text-[var(--muted)] mb-1">
                  {t("tagDescription")}
                </label>
                <textarea
                  value={form.description}
                  onChange={(e) => setForm({ ...form, description: e.target.value })}
                  placeholder={t("tagDescriptionPlaceholder")}
                  rows={3}
                  className="w-full px-3 py-2 text-sm rounded-[var(--radius-md)]
                    border border-[var(--border)] bg-[var(--bg-elevated)] text-[var(--text)]
                    placeholder:text-[var(--muted)] focus:outline-none focus:border-[var(--accent)] resize-none"
                />
              </div>
              {form.name && (
                <div className="p-2.5 rounded-[var(--radius-md)] bg-[var(--secondary)] border border-[var(--border)]">
                  <p className="text-[10px] text-[var(--muted)] mb-1">预览</p>
                  <code className="text-xs font-mono text-[var(--text-strong)]">
                    [{form.name}:值]
                  </code>
                </div>
              )}
            </div>

            <div className="flex justify-end gap-2 mt-5">
              <button
                onClick={() => { setShowCreate(false); setForm({ name: "", description: "" }); }}
                className="px-4 py-1.5 text-xs font-medium rounded-[var(--radius-md)]
                  border border-[var(--border)] text-[var(--text)] hover:bg-[var(--bg-hover)] transition-all"
              >
                {t("common:cancel")}
              </button>
              <button
                onClick={() => createMut.mutate()}
                disabled={!form.name.trim() || createMut.isPending}
                className="px-4 py-1.5 text-xs font-medium rounded-[var(--radius-md)]
                  bg-[var(--accent)] text-white hover:opacity-90 transition-all disabled:opacity-50"
              >
                {createMut.isPending ? t("common:saving") : t("common:create")}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete Confirm */}
      {deleteConfirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="bg-[var(--card)] rounded-[var(--radius-lg,12px)] border border-[var(--border)] shadow-xl w-full max-w-sm mx-4 p-6">
            <div className="flex items-center gap-3 mb-4">
              <div className="w-9 h-9 rounded-full bg-red-500/10 flex items-center justify-center flex-shrink-0">
                <Trash2 size={16} className="text-red-400" />
              </div>
              <p className="text-sm text-[var(--text)]">
                {t("deleteConfirm", { name: deleteConfirm })}
              </p>
            </div>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setDeleteConfirm(null)}
                className="px-4 py-1.5 text-xs font-medium rounded-[var(--radius-md)]
                  border border-[var(--border)] text-[var(--text)] hover:bg-[var(--bg-hover)] transition-all"
              >
                {t("common:cancel")}
              </button>
              <button
                onClick={() => deleteMut.mutate(deleteConfirm)}
                disabled={deleteMut.isPending}
                className="px-4 py-1.5 text-xs font-medium rounded-[var(--radius-md)]
                  bg-red-500 text-white hover:bg-red-600 transition-all disabled:opacity-50"
              >
                {deleteMut.isPending ? t("common:saving") : t("common:delete")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
