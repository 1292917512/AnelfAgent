import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ArrowUpRight, Boxes, KeyRound, Radio, Settings2 } from "lucide-react";
import type { ProviderKeyEntry } from "@/lib/types";

/** 凭据卡片的归属与导航外框，字段及授权流程由注册内容提供。 */
export function CredentialCard({ credential, status, children }: {
  credential: ProviderKeyEntry; status?: ReactNode; children: ReactNode;
}) {
  const { t } = useTranslation("common");
  const [kind, name] = credential.owner.split(":");
  const Icon = kind === "entity" ? Boxes : kind === "channel" ? Radio : kind === "core" ? Settings2 : KeyRound;
  const owner = t(`providerKeys.owner.${kind || "unregistered"}`);
  return <section id={`credential-${credential.name}`} aria-label={credential.title}
    className="min-w-0 scroll-mt-24 overflow-hidden rounded-2xl border border-border bg-card">
    <header className="space-y-3 border-b border-border/60 p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <span className="rounded-xl bg-accent/10 p-2.5 text-accent"><Icon size={18} /></span>
          <div className="min-w-0">
            <h3 className="break-words text-sm font-semibold text-heading">{credential.title}</h3>
            <p className="mt-1 break-all text-xs text-muted">{owner}{(credential.owner_title || name) && ` · ${credential.owner_title || name}`}</p>
          </div>
        </div>
        {status}
      </div>
      <p className="text-xs leading-relaxed text-muted">{credential.unregistered ? t("providerKeys.unregisteredHint") : credential.description}</p>
      {credential.destination && <Link to={credential.destination} className="inline-flex items-center gap-1 text-xs text-accent hover:underline">
        {t("providerKeys.openOwner")}<ArrowUpRight size={13} />
      </Link>}
    </header>
    <div className="space-y-4 p-5">{children}</div>
  </section>;
}
