import { useState, useMemo } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { tagsApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { UnifiedTag } from "@/lib/types";
import { PageContainer } from "@/components/common/PageContainer";
import { Button, ConfirmDialog, Input, LoadingBlock, Modal, Textarea } from "@/components/ui";
import { Tag, Plus, Trash2, Lock, Search, MessageSquare, Wrench } from "lucide-react";

function SourceBadge({ source, t }: { source: "message" | "tool"; t: (k: string) => string }) {
  if (source === "message") {
    return (
      <span
        title={t("sourceMessageTooltip")}
        className="inline-flex items-center gap-0.5 text-[9px] px-1.5 py-0.5 rounded-full
          bg-accent-subtle text-info border border-info/20 cursor-help"
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
        bg-secondary text-muted border border-border cursor-help"
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
        "group flex flex-col gap-1.5 p-3 rounded-md border transition-colors",
        tag.builtin
          ? "border-border bg-secondary hover:border-border-strong"
          : "border-accent/30 bg-accent/5 hover:border-accent/50",
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5 flex-wrap">
            <code className="text-xs font-mono font-semibold text-heading">
              [{tag.name}]
            </code>
            {tag.builtin ? (
              <span
                title={t("builtinTooltip")}
                className="inline-flex items-center gap-0.5 text-[9px] px-1.5 py-0.5 rounded-full
                  bg-secondary text-muted border border-border cursor-help"
              >
                <Lock size={8} />
                {t("builtin")}
              </span>
            ) : (
              <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-accent/10 text-accent border border-accent/20">
                {t("custom")}
              </span>
            )}
            {tag.sources
              .filter((s) => s !== "custom")
              .map((s) => (
                <SourceBadge key={s} source={s as "message" | "tool"} t={t} />
              ))}
          </div>
          {tag.description ? (
            <p className="text-[11px] text-muted mt-1 leading-relaxed">
              {tag.description}
            </p>
          ) : (
            <p className="text-[11px] text-muted/50 mt-1 italic">—</p>
          )}
        </div>
        {/* 删除按钮（触屏常显） */}
        {!tag.builtin && onDelete && (
          <button
            onClick={onDelete}
            className="opacity-100 md:opacity-0 md:group-hover:opacity-100 flex-shrink-0 p-1 rounded
              text-muted hover:text-danger hover:bg-danger/10 transition-all"
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
  const [form, setForm] = useState({ name: "", description: "" });
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

  const builtinTags = filtered.filter((tag) => tag.builtin);
  const customTags = filtered.filter((tag) => !tag.builtin);
  const totalBuiltin = allTags.filter((tag) => tag.builtin).length;
  const totalCustom = allTags.filter((tag) => !tag.builtin).length;

  const closeCreate = () => {
    setShowCreate(false);
    setForm({ name: "", description: "" });
  };

  return (
    <PageContainer>
      {/* 统计 + 创建 */}
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-1.5 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs px-2 py-0.5 rounded-full bg-secondary text-muted border border-border">
              {t("statsTotal", { count: allTags.length })}
            </span>
            <span className="text-xs px-2 py-0.5 rounded-full bg-secondary text-muted border border-border">
              {t("statsBuiltin", { count: totalBuiltin })}
            </span>
            {totalCustom > 0 && (
              <span className="text-xs px-2 py-0.5 rounded-full bg-accent/10 text-accent border border-accent/20">
                {t("statsCustom", { count: totalCustom })}
              </span>
            )}
          </div>
          <p className="text-xs text-muted max-w-xl">{t("subtitle")}</p>
        </div>
        <Button variant="primary" size="sm" onClick={() => setShowCreate(true)} className="shrink-0">
          <Plus size={14} />
          {t("createTag")}
        </Button>
      </div>

      {/* 搜索 */}
      <div className="relative">
        <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
        <Input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={t("searchPlaceholder")}
          className="pl-9"
        />
      </div>

      {isLoading ? (
        <LoadingBlock label={t("common:loading")} />
      ) : filtered.length === 0 ? (
        <p className="text-sm text-muted text-center py-10">
          {search ? t("noMatch") : t("noTags")}
        </p>
      ) : (
        <div className="space-y-6">
          {/* 内置标签 */}
          {builtinTags.length > 0 && (
            <section className="space-y-2">
              <div className="flex items-center gap-2 flex-wrap">
                <Lock size={13} className="text-muted" />
                <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-strong">
                  {t("sectionBuiltin")}
                </h2>
                <span className="text-[11px] text-muted">
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

          {/* 自定义标签 */}
          <section className="space-y-2">
            <div className="flex items-center gap-2 flex-wrap">
              <Tag size={13} className="text-accent" />
              <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-strong">
                {t("sectionCustom")}
              </h2>
              <span className="text-[11px] text-muted">
                — {t("sectionCustomDesc")}
              </span>
            </div>
            {customTags.length === 0 ? (
              <div className="rounded-md border border-dashed border-border p-6 text-center">
                <p className="text-xs text-muted">{t("noCustomTags")}</p>
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

      {/* 创建弹窗 */}
      <Modal
        open={showCreate}
        onClose={closeCreate}
        width="max-w-md"
        title={
          <span className="flex items-center gap-2">
            <Plus size={18} className="text-accent" />
            {t("createTagTitle")}
          </span>
        }
        footer={
          <>
            <Button variant="secondary" size="sm" onClick={closeCreate}>
              {t("common:cancel")}
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={() => createMut.mutate()}
              disabled={!form.name.trim()}
              loading={createMut.isPending}
            >
              {createMut.isPending ? t("common:saving") : t("common:create")}
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-muted mb-1">{t("tagName")}</label>
            <Input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder={t("tagNamePlaceholder")}
              className="font-mono"
            />
            <p className="text-[10px] text-muted mt-1">{t("tagNameHint")}</p>
          </div>
          <div>
            <label className="block text-xs font-medium text-muted mb-1">{t("tagDescription")}</label>
            <Textarea
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              placeholder={t("tagDescriptionPlaceholder")}
              rows={3}
              className="resize-none"
            />
          </div>
          {form.name && (
            <div className="p-2.5 rounded-md bg-secondary border border-border">
              <p className="text-[10px] text-muted mb-1">{t("preview")}</p>
              <code className="text-xs font-mono text-heading">
                [{form.name}:{t("previewValue")}]
              </code>
            </div>
          )}
        </div>
      </Modal>

      {/* 删除确认 */}
      <ConfirmDialog
        open={!!deleteConfirm}
        onClose={() => setDeleteConfirm(null)}
        onConfirm={() => deleteConfirm && deleteMut.mutate(deleteConfirm)}
        title={t("common:delete")}
        message={t("deleteConfirm", { name: deleteConfirm ?? "" })}
        confirmText={deleteMut.isPending ? t("common:saving") : t("common:delete")}
        cancelText={t("common:cancel")}
        danger
        loading={deleteMut.isPending}
      />
    </PageContainer>
  );
}
