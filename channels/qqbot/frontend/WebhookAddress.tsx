import { useId, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Check, Copy } from "lucide-react";
import { Button, Input } from "@/components/ui";
import { useCopyFeedback } from "@/hooks/useCopyFeedback";

/** QQ uses a public HTTPS callback. Only the browser composes this value. */
function callbackUrl(base: string, path: string): string | null {
  if (!base.trim() || !path.startsWith("/")) return null;
  try {
    const url = new URL(base.includes("://") ? base.trim() : `https://${base.trim()}`);
    const host = url.hostname.toLowerCase().replace(/\.$/, "");
    if (url.protocol !== "https:" || url.username || url.password || url.search || url.hash ||
        !["", "80", "443", "8080", "8443"].includes(url.port)) return null;
    if (host.startsWith("[") ? /^\[(?:::1?\]|f[cd]|fe[89ab]|::ffff:)/i.test(host)
      : !host.includes(".") || /(?:^|\.)(?:localhost|local|internal)$/.test(host) ||
        (/^(?:\d+\.){3}\d+$/.test(host) &&
         /^(?:127\.|10\.|0\.|192\.168\.|169\.254\.|172\.(?:1[6-9]|2\d|3[01])\.)/.test(host))) return null;
    const prefix = url.pathname.replace(/\/+$/, "");
    url.pathname = prefix.endsWith(path) ? prefix : prefix + path;
    return url.toString();
  } catch {
    return null;
  }
}

export function WebhookAddress({ path, active }: { path: string; active: boolean }) {
  const { t } = useTranslation("channel-qqbot");
  const id = useId();
  const client = useQueryClient();
  const draftKey = ["qqbot-callback-address", path];
  const [base, setBase] = useState(() => client.getQueryData<string>(draftKey) ??
    (callbackUrl(window.location.origin, path) ? window.location.origin : ""));
  const [copied, markCopied, resetCopied] = useCopyFeedback();
  const [manual, setManual] = useState(false);
  const output = useRef<HTMLInputElement>(null);
  const url = callbackUrl(base, path);
  const invalid = !!base.trim() && !url;
  const copy = async () => {
    if (!url) return;
    try {
      await navigator.clipboard.writeText(url);
      markCopied();
    } catch {
      output.current?.focus();
      output.current?.select();
      setManual(true);
    }
  };
  return <div className="space-y-2 rounded-lg border border-border bg-panel p-3">
    <label htmlFor={id} className="text-sm font-medium text-heading">{t("publicHost")}</label>
    {!active && <p id={`${id}-hint`} className="text-xs text-muted">{t("publicHint")}</p>}
    <Input id={id} value={base} placeholder="https://bot.example.com" autoComplete="off" spellCheck={false}
      aria-describedby={active ? undefined : `${id}-hint`} aria-invalid={invalid}
      onBlur={() => client.setQueryData(draftKey, url ? base : "")}
      onChange={e => { setBase(e.target.value); resetCopied(); setManual(false); }} />
    {invalid && <p role="alert" className="text-xs text-danger">{t("publicInvalid")}</p>}
    <label htmlFor={`${id}-callback`} className="block pt-1 text-xs font-medium text-heading">{url ? t("callback") : t("callbackPath")}</label>
    <div className="flex flex-wrap items-center gap-2">
      <Input id={`${id}-callback`} readOnly ref={output} value={url ?? path}
        className="min-w-0 flex-1 basis-48 font-mono text-xs" onFocus={e => e.currentTarget.select()} />
      <Button size="sm" disabled={!url} onClick={() => void copy()}>{copied ? <Check size={14} /> : <Copy size={14} />}
        {copied ? t("copied") : t("copy")}</Button>
    </div>
    {(manual || !active) && <p role="status" className="text-xs text-muted">{t(manual ? "manualCopy" : "callbackHint")}</p>}
  </div>;
}
