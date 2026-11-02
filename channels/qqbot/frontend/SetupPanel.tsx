import { useId, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Cable, ExternalLink, Plug, Save, Waypoints } from "lucide-react";
import { Button } from "@/components/ui";
import { ConfigSource } from "@/components/common/ConfigSource";
import { apiErrorMessage } from "@/lib/api";
import type { ChannelSettingsProps } from "@/lib/channel-plugins";
import type { ConfigMetaItem } from "@/lib/types";
import { cn } from "@/lib/utils";
import { getSetup } from "./api";
import { ConnectionCheck } from "./ConnectionCheck";
import { WebhookAddress } from "./WebhookAddress";

const queryKey = ["qqbot-setup"];

export default function SetupPanel({ items, value, update, field, busy, dirty, status, apply }: ChannelSettingsProps) {
  const { t } = useTranslation("channel-qqbot");
  const groupId = useId();
  const client = useQueryClient();
  const setup = useQuery({ queryKey, queryFn: getSetup, refetchInterval: 5000, throwOnError: false });
  const [feedback, setFeedback] = useState<{ text: string; failed: boolean } | null>(null);
  const accounts = items.filter(i => i.tag === "account");
  const transport = items.find(i => i.tag === "transport");
  const subscriptions = items.find(i => i.tag === "subscriptions");
  const startup = items.find(i => i.tag === "startup");
  const preferences = items.filter(i => !["account", "transport", "subscriptions"].includes(i.tag));
  const mode = transport ? String(value(transport)) : "";
  const mask = subscriptions ? Number(value(subscriptions)) : 0;
  const validMask = Number.isInteger(mask) && mask >= (subscriptions?.min ?? 1) && mask <= (subscriptions?.max ?? 0x7fffffff);
  const validAccount = accounts.length > 0 && accounts.every(i => String(value(i) ?? "").trim());
  const disabledByEnv = !!startup && !startup.editable && !value(startup);
  const disablingStartup = !!startup && !!startup.value && value(startup) === false;
  const runtime = setup.isError ? status : setup.data?.status ?? status;
  const active = ["starting", "running", "reconnecting"].includes(runtime);
  const statusFirst = active || runtime === "error";
  const label = (i: ConfigMetaItem) => t(`field.${i.key.replace(/^qqbot_/, "")}`, { defaultValue: i.description });
  const canApply = !!transport && (!subscriptions || validMask);
  const submit = async (connect: boolean) => {
    setFeedback(null);
    try {
      await apply(connect);
      setFeedback({ text: connect ? t("requested") : t("saved"), failed: false });
    } catch (error) {
      setFeedback({ text: apiErrorMessage(error, t("applyFailed")), failed: true });
    } finally {
      await client.invalidateQueries({ queryKey });
    }
  };
  const diagnostics = setup.data && <ConnectionCheck key={`${runtime}:${setup.data.mode}:${setup.data.self_id}:${dirty}`}
    setup={setup.data} status={runtime} compact={active} dirty={dirty} busy={busy || setup.isError} />;
  return <section aria-label={t(active ? "overview" : "title")} className="space-y-4">
    <header className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0 space-y-1">
        <h3 className="break-words text-base font-semibold text-heading">{active ? setup.data?.bot_name || t("overview") : t("title")}</h3>
        {active ? setup.data?.self_id && <p className="break-all text-xs text-muted">ID {setup.data.self_id}</p>
          : <p className="text-xs leading-relaxed text-muted">{t("intro")}</p>}
      </div>
      <a href="https://q.qq.com/" target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs text-accent hover:underline">
        {t("platform")}<ExternalLink size={12} />
      </a>
    </header>
    {setup.isError && <p role="alert" className="text-xs text-danger">{t("setupError")} <button type="button" className="underline" onClick={() => void setup.refetch()}>{t("retry")}</button></p>}
    {setup.isPending && <p role="status" className="text-xs text-muted">{t("loading")}</p>}
    {statusFirst && diagnostics}
    <fieldset disabled={busy} onChange={() => setFeedback(null)} className="min-w-0 space-y-4">
      {!active && <p className="text-xs text-muted">{t("credentialHint")}</p>}
      <div className="grid gap-3 sm:grid-cols-2">{accounts.map(i => field(i, label(i)))}</div>
      {transport && <fieldset disabled={!transport.editable} className="min-w-0 space-y-2">
        <legend className="text-xs font-medium text-heading">{t("transport")}</legend>
        <div className="grid gap-2 sm:grid-cols-2">{transport.options?.map(option => {
          const Icon = option === "webhook" ? Waypoints : Cable;
          return <label key={option} className={cn("flex items-start gap-2 rounded-lg border p-3",
            mode === option ? "border-accent bg-secondary" : "border-border hover:bg-hover")}>
            <input name={groupId} type="radio" value={option} checked={mode === option}
              aria-label={t(`mode.${option}`)} className="mt-1 accent-accent" onChange={() => {
                if (option === "webhook" && subscriptions && !validMask) {
                  update(subscriptions, subscriptions.value ?? subscriptions.default);
                }
                update(transport, option);
              }} />
            <span className="min-w-0"><span className="flex items-center gap-1.5 text-sm font-semibold text-heading"><Icon size={14} />{t(`mode.${option}`)}</span>
              {!active && <span className="mt-1 block text-xs leading-relaxed text-muted">{t(`modeHint.${option}`)}</span>}</span>
          </label>;
        })}</div>
        <ConfigSource item={transport} />
      </fieldset>}
      {mode === "websocket" && subscriptions && <fieldset className="min-w-0 rounded-lg border border-border p-3" disabled={!subscriptions.editable}>
        <legend className="px-1 text-xs font-semibold text-heading">{t("events")}</legend>
        {!active && <p className="mb-2 text-xs text-muted">{t("eventHint")}</p>}
        <div className="grid gap-x-3 sm:grid-cols-2">{Object.entries(setup.data?.intents ?? {}).map(([name, flag]) =>
          <label key={name} className="flex items-center gap-2 py-1.5 text-xs text-heading">
            <input type="checkbox" checked={(mask & flag) !== 0} className="accent-accent"
              onChange={e => update(subscriptions, e.target.checked ? mask | flag : mask & ~flag)} />
            {t(`intent.${name}`, { defaultValue: name })}
          </label>)}</div>
        <div className="mt-2 max-w-sm">{field(subscriptions, label(subscriptions))}</div>
      </fieldset>}
      {!validMask && subscriptions && <p role="alert" className="text-xs text-danger">{t("invalidMask")}</p>}
      {setup.data && <div hidden={mode !== "webhook"}><WebhookAddress path={setup.data.callback_path} active={active} /></div>}
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{preferences.map(i => field(i, label(i)))}</div>
      {(!active || dirty || feedback?.failed) && <div className="space-y-2 border-t border-border pt-4">
        <div className="flex flex-wrap gap-2">
          <Button variant="primary" size="sm" onClick={() => void submit(true)} loading={busy}
            disabled={!canApply || !validAccount || disabledByEnv || disablingStartup || (active && !dirty)}>
            <Plug size={14} />{active ? t("applyOnline") : dirty ? t("saveConnect") : t("connect")}
          </Button>
          <Button size="sm" disabled={!canApply || !dirty || busy} onClick={() => void submit(false)}>
            <Save size={14} />{t("saveOnly")}
          </Button>
        </div>
        {(disablingStartup || !validAccount || !active) && <p className="text-xs text-muted">{disablingStartup ? t("saveStartupHint") : !validAccount ? t("missing") : t("startHint")}</p>}
        {feedback && (feedback.failed || (!dirty && !active)) && <p role={feedback.failed ? "alert" : "status"} className={feedback.failed ? "text-xs text-danger" : "text-xs text-ok"}>{feedback.text}</p>}
      </div>}
    </fieldset>
    {!statusFirst && diagnostics}
  </section>;
}
