import { useState } from "react";
import { useTranslation } from "react-i18next";
import { KeyRound, Lock, ShieldCheck, Zap } from "lucide-react";
import { vaultApi, vaultErrorMessage } from "./api";
import { toast } from "@/stores/toast-store";

interface UnlockScreenProps {
  mode: "setup" | "unlock";
  /** 机器密钥模式：解锁无需密码（异常恢复场景，正常自动解锁不会到此） */
  machine?: boolean;
  onDone: () => void;
}

const inputCls =
  "w-full px-3 py-2 text-sm rounded-md border border-border bg-elevated text-heading placeholder:text-muted focus:outline-none focus:border-accent transition-all";

/** 初始化密码本（机器密钥/主密码双模式）或解锁（主密码模式）。 */
export function UnlockScreen({ mode, machine = false, onDone }: UnlockScreenProps) {
  const { t } = useTranslation("vault");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [pending, setPending] = useState(false);

  const run = async (action: () => Promise<unknown>, successKey: string, failKey: string) => {
    setPending(true);
    try {
      await action();
      toast.success(t(successKey));
      onDone();
    } catch (err) {
      toast.error(vaultErrorMessage(err, t(failKey)));
    } finally {
      setPending(false);
    }
  };

  const quickSetup = () =>
    run(() => vaultApi.setup("machine"), "unlock.setupSuccess", "unlock.setupFailed");

  const masterSetup = () => {
    if (password.length < 8) {
      toast.error(t("unlock.tooShort"));
      return;
    }
    if (password !== confirm) {
      toast.error(t("unlock.mismatch"));
      return;
    }
    void run(() => vaultApi.setup("master", password), "unlock.setupSuccess", "unlock.setupFailed");
  };

  const unlock = () =>
    run(() => vaultApi.unlock(password), "unlock.unlockSuccess", "unlock.unlockFailed");

  if (mode === "setup") {
    return (
      <div className="flex flex-col items-center justify-center py-12">
        <div className="w-full max-w-md space-y-4">
          <div className="flex flex-col items-center gap-2 text-center">
            <div className="p-3 rounded-full bg-accent-subtle border border-accent">
              <ShieldCheck size={22} className="text-accent" />
            </div>
            <h2 className="text-base font-semibold text-heading">{t("unlock.setupTitle")}</h2>
          </div>

          <button
            onClick={quickSetup}
            disabled={pending}
            className="w-full p-4 rounded-xl border border-accent bg-accent-subtle text-left hover:opacity-90 disabled:opacity-50 transition-all"
          >
            <div className="flex items-center gap-2 text-sm font-medium text-accent">
              <Zap size={15} /> {t("unlock.quickTitle")}
            </div>
            <p className="text-xs text-muted mt-1 leading-relaxed">
              {t("unlock.quickDesc")}
            </p>
          </button>

          <div className="p-4 rounded-xl border border-border bg-elevated space-y-3">
            <div className="flex items-center gap-2 text-sm font-medium text-heading">
              <Lock size={15} /> {t("unlock.masterTitle")}
            </div>
            <p className="text-xs text-muted leading-relaxed">{t("unlock.masterDesc")}</p>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={t("unlock.passwordPlaceholder")}
              className={inputCls}
            />
            <input
              type="password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && masterSetup()}
              placeholder={t("unlock.confirmPlaceholder")}
              className={inputCls}
            />
            <button
              onClick={masterSetup}
              disabled={pending || !password}
              className="w-full flex items-center justify-center gap-1.5 px-3 py-2 text-sm font-medium rounded-md border border-border bg-elevated text-heading hover:bg-hover disabled:opacity-50 transition-all"
            >
              <KeyRound size={14} /> {t("unlock.masterAction")}
            </button>
            <p className="text-[11px] text-muted leading-relaxed">
              {t("unlock.setupWarning")}
            </p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-center justify-center py-16">
      <div className="w-full max-w-sm space-y-5 p-6 rounded-xl border border-border bg-elevated">
        <div className="flex flex-col items-center gap-2 text-center">
          <div className="p-3 rounded-full bg-accent-subtle border border-accent">
            <Lock size={22} className="text-accent" />
          </div>
          <h2 className="text-base font-semibold text-heading">{t("unlock.unlockTitle")}</h2>
          <p className="text-xs text-muted leading-relaxed">{t("unlock.unlockDesc")}</p>
        </div>
        <div className="space-y-3">
          {!machine && (
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && unlock()}
              placeholder={t("unlock.passwordPlaceholder")}
              className={inputCls}
              autoFocus
            />
          )}
          <button
            onClick={unlock}
            disabled={pending || (!machine && !password)}
            className="w-full flex items-center justify-center gap-1.5 px-3 py-2 text-sm font-medium rounded-md bg-accent text-white hover:opacity-90 disabled:opacity-50 transition-all"
          >
            <KeyRound size={14} />
            {pending ? t("unlock.pending") : t("unlock.unlockAction")}
          </button>
        </div>
      </div>
    </div>
  );
}
