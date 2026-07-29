import { useState, useMemo } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { tagsApi } from "@/lib/api";
import type { UnifiedTag } from "@/lib/types";
import { PageContainer } from "@/components/common/PageContainer";
import { Button, ConfirmDialog, Input, LoadingBlock } from "@/components/ui";
import { Tag, Plus, Lock, Search } from "lucide-react";
import { TagCard } from "./tags/TagCard";
import { CreateTagModal } from "./tags/CreateTagModal";

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

      <CreateTagModal
        open={showCreate}
        onClose={closeCreate}
        form={form}
        setForm={setForm}
        pending={createMut.isPending}
        onSubmit={() => createMut.mutate()}
      />

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
