import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ArrowRight, ArrowUpRight, Boxes, KeyRound, Radio, Settings2 } from "lucide-react";
import type { ProviderKeyEntry } from "@/lib/types";
import { Badge, Modal } from "@/components/ui";

/** 凭据目录只展示定长摘要，编辑内容由二级界面承载。 */
export function CredentialCard({ credential, onEdit }: {
  credential: ProviderKeyEntry; onEdit: () => void;
}) {
  const { t } = useTranslation("common");
  const [kind, name] = credential.owner.split(":");
  const Icon = kind === "entity" ? Boxes : kind === "channel" ? Radio : kind === "core" ? Settings2 : KeyRound;
  const owner = t(`providerKeys.owner.${kind || "unregistered"}`);
  return <section id={`credential-${credential.name}`} aria-label={credential.title} className="min-w-0 scroll-mt-24">
    <button type="button" onClick={onEdit} aria-haspopup="dialog" aria-label={t("providerKeys.editCredential", { name: credential.title })}
      className="group flex h-full w-full flex-col rounded-xl border border-border bg-card p-4 text-left transition-colors hover:border-accent/50 hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
      <div className="flex w-full min-w-0 items-start gap-3">
        <span className="shrink-0 rounded-lg bg-accent/10 p-2.5 text-accent"><Icon size={18} /></span>
        <div className="min-w-0 flex-1">
          <h3 className="line-clamp-2 h-10 break-words text-sm font-semibold leading-5 text-heading" title={credential.title}>{credential.title}</h3>
          <p className="mt-1 truncate text-xs text-muted" title={`${owner} · ${credential.owner_title || name || ""}`}>{owner}{(credential.owner_title || name) && ` · ${credential.owner_title || name}`}</p>
        </div>
      </div>
      <p className="my-4 line-clamp-2 h-10 break-words text-xs leading-5 text-muted">{credential.unregistered ? t("providerKeys.unregisteredHint") : credential.description}</p>
      <div className="mt-auto flex w-full items-center justify-between gap-2 border-t border-border/60 pt-3">
        <Badge variant={credential.configured ? "ok" : "neutral"}>{t(credential.configured === null ? "providerKeys.authorization" : credential.configured ? "providerKeys.configured" : "providerKeys.notConfigured")}</Badge>
        <span className="inline-flex shrink-0 items-center gap-1 text-xs text-accent">{t("providerKeys.manage")}<ArrowRight size={14} /></span>
      </div>
    </button>
  </section>;
}

/** 通用字段和模块授权编辑器共享详情标题、归属导航与弹层。 */
export function CredentialDialog({ credential, onClose, children, footer, busy = false, open = true, onAfterClose }: {
  credential: ProviderKeyEntry; onClose: () => void; children: ReactNode; footer?: ReactNode; busy?: boolean;
  open?: boolean; onAfterClose?: () => void;
}) {
  const { t } = useTranslation("common");
  return <Modal open={open} onAfterClose={onAfterClose} onClose={onClose} title={credential.title} width="max-w-xl" footer={footer} dismissible={!busy}>
    <div className="mb-5 space-y-2 border-b border-border/60 pb-4">
      <p className="break-words text-xs leading-relaxed text-muted">{credential.unregistered ? t("providerKeys.unregisteredHint") : credential.description}</p>
      {credential.destination && <Link to={credential.destination} className="inline-flex items-center gap-1 text-xs text-accent hover:underline">
        {t("providerKeys.openOwner")}<ArrowUpRight size={13} />
      </Link>}
    </div>
    {children}
  </Modal>;
}
