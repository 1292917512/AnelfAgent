import { useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, KeyRound, ShieldAlert, ShieldCheck, ShieldX } from "lucide-react";
import { vaultApi, vaultErrorMessage } from "./api";
import { Card } from "@/components/common/Card";
import { toast } from "@/stores/toast-store";
import type { VaultBreachReport } from "./types";

const inputCls =
  "w-full px-3 py-2 text-sm rounded-md border border-border bg-elevated text-heading placeholder:text-muted focus:outline-none focus:border-accent transition-all";
const labelCls = "block text-xs font-medium text-muted mb-1";

/** 安全体检：HIBP 泄露检查 + 重复/弱密码分析 + 主密码保护管理。 */
export function SecurityPanel({
  unlockMode,
  onModeChanged,
  onLocked,
}: {
  unlockMode: "machine" | "master";
  onModeChanged: () => void;
  onLocked: () => void;
}) {
  const { t } = useTranslation("vault");
  const [report, setReport] = useState<VaultBreachReport | null>(null);
  const [checking, setChecking] = useState(false);

  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [changing, setChanging] = useState(false);

  const runCheck = async () => {
    setChecking(true);
    try {
      const { data } = await vaultApi.breachCheck();
      setReport(data);
    } catch (err) {
      toast.error(vaultErrorMessage(err, t("messages.breachFailed")));
    } finally {
      setChecking(false);
    }
  };

  const changeMaster = async () => {
    if (newPassword.length < 8) {
      toast.error(t("unlock.tooShort"));
      return;
    }
    if (newPassword !== confirmPassword) {
      toast.error(t("unlock.mismatch"));
      return;
    }
    setChanging(true);
    try {
      if (unlockMode === "machine") {
        await vaultApi.masterEnable(newPassword);
        toast.success(t("security.enableMasterSuccess"));
      } else {
        await vaultApi.changeMaster(oldPassword, newPassword);
        toast.success(t("security.changeMasterSuccess"));
      }
      setOldPassword("");
      setNewPassword("");
      setConfirmPassword("");
      onModeChanged();
    } catch (err) {
      toast.error(vaultErrorMessage(err, t("security.changeMasterFailed")));
    } finally {
      setChanging(false);
    }
  };

  const disableMaster = async () => {
    if (!window.confirm(t("security.confirmDisableMaster"))) {
      return;
    }
    try {
      await vaultApi.masterDisable();
      toast.success(t("security.disableMasterSuccess"));
      onModeChanged();
    } catch (err) {
      toast.error(vaultErrorMessage(err, t("security.changeMasterFailed")));
    }
  };

  const clean = report &&
    report.pwned.length === 0 && report.reused.length === 0 && report.weak.length === 0;

  return (
    <div className="space-y-4">
      <Card
        title={t("security.title")}
        subtitle={t("security.subtitle")}
        actions={
          <button
            onClick={runCheck}
            disabled={checking}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md bg-accent text-white hover:opacity-90 disabled:opacity-50 transition-all"
          >
            <ShieldCheck size={14} />
            {checking ? t("security.checking") : t("security.runCheck")}
          </button>
        }
      >
        {!report ? (
          <div className="py-8 text-center text-sm text-muted">
            {t("security.hint")}
          </div>
        ) : (
          <div className="space-y-4">
            <div className="text-xs text-muted">
              {t("security.checked", { count: report.checked_entries })}
              {!report.hibp_checked && (
                <span className="ml-2 text-warn">
                  {report.hibp_error
                    ? t("security.hibpError", { error: report.hibp_error })
                    : t("security.hibpSkipped")}
                </span>
              )}
            </div>

            {clean && (
              <div className="flex items-center gap-2 p-3 rounded-lg border border-ok/40 bg-ok/10 text-ok text-sm">
                <ShieldCheck size={16} /> {t("security.allClean")}
              </div>
            )}

            {report.pwned.length > 0 && (
              <section className="space-y-2">
                <h3 className="flex items-center gap-1.5 text-sm font-medium text-danger">
                  <ShieldX size={15} /> {t("security.pwnedTitle", { count: report.pwned.length })}
                </h3>
                {report.pwned.map((item) => (
                  <div key={item.id}
                    className="flex items-center justify-between p-2.5 rounded-md border border-danger/40 bg-danger/10 text-sm">
                    <span className="text-heading">{item.title}</span>
                    <span className="text-xs text-danger">
                      {t("security.pwnedCount", { count: item.count })}
                    </span>
                  </div>
                ))}
              </section>
            )}

            {report.reused.length > 0 && (
              <section className="space-y-2">
                <h3 className="flex items-center gap-1.5 text-sm font-medium text-warn">
                  <AlertTriangle size={15} /> {t("security.reusedTitle", { count: report.reused.length })}
                </h3>
                {report.reused.map((group, i) => (
                  <div key={i}
                    className="p-2.5 rounded-md border border-warn/40 bg-warn/10 text-sm">
                    <span className="text-xs text-warn">
                      {t("security.reusedCount", { count: group.count })}
                    </span>
                    <span className="ml-2 text-heading">
                      {group.entries.map((e) => e.title).join(" / ")}
                    </span>
                  </div>
                ))}
              </section>
            )}

            {report.weak.length > 0 && (
              <section className="space-y-2">
                <h3 className="flex items-center gap-1.5 text-sm font-medium text-warn">
                  <ShieldAlert size={15} /> {t("security.weakTitle", { count: report.weak.length })}
                </h3>
                {report.weak.map((item) => (
                  <div key={item.id}
                    className="flex items-center justify-between p-2.5 rounded-md border border-border bg-elevated text-sm">
                    <span className="text-heading">{item.title}</span>
                    <span className="text-xs text-muted">
                      {t("generator.entropy", { bits: item.entropy })}
                      {item.issues.length > 0 && ` · ${item.issues.join("、")}`}
                    </span>
                  </div>
                ))}
              </section>
            )}
          </div>
        )}
      </Card>

      <Card
        title={unlockMode === "machine"
          ? t("security.enableMasterTitle")
          : t("security.changeMasterTitle")}
        subtitle={unlockMode === "machine"
          ? t("security.enableMasterSubtitle")
          : t("security.changeMasterSubtitle")}
      >
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          {unlockMode === "master" && (
            <div>
              <label className={labelCls}>{t("security.oldPassword")}</label>
              <input type="password" value={oldPassword}
                onChange={(e) => setOldPassword(e.target.value)} className={inputCls} />
            </div>
          )}
          <div>
            <label className={labelCls}>
              {unlockMode === "machine" ? t("security.masterPassword") : t("security.newPassword")}
            </label>
            <input type="password" value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)} className={inputCls} />
          </div>
          <div>
            <label className={labelCls}>{t("security.confirmPassword")}</label>
            <input type="password" value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)} className={inputCls} />
          </div>
        </div>
        <div className="mt-3 flex items-center gap-3 flex-wrap">
          <button
            onClick={changeMaster}
            disabled={changing || !newPassword || (unlockMode === "master" && !oldPassword)}
            className="flex items-center gap-1.5 px-3 py-2 text-sm font-medium rounded-md bg-accent text-white hover:opacity-90 disabled:opacity-50 transition-all"
          >
            <KeyRound size={14} />
            {unlockMode === "machine"
              ? t("security.enableMasterAction")
              : t("security.changeMasterAction")}
          </button>
          {unlockMode === "master" && (
            <>
              <button
                onClick={disableMaster}
                className="px-3 py-2 text-sm font-medium rounded-md border border-warn/40 bg-warn/10 text-warn hover:bg-warn/20 transition-all"
              >
                {t("security.disableMasterAction")}
              </button>
              <button
                onClick={async () => {
                  await vaultApi.lock();
                  onLocked();
                }}
                className="px-3 py-2 text-sm font-medium rounded-md border border-border bg-elevated text-muted hover:bg-hover transition-all"
              >
                {t("actions.lockNow")}
              </button>
            </>
          )}
        </div>
      </Card>
    </div>
  );
}
