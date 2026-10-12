import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { tagsApi, apiErrorMessage } from "@/lib/api";
import { PageContainer, PageHeader } from "@/components/common/PageContainer";
import { AsyncState, QueryError } from "@/components/common/AsyncState";
import { Button, ConfirmDialog, Input } from "@/components/ui";
import { Tag, Plus, Search } from "lucide-react";
import { TagCard } from "./tags/TagCard";
import { CreateTagModal } from "./tags/CreateTagModal";

const FILTERS = ["all", "message", "tool", "custom"] as const;
type Filter = typeof FILTERS[number];

export default function Tags() {
  const { t } = useTranslation(["tags", "common"]);
  const client = useQueryClient();
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState({ name: "", description: "" });
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const query = useQuery({ queryKey: ["unified-tags"], queryFn: () => tagsApi.unified().then((r) => r.data), throwOnError: false });
  const create = useMutation({
    mutationFn: () => tagsApi.createMessageTag(form.name.trim(), form.description.trim()),
    onSuccess: async () => { setShowCreate(false); setForm({ name: "", description: "" }); await client.invalidateQueries({ queryKey: ["unified-tags"] }); },
  });
  const remove = useMutation({
    mutationFn: (name: string) => tagsApi.deleteMessageTag(name),
    onSuccess: async () => { setDeleteTarget(null); await client.invalidateQueries({ queryKey: ["unified-tags"] }); },
  });
  const tags = query.data ?? [];
  const keyword = search.trim().toLowerCase();
  const filtered = tags.filter((tag) => (filter === "all" || tag.sources.includes(filter))
    && [tag.name, tag.description, ...tag.tools].some((value) => value.toLowerCase().includes(keyword)));
  return <PageContainer>
    <PageHeader icon={<Tag size={20} className="text-accent" />} title={t("title")} subtitle={t("subtitle")}
      actions={<Button size="sm" variant="primary" onClick={() => { create.reset(); setShowCreate(true); }}><Plus size={14} />{t("createTag")}</Button>} />
    <div className="flex flex-col gap-3 xl:flex-row xl:items-center">
      <div className="relative min-w-0 flex-1">
        <Search size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
        <Input value={search} onChange={(e) => setSearch(e.target.value)} aria-label={t("searchPlaceholder")} placeholder={t("searchPlaceholder")} className="pl-9" />
      </div>
      <div className="flex flex-wrap gap-1.5" aria-label={t("filterLabel")}>
        {FILTERS.map((value) => <Button key={value} size="sm" variant={filter === value ? "primary" : "secondary"} aria-pressed={filter === value} onClick={() => setFilter(value)}>
          {t("filters." + value)}<span className="tabular-nums opacity-70">{value === "all" ? tags.length : tags.filter((tag) => tag.sources.includes(value)).length}</span>
        </Button>)}
      </div>
    </div>
    <AsyncState pending={query.isPending} error={!query.data && query.error} retry={() => void query.refetch()}>
      {query.isError && query.data && <QueryError compact error={query.error} retry={() => void query.refetch()} />}
      <div className="grid items-start gap-3 md:grid-cols-2 2xl:grid-cols-3">
        {filtered.map((tag) => <TagCard key={tag.name} tag={tag} onDelete={() => { remove.reset(); setDeleteTarget(tag.name); }} t={t} />)}
      </div>
      {!filtered.length && <p className="rounded-xl border border-dashed border-border p-8 text-center text-sm text-muted">{t(keyword || filter !== "all" ? "noMatch" : "noTags")}</p>}
    </AsyncState>
    {remove.isError && <p role="alert" className="text-sm text-danger">{apiErrorMessage(remove.error, t("common:requestFailed"))}</p>}
    <CreateTagModal open={showCreate} onClose={() => { if (!create.isPending) setShowCreate(false); }}
      form={form} setForm={setForm} pending={create.isPending} onSubmit={() => create.mutate()} error={create.error} />
    <ConfirmDialog open={deleteTarget !== null} onClose={() => { if (!remove.isPending) setDeleteTarget(null); }}
      onConfirm={() => { if (deleteTarget) remove.mutate(deleteTarget); }} title={t("common:delete")}
      message={t("deleteConfirm", { name: deleteTarget ?? "" })} danger loading={remove.isPending} />
  </PageContainer>;
}
