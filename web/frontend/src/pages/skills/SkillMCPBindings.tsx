import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { ArrowUpRight, Link2, Search } from "lucide-react";
import { apiErrorMessage, skillsApi } from "@/lib/api";
import type { SkillMCPBindings as Bindings } from "@/lib/types";
import { Button, ConfirmDialog, Input, Modal } from "@/components/ui";
import { AsyncState } from "@/components/common/AsyncState";
import { useDiscardChanges } from "@/hooks/useDiscardChanges";
import { useDialogSession } from "@/hooks/useDialogSession";
import { SkillMCPState } from "@/components/skills/SkillMCPStatus";

export function SkillMCPBindings({ name, count }: { name: string; count: number }) {
  const { t } = useTranslation("skills");
  const [open, setOpen] = useState(false);
  return <><Button size="sm" onClick={() => setOpen(true)}><Link2 size={14} />{t("mcp.manage", { count })}</Button>
    {open && <BindingsLoader name={name} onClose={() => setOpen(false)} />}</>;
}

function BindingsLoader({ name, onClose }: { name: string; onClose: () => void }) {
  const { t } = useTranslation("skills");
  const session = useDialogSession(onClose);
  const query = useQuery({ queryKey: ["skillMcp", name], queryFn: () => skillsApi.mcpBindings(name).then((r) => r.data), throwOnError: false });
  if (!query.data || !session.surfaceProps.open) return <Modal {...session.surfaceProps} title={t("mcp.title", { name })} onClose={session.close} width="max-w-xl">
    <AsyncState pending={query.isPending} error={query.error} retry={() => void query.refetch()}>{null}</AsyncState>
  </Modal>;
  return <BindingsEditor data={query.data} onClose={onClose} />;
}

function BindingsEditor({ data, onClose }: { data: Bindings; onClose: () => void }) {
  const { t } = useTranslation("skills");
  const session = useDialogSession(onClose);
  const client = useQueryClient();
  const [initial] = useState(() => data.servers.map((server) => server.name));
  const [selected, setSelected] = useState(initial);
  const [search, setSearch] = useState("");
  const dirty = selected.length !== initial.length || selected.some((name) => !initial.includes(name));
  const save = useMutation({ mutationFn: () => skillsApi.setMcpBindings(data.name, selected), onSuccess: () => {
    void client.invalidateQueries({ queryKey: ["skillMcp", data.name] });
    void client.invalidateQueries({ queryKey: ["skill", data.name] });
    void client.invalidateQueries({ queryKey: ["skills", "list"] });
    session.close();
  } });
  const guard = useDiscardChanges(dirty, session.close, save.isPending);
  const choices = [...data.available, ...data.servers.filter((server) => !data.available.some((item) => item.name === server.name))]
    .filter((server) => server.name.toLocaleLowerCase().includes(search.toLocaleLowerCase()));
  return <><Modal {...session.surfaceProps} title={t("mcp.title", { name: data.name })} onClose={guard.requestClose} dismissible={!save.isPending} width="max-w-xl"
    footer={<><Button onClick={guard.requestClose} disabled={save.isPending}>{t("cancel")}</Button><Button variant="primary" disabled={!dirty} loading={save.isPending} onClick={() => save.mutate()}>{t("save")}</Button></>}>
    <div className="space-y-4">
      <p className="text-xs leading-5 text-muted">{t("mcp.hint")}</p>
      <div className="flex items-center justify-between gap-2 text-xs text-muted"><span>{t("mcp.selected", { count: selected.length })}</span><Link to="/mcp" className="inline-flex items-center gap-1 text-accent">{t("mcp.configure")}<ArrowUpRight size={12} /></Link></div>
      <div className="relative"><Search size={14} className="absolute left-3 top-3 text-muted" /><Input className="pl-9" aria-label={t("mcp.search")} placeholder={t("mcp.search")} value={search} onChange={(event) => setSearch(event.target.value)} /></div>
      <fieldset disabled={save.isPending} className="space-y-2">
        {choices.map((server) => <label key={server.name} className="flex cursor-pointer items-start gap-3 rounded-lg border border-border p-3 hover:bg-secondary">
          <input type="checkbox" className="mt-1 accent-accent" checked={selected.includes(server.name)} onChange={(event) => setSelected((current) => event.target.checked ? [...current, server.name] : current.filter((name) => name !== server.name))} />
          <span className="min-w-0 flex-1"><span className="block break-all text-sm font-medium">{server.name}</span><SkillMCPState state={server} /></span>
        </label>)}
        {!choices.length && <p className="py-6 text-center text-sm text-muted">{t("mcp.empty")}</p>}
      </fieldset>
      {save.error && <p role="alert" className="break-words text-xs text-danger">{apiErrorMessage(save.error, t("mcp.saveFailed"))}</p>}
    </div>
  </Modal><ConfirmDialog {...guard.confirmProps} /></>;
}
