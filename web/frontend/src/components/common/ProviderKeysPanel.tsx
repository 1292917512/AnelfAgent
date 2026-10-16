import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { KeyRound, Search } from "lucide-react";
import { providerKeysApi } from "@/lib/api";
import { Input } from "@/components/ui";
import { AsyncState, QueryError } from "./AsyncState";
import { TabBar } from "./TabBar";
import { CredentialCard, CredentialDialog } from "@/components/credentials/CredentialCard";
import { CredentialFields } from "@/components/credentials/CredentialFields";
import { ContributionContent } from "@/components/extensions/ContributionSlot";
import { getUiContributions } from "@/lib/ui-contribution-registry";

/** 凭据目录仅消费模块声明，不持有供应商协议或配置字段。 */
export function ProviderKeysPanel({ domain, owner: moduleOwner }: { domain?: string; owner?: string }) {
  const { t } = useTranslation("common");
  const [search, setSearch] = useState("");
  const [owner, setOwner] = useState("all");
  const [editing, setEditing] = useState<string>();
  const query = useQuery({ queryKey: ["providerKeys", domain ?? "all"],
    queryFn: () => providerKeysApi.list(domain).then((r) => r.data), throwOnError: false });
  const { hash } = useLocation();
  useEffect(() => {
    if (query.data && hash.startsWith("#credential-")) document.getElementById(hash.slice(1))?.scrollIntoView({ block: "center" });
  }, [query.data, hash]);
  const providers = (query.data?.providers ?? []).filter((entry) => !moduleOwner || entry.owner === moduleOwner);
  const editors = getUiContributions("credentials.editor");
  const selected = providers.find((entry) => entry.name === editing);
  const editor = selected && editors.find((item) => item.owner === selected.owner && item.id === selected.editor);
  const closeEditor = () => setEditing(undefined);
  const fallback = selected && <CredentialDialog credential={selected} onClose={closeEditor}><p className="text-sm text-muted">{t("providerKeys.editorUnavailable")}</p></CredentialDialog>;
  const keyword = search.trim().toLocaleLowerCase();
  const filtered = providers.filter((entry) => (owner === "all" || (entry.owner.split(":")[0] || "unregistered") === owner)
    && [entry.title, entry.name, entry.owner, entry.owner_title ?? "", entry.description].some((text) => text.toLocaleLowerCase().includes(keyword)));
  const kinds = ["all", "entity", "channel", "core", "unregistered"].filter((kind) => kind !== "unregistered" || providers.some((item) => !item.owner));
  if (moduleOwner && query.data && !providers.length) return null;
  return <div className="space-y-5">
    <div className="flex flex-wrap items-start justify-between gap-4">
      <div>
        <h2 className="flex items-center gap-2 text-base font-semibold text-heading"><KeyRound size={18} className="text-accent" />{t("providerKeys.title")}</h2>
        <p className="mt-2 text-sm text-muted">{t("providerKeys.hint")}</p>
      </div>
      {!moduleOwner && <div className="relative w-full sm:w-64"><Search size={16} className="absolute left-3 top-2.5 text-muted" />
        <Input className="pl-9" value={search} onChange={(event) => setSearch(event.target.value)} aria-label={t("providerKeys.search")} placeholder={t("providerKeys.search")} />
      </div>}
    </div>
    {!moduleOwner && <TabBar tabs={kinds.map((key) => ({ key, label: t(`providerKeys.owner.${key}`) }))} activeTab={owner} onChange={setOwner} />}
    {query.data && query.error && <QueryError compact error={query.error} retry={() => void query.refetch()} />}
    <AsyncState pending={query.isPending} error={query.data ? undefined : query.error} retry={() => void query.refetch()}>
      <div className="grid auto-rows-fr grid-cols-[repeat(auto-fill,minmax(min(100%,19rem),1fr))] gap-3">
        {filtered.map((credential) => <CredentialCard key={credential.name} credential={credential} onEdit={() => setEditing(credential.name)} />)}
      </div>
      {!filtered.length && <p className="py-12 text-center text-sm text-muted">{t("providerKeys.empty")}</p>}
    </AsyncState>
    {selected && (!selected.editor ? <CredentialFields key={selected.name} credential={selected} onClose={closeEditor} />
      : editor ? <ContributionContent entry={editor} componentProps={{ credential: selected, onClose: closeEditor }} fallback={fallback}
        pending={<CredentialDialog credential={selected} onClose={closeEditor}><p role="status" className="text-xs text-muted">{t("loading")}</p></CredentialDialog>} /> : fallback)}
  </div>;
}
