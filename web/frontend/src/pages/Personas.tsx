import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { personasApi } from "@/lib/api";
import { PageContainer, PageIntro } from "@/components/common/PageContainer";
import { AsyncState } from "@/components/common/AsyncState";
import { ListToolbar } from "@/components/common/ListToolbar";
import { cn } from "@/lib/utils";
import { Button, Input, ConfirmDialog } from "@/components/ui";
import { Plus, Trash2, Star } from "lucide-react";
import { PersonaEditor } from "./personas/PersonaEditor";

export default function Personas() {
  const { t } = useTranslation(["personas", "common"]);
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const selected = params.get("persona");
  const select = (key: string) => setParams((current) => { const next = new URLSearchParams(current); next.set("persona", key); return next; });
  const [newKey, setNewKey] = useState("");
  const [search, setSearch] = useState("");
  const [deleting, setDeleting] = useState<string | null>(null);
  const personas = useQuery({ queryKey: ["personas"], queryFn: () => personasApi.list().then((r) => r.data), throwOnError: false });
  const { data: active } = useQuery({ queryKey: ["activePersona"], queryFn: () => personasApi.active().then((r) => r.data.active) });
  const create = useMutation({
    mutationFn: personasApi.create,
    onSuccess: (_data, key) => { void client.invalidateQueries({ queryKey: ["personas"] }); setNewKey(""); select(key); },
  });
  const remove = useMutation({
    mutationFn: personasApi.remove,
    onSuccess: () => { void client.invalidateQueries({ queryKey: ["personas"] }); setDeleting(null); },
  });
  const activate = useMutation({
    mutationFn: personasApi.activate,
    onSuccess: () => client.invalidateQueries({ queryKey: ["activePersona"] }),
  });
  const matches = (personas.data ?? []).filter((p) => [p.key, p.name, p.description].join(" ").toLowerCase().includes(search.trim().toLowerCase()));
  return <PageContainer>
    <PageIntro />
    <form className="flex flex-wrap gap-2" onSubmit={(event) => { event.preventDefault(); if (newKey.trim() && !create.isPending) create.mutate(newKey.trim()); }}>
      <Input value={newKey} onChange={(event) => setNewKey(event.target.value)} aria-label={t("newPersonaKey")} placeholder={t("newPersonaKey")} className="min-w-40 flex-1 sm:max-w-xs" />
      <Button type="submit" variant="primary" disabled={!newKey.trim()} loading={create.isPending}><Plus size={16} />{t("createNew")}</Button>
    </form>
    <AsyncState pending={personas.isPending} error={!personas.data ? personas.error : undefined} retry={() => void personas.refetch()}>
      <div className="grid items-start gap-6 lg:grid-cols-[320px_minmax(0,1fr)]">
        <div className="space-y-4">
          <ListToolbar search={search} onSearch={setSearch} count={matches.length} />
          <div className="space-y-2">
            {matches.length === 0 && <p className="py-10 text-center text-sm text-muted">{t("common:noMatches")}</p>}
            {matches.map((persona) => <div key={persona.key} className={cn("flex items-center rounded-xl border p-2", selected === persona.key ? "border-accent bg-accent-subtle" : "border-border bg-card")}>
              <button onClick={() => select(persona.key)} aria-pressed={selected === persona.key} className="min-w-0 flex-1 p-2 text-left">
                <span className="block truncate text-sm font-medium text-heading">{persona.name || persona.key}</span>
                <span className="block truncate text-xs text-muted">{persona.description || persona.key}</span>
              </button>
              <Button size="icon" variant="ghost" title={t("activate")} disabled={persona.key === active || activate.isPending} onClick={() => activate.mutate(persona.key)}>
                <Star size={15} className={persona.key === active ? "fill-warn text-warn" : ""} />
              </Button>
              <Button size="icon" variant="ghost" title={t("common:delete")} disabled={persona.key === active} onClick={() => setDeleting(persona.key)}><Trash2 size={15} /></Button>
            </div>)}
          </div>
        </div>
        {selected && personas.data?.some((p) => p.key === selected) ? <PersonaEditor key={selected} personaKey={selected} />
          : <div className="rounded-xl border border-dashed border-border px-6 py-20 text-center text-sm text-muted">{t("selectPersona")}</div>}
      </div>
    </AsyncState>
    <ConfirmDialog open={deleting !== null} title={t("common:delete")} message={t("confirmDelete", { name: deleting })}
      danger loading={remove.isPending} onClose={() => setDeleting(null)} onConfirm={() => { if (deleting) remove.mutate(deleting); }} />
  </PageContainer>;
}
