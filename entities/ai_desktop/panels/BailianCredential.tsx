import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { QrCode, ExternalLink } from "lucide-react";
import { Badge, Button } from "@/components/ui";
import { CredentialCard } from "@/components/credentials/CredentialCard";
import { QueryError } from "@/components/common/AsyncState";
import type { CredentialEditorProps } from "@/lib/ui-contributions";
import { aiDesktopApi } from "./api";
import { SubscriptionLoginDialog } from "./SubscriptionLoginDialog";

/** 百炼授权状态与扫码流程，配置不隐式开启额度监控。 */
export default function BailianCredential({ credential }: CredentialEditorProps) {
  const { t } = useTranslation("ai_desktop");
  const [open, setOpen] = useState(false);
  const query = useQuery({ queryKey: ["bailian-console-credential"],
    queryFn: () => aiDesktopApi.consoleCredential().then((r) => r.data), throwOnError: false });
  return <CredentialCard credential={credential} status={query.data && <Badge variant={query.data.configured ? "ok" : "neutral"}>
    {t(query.data.configured ? "subscription.authorizationSaved" : "subscription.authorizationMissing")}
  </Badge>}>
    {query.isPending ? <p role="status" className="text-xs text-muted">{t("common:loading")}</p>
      : query.error ? <QueryError compact error={query.error} retry={() => void query.refetch()} /> : <>
      <p className="text-xs leading-relaxed text-muted">{t("subscription.credentialsHint")}</p>
      {query.data?.available ? <div className="flex flex-wrap items-center justify-between gap-3">
        <span className="text-xs text-muted">{t(query.data.monitoring ? "subscription.monitoringOn" : "subscription.monitoringOff")}</span>
        <Button size="sm" variant="primary" onClick={() => setOpen(true)}><QrCode size={16} />
          {t(query.data.configured ? "subscription.signInAgain" : "subscription.login")}</Button>
      </div> : <div className="space-y-2 text-xs text-muted">
        <p>{t("subscription.cliRequired")}</p>
        <a href="https://bailian.aliyun.com/cli/install.md" target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-accent hover:underline">
          {t("subscription.installGuide")}<ExternalLink size={13} /></a>
      </div>}
    </>}
    {open && <SubscriptionLoginDialog onClose={() => setOpen(false)} />}
  </CredentialCard>;
}
