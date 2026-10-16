import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { KeyRound, Search } from "lucide-react";
import { providerKeysApi } from "@/lib/api";
import { Input } from "@/components/ui";
import { AsyncState, QueryError } from "./AsyncState";
import { TabBar } from "./TabBar";
import { CredentialCard } from "@/components/credentials/CredentialCard";
import { CredentialFields } from "@/components/credentials/CredentialFields";
import { ContributionContent } from "@/components/extensions/ContributionSlot";
import { getUiContributions } from "@/lib/ui-contribution-registry";

/** 凭据目录仅消费模块声明，不持有供应商协议或配置字段。 */
export function ProviderKeysPanel({ domain, owner: moduleOwner }: { domain?: string; owner?: string }) {
  const { t } = useTranslation("common");
  const [search, setSearch] = useState("");
  const [owner, setOwner] = useState("all");
  const query = useQuery({ queryKey: ["providerKeys", domain ?? "all"],
    queryFn: () => providerKeysApi.list(domain).then((r) => r.data), throwOnError: false });
  const { hash } = useLocation();
  useEffect(() => {
    if (query.data && hash.startsWith("#credential-")) document.getElementById(hash.slice(1))?.scrollIntoView({ block: "center" });
  }, [query.data, hash]);
  const providers = (query.data?.providers ?? []).filter((entry) => !moduleOwner || entry.owner === moduleOwner);
  const editors = getUiContributions("credentials.editor");
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
      <div className="grid items-start gap-4 xl:grid-cols-2">
        {providers.map((credential) => {
          const hidden = !filtered.includes(credential);
          if (!credential.editor) return <div key={credential.name} hidden={hidden}><CredentialFields credential={credential} /></div>;
          const editor = editors.find((item) => item.owner === credential.owner && item.id === credential.editor);
          const fallback = <CredentialCard credential={credential}><p className="text-sm text-muted">{t("providerKeys.editorUnavailable")}</p></CredentialCard>;
          return <div key={credential.name} hidden={hidden}>{editor ? <ContributionContent entry={editor} componentProps={{ credential }} fallback={fallback} /> : fallback}</div>;
        })}
      </div>
      {!filtered.length && <p className="py-12 text-center text-sm text-muted">{t("providerKeys.empty")}</p>}
    </AsyncState>
  </div>;
}
