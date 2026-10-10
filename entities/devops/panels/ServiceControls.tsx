import { useState } from "react";
import { useTranslation } from "react-i18next";
import { GitBranch, Hammer, RefreshCw, Rocket } from "lucide-react";
import { Button, ConfirmDialog } from "@/components/ui";
import { useDevopsOperation, type DevopsAction } from "./operations";

const actions = {
  restart: { label: "restartService", confirm: "restartConfirm", icon: RefreshCw },
  update: { label: "updateAndRestart", confirm: "updateRestartConfirm", icon: Rocket },
  build: { label: "buildAndRestart", confirm: "buildRestartConfirm", icon: Hammer },
  pull: { label: "pullCode", confirm: "pullConfirm", icon: GitBranch },
};

export default function ServiceControls({ full = false }: { full?: boolean }) {
  const { t } = useTranslation("devops");
  const { busy, phase, detail, run } = useDevopsOperation();
  const [confirm, setConfirm] = useState<DevopsAction | null>(null);
  const visible: DevopsAction[] = full ? ["restart", "update", "build", "pull"] : ["restart", "update"];
  const progress = phase === "ready" ? "serviceReady" : phase === "updated" ? "pullSuccess" : phase === "restarting" ? "restartingHint" : phase === "building" ? "buildingHint" : "pulling";
  return <div className="space-y-3">
    <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,190px),1fr))] gap-2">
      {visible.map((action) => {
        const { icon: Icon, label } = actions[action];
        return <Button key={action} variant={action === "update" ? "primary" : "secondary"} className="h-auto min-h-11 whitespace-normal py-2 text-left" disabled={busy} onClick={() => setConfirm(action)}>
          <Icon size={16} className="shrink-0" />{t(label)}
        </Button>;
      })}
    </div>
    {phase !== "idle" && <div role={phase === "failed" ? "alert" : "status"} className={`rounded-xl border px-3 py-2.5 text-xs leading-relaxed ${phase === "failed" ? "border-danger/30 bg-danger-subtle text-danger" : "border-border bg-elevated text-muted"}`}>
      {phase !== "failed" && <p>{t(progress)}</p>}
      {detail && <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-words font-mono">{detail}</pre>}
      {phase === "ready" && <Button className="mt-2" size="sm" onClick={() => window.location.reload()}><RefreshCw size={14} />{t("reloadPage")}</Button>}
    </div>}
    <ConfirmDialog open={confirm !== null} title={confirm ? t(actions[confirm].label) : ""} message={confirm ? t(actions[confirm].confirm) : ""}
      onClose={() => setConfirm(null)} onConfirm={() => { if (confirm) void run(confirm); setConfirm(null); }} />
  </div>;
}
