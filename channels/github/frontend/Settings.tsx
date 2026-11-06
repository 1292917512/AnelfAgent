import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import { Button, Select } from "@/components/ui";
import { TabBar } from "@/components/common/TabBar";
import { SchemaField } from "@/components/common/SchemaField";
import { ConfigSource } from "@/components/common/ConfigSource";
import type { ChannelSettingsProps } from "@/lib/channel-plugins";
import { api, apiErrorMessage } from "@/lib/api/client";
import { StatusPanel } from "./StatusPanel";

export default function Settings({ items, value, update, field, busy, dirty, status, apply }: ChannelSettingsProps) {
  const { t } = useTranslation("channel-github");
  const queryClient = useQueryClient();
  const sections = [...new Set(items.map(item => item.schema?.section ?? "reception"))];
  const [active, setActive] = useState(sections[0] ?? "reception");
  const [feedback, setFeedback] = useState<{ text: string; failed: boolean } | null>(null);
  const [account, setAccount] = useState("");
  const [authorizeUrl, setAuthorizeUrl] = useState("");
  const form = useRef<HTMLFormElement>(null);
  const tabs = [...sections.map(key => ({ key, label: t(`section.${key}`, { defaultValue: key }) })), { key: "status", label: t("status.title") }];
  const submit = async (connect: boolean) => {
    if (!form.current?.reportValidity()) return;
    setFeedback(null);
    try {
      const reconnect = connect && ["running", "starting", "reconnecting"].includes(status);
      await apply(connect && !reconnect);
      if (reconnect) {
        const result = await api.post<{ failed?: string[] }>("/adapters/github/reload", {}, { errorFeedback: "local" });
        if (result.data.failed?.length) throw new Error(t("failed"));
        await queryClient.invalidateQueries({ queryKey: ["adapters"] });
      }
      setFeedback({ text: t(connect ? "applied" : "saved"), failed: false });
    }
    catch (error) { setFeedback({ text: apiErrorMessage(error, t("failed")), failed: true }); }
  };
  const startOAuth = async () => {
    setFeedback(null); setAuthorizeUrl("");
    try {
      const response = await api.post<{ authorize_url: string }>("/channels/github/oauth/start", { account }, { errorFeedback: "local" });
      setAuthorizeUrl(response.data.authorize_url);
    } catch (error) { setFeedback({ text: apiErrorMessage(error, t("failed")), failed: true }); }
  };
  const accountsItem = items.find(item => item.key === "github_accounts");
  const accountsValue = accountsItem ? value(accountsItem) : [];
  const accounts = Array.isArray(accountsValue) ? accountsValue.filter((item): item is { name: string; client_id: string } => !!item && typeof item === "object" && typeof item.name === "string" && !!item.client_id) : [];
  return <div className="min-w-0 space-y-4">
    <p className="text-xs leading-relaxed text-muted">{t("intro")}</p>
    <TabBar tabs={tabs} activeTab={active} onChange={setActive} />
    <form ref={form} onSubmit={event => { event.preventDefault(); void submit(false); }} className="min-w-0 space-y-4">
      <fieldset disabled={busy} className="min-w-0 space-y-4">
        {sections.map(section => <section key={section} hidden={active !== section} aria-label={t(`section.${section}`, { defaultValue: section })} className="space-y-4">
          {items.filter(item => (item.schema?.section ?? "reception") === section).map(item => {
            const name = item.key.replace(/^github_/, "");
            const label = t(`field.${name}`, { defaultValue: item.description });
            return item.type === "json" && item.schema ? <div key={item.key} className="min-w-0 space-y-2 rounded-lg border border-border p-3">
              <h4 className="text-sm font-medium text-heading">{label}</h4>
              <ConfigSource item={item} />
              <SchemaField schema={item.schema} value={value(item)} label={label} path={name} disabled={!item.editable}
                onChange={next => update(item, next)} translate={(key, fallback) => t(key, { defaultValue: fallback })} />
            </div> : <div key={item.key}>{field(item, label)}</div>;
          })}
          {section === "authentication" && accounts.length > 0 && <div className="space-y-2 rounded-lg border border-border p-3">
            <p className="text-xs text-muted">{t("oauth.hint")}</p>
            <Select aria-label={t("oauth.account")} value={account} onChange={event => { setAccount(event.target.value); setAuthorizeUrl(""); }}>
              <option value="">{t("oauth.choose")}</option>{accounts.map(entry => <option key={entry.name} value={entry.name}>{entry.name}</option>)}
            </Select>
            <Button disabled={!account || dirty} onClick={() => void startOAuth()}>{t("oauth.start")}</Button>
            {authorizeUrl && <a href={authorizeUrl} target="_blank" rel="noreferrer" className="ml-3 text-sm text-accent underline">{t("oauth.open")}</a>}
          </div>}
        </section>)}
      </fieldset>
      {active === "status" && <StatusPanel />}
      <div className="flex flex-wrap items-center gap-2 border-t border-border pt-3">
        <Button type="submit" variant="primary" disabled={busy || !dirty}>{t("save")}</Button>
        <Button disabled={busy} onClick={() => void submit(true)}>{t("apply")}</Button>
        <span className="text-xs text-muted">{t(dirty ? "unsaved" : "restartHint")}</span>
      </div>
      {feedback && <p role={feedback.failed ? "alert" : "status"} className={`text-xs ${feedback.failed ? "text-danger" : "text-ok"}`}>{feedback.text}</p>}
    </form>
  </div>;
}
