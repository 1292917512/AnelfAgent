import { useState } from "react";
import { useTranslation } from "react-i18next";
import { LockKeyhole } from "lucide-react";
import { ConfirmDialog, Switch } from "@/components/ui";

/** A session-only editing cover; unlocking never changes stored model settings. */
export function EmbeddingEditLock({ unlocked, disabled, onChange }: {
  unlocked: boolean; disabled: boolean; onChange: (value: boolean) => void;
}) {
  const { t } = useTranslation("memory");
  const [confirming, setConfirming] = useState(false);
  return <div className="space-y-2 border-t border-border pt-4">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h3 className="text-sm font-semibold text-heading">{t("cognee.embeddingConfigTitle")}</h3>
      <div className="flex items-center gap-2" title={t("cognee.embeddingLockToggleHint")}>
        <span className="text-xs text-muted">{t("cognee.embeddingUnlock")}</span>
        <Switch checked={unlocked} label={t("cognee.embeddingUnlock")} disabled={disabled}
          onChange={(value) => { if (value) setConfirming(true); else onChange(false); }} />
      </div>
    </div>
    <p className="flex items-start gap-2 text-xs leading-relaxed text-muted">
      <LockKeyhole size={14} className="mt-0.5 shrink-0" aria-hidden />
      {t(unlocked ? "cognee.embeddingUnlockedHint" : "cognee.embeddingLockedHint")}
    </p>
    <ConfirmDialog open={confirming} danger title={t("cognee.embeddingUnlockConfirmTitle")}
      message={t("cognee.embeddingUnlockConfirm")} onClose={() => setConfirming(false)}
      onConfirm={() => { if (!disabled) onChange(true); setConfirming(false); }} />
  </div>;
}
