import { useState } from "react";
import { useTranslation } from "react-i18next";
import { RefreshCw } from "lucide-react";
import { Button, ConfirmDialog } from "@/components/ui";
import { useDevopsOperation } from "./operations";

export default function RestartControl() {
  const { t } = useTranslation("devops");
  const { busy, phase, detail, run } = useDevopsOperation();
  const [confirm, setConfirm] = useState(false);
  return <div className="min-w-0 space-y-2">
    <Button size="sm" variant="secondary" disabled={busy} onClick={() => setConfirm(true)}><RefreshCw size={14} />{t("restartService")}</Button>
    {phase === "failed" && <p role="alert" className="break-words text-xs text-danger">{detail}</p>}
    {phase === "restarting" && <p role="status" className="text-xs text-muted">{t("restartingHint")}</p>}
    {phase === "ready" && <Button size="sm" onClick={() => window.location.reload()}>{t("reloadPage")}</Button>}
    <ConfirmDialog open={confirm} title={t("restartService")} message={t("restartConfirm")} onClose={() => setConfirm(false)} onConfirm={() => { setConfirm(false); void run("restart"); }} />
  </div>;
}
