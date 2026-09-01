import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiErrorMessage } from "@/lib/api";
import { acfunApi } from "../api";
import { cn } from "@/lib/utils";
import {
  LogIn, LogOut, X, Loader2, CheckCircle, AlertCircle, QrCode, KeyRound, RefreshCw,
} from "lucide-react";

type Phase = "idle" | "submitting" | "success" | "error";
type QrPhase = "loading" | "wait" | "scaned" | "confirmed" | "timeout" | "error";
type LoginTab = "qr" | "password";

/** 扫码登录面板：QR 展示 + 1.5s 轮询状态机（wait/scaned/confirmed/timeout/error） */
function QrPanel({ onLoggedIn }: { onLoggedIn: () => void }) {
  const { t } = useTranslation("channel-acfun");
  const [phase, setPhase] = useState<QrPhase>("loading");
  const [qrPng, setQrPng] = useState("");
  const [error, setError] = useState("");
  const [account, setAccount] = useState("");
  const sessionRef = useRef<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollingRef = useRef(false);

  const stopPolling = () => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  };

  const discardSession = () => {
    stopPolling();
    const sid = sessionRef.current;
    sessionRef.current = null;
    if (sid) acfunApi.qrDiscard(sid).catch(() => undefined);
  };

  const start = async () => {
    discardSession();
    setPhase("loading");
    setError("");
    setAccount("");
    try {
      const { data } = await acfunApi.qrStart();
      sessionRef.current = data.session_id;
      setQrPng(data.qr_png);
      setPhase("wait");
      timerRef.current = setInterval(poll, 1500);
    } catch (e) {
      setPhase("error");
      setError(apiErrorMessage(e, t("failed")));
    }
  };

  const poll = async () => {
    const sid = sessionRef.current;
    if (!sid || pollingRef.current) return;
    pollingRef.current = true;
    try {
      const { data } = await acfunApi.qrStatus(sid);
      switch (data.status) {
        case "scaned":
          setPhase("scaned");
          break;
        case "confirmed":
          if (data.success === false) {
            setPhase("error");
            setError(data.error ?? "");
          } else {
            setPhase("confirmed");
            setAccount(data.username ?? "");
            stopPolling();
            sessionRef.current = null;
            onLoggedIn();
          }
          break;
        case "timeout":
          setPhase("timeout");
          setError(data.error ?? "");
          discardSession();
          break;
        case "error":
          setPhase("error");
          setError(data.error ?? "");
          discardSession();
          break;
        default:
          setPhase((p) => (p === "scaned" ? p : "wait"));
      }
    } catch {
      // 单次轮询失败静默，下一轮重试
    } finally {
      pollingRef.current = false;
    }
  };

  useEffect(() => {
    start();
    return () => discardSession();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="flex flex-col items-center gap-3">
      <div className="w-[220px] h-[220px] rounded-md border border-border bg-white flex items-center justify-center overflow-hidden">
        {phase === "loading" ? (
          <Loader2 size={26} className="animate-spin text-muted" />
        ) : qrPng ? (
          <img src={qrPng} alt="AcFun QR" className="w-full h-full object-contain" />
        ) : (
          <AlertCircle size={26} className="text-danger" />
        )}
      </div>
      <div className="text-center space-y-1 min-h-[32px]">
        {phase === "wait" && <p className="text-xs text-muted">{t("qrWaiting")}</p>}
        {phase === "scaned" && <p className="text-xs text-warn">{t("qrScanned")}</p>}
        {phase === "confirmed" && (
          <p className="flex items-center justify-center gap-1 text-xs text-ok">
            <CheckCircle size={14} />
            {t("success")}
            {account && <span className="font-mono text-[10px] opacity-70">{account}</span>}
          </p>
        )}
        {(phase === "error" || phase === "timeout") && (
          <p className="text-xs text-danger">
            {phase === "timeout" ? t("qrExpired") : t("failed")}
            {error ? `: ${error}` : ""}
          </p>
        )}
        {(phase === "wait" || phase === "scaned") && (
          <p className="text-[11px] text-muted">{t("qrHint")}</p>
        )}
      </div>
      {(phase === "error" || phase === "timeout") && (
        <button
          onClick={start}
          className="flex items-center gap-1 px-3 py-1.5 text-xs font-medium rounded-md bg-accent text-white hover:opacity-90 transition-all"
        >
          <RefreshCw size={12} /> {t("qrRetry")}
        </button>
      )}
    </div>
  );
}

/** 账密登录面板：账号/密码 + 可选图形验证码（点击刷新） */
function PasswordPanel({ onLoggedIn }: { onLoggedIn: () => void }) {
  const { t } = useTranslation("channel-acfun");
  const [phase, setPhase] = useState<Phase>("idle");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [captcha, setCaptcha] = useState("");
  const [captchaImage, setCaptchaImage] = useState("");
  const [captchaKey, setCaptchaKey] = useState("");
  const [showCaptcha, setShowCaptcha] = useState(false);
  const [captchaLoading, setCaptchaLoading] = useState(false);
  const [error, setError] = useState("");

  const fetchCaptcha = async () => {
    if (captchaLoading) return;
    setCaptchaLoading(true);
    try {
      const { data } = await acfunApi.captcha();
      setCaptchaImage(data.image);
      setCaptchaKey(data.key);
      setCaptcha("");
      setShowCaptcha(true);
    } catch (e) {
      setError(apiErrorMessage(e, t("captchaFailed")));
    } finally {
      setCaptchaLoading(false);
    }
  };

  const submit = async () => {
    if (!username.trim() || !password || phase === "submitting") return;
    setPhase("submitting");
    setError("");
    try {
      const { data } = await acfunApi.login({
        username: username.trim(),
        password,
        key: captchaKey || undefined,
        captcha: captcha || undefined,
      });
      if (data.success) {
        setPhase("success");
        setPassword("");
        setCaptcha("");
        onLoggedIn();
      } else {
        setPhase("error");
        setError(data.error_msg ?? "");
        if (data.need_captcha || (data.error_msg ?? "").includes("验证码")) {
          fetchCaptcha();
        }
      }
    } catch (e) {
      setPhase("error");
      setError(apiErrorMessage(e, t("failed")));
    }
  };

  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <label className="text-xs text-muted">{t("username")}</label>
        <input
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          placeholder={t("usernamePlaceholder")}
          autoComplete="username"
          className="w-full px-3 py-2 text-sm rounded-md border border-border bg-secondary text-foreground placeholder:text-muted focus:outline-none focus:border-accent"
        />
      </div>
      <div className="space-y-1">
        <label className="text-xs text-muted">{t("password")}</label>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
          placeholder={t("passwordPlaceholder")}
          autoComplete="current-password"
          className="w-full px-3 py-2 text-sm rounded-md border border-border bg-secondary text-foreground placeholder:text-muted focus:outline-none focus:border-accent"
        />
      </div>

      {showCaptcha ? (
        <div className="space-y-1">
          <label className="text-xs text-muted">{t("captcha")}</label>
          <div className="flex items-center gap-2">
            <input
              value={captcha}
              onChange={(e) => setCaptcha(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submit()}
              placeholder={t("captchaPlaceholder")}
              className="flex-1 px-3 py-2 text-sm rounded-md border border-border bg-secondary text-foreground placeholder:text-muted focus:outline-none focus:border-accent"
            />
            {captchaImage && (
              <img
                src={captchaImage}
                alt="captcha"
                title={t("captchaRefresh")}
                onClick={fetchCaptcha}
                className="h-[38px] rounded-md border border-border cursor-pointer bg-white"
              />
            )}
          </div>
        </div>
      ) : (
        <button
          onClick={fetchCaptcha}
          disabled={captchaLoading}
          className="text-[11px] text-muted hover:text-foreground transition-colors flex items-center gap-1"
        >
          {captchaLoading && <Loader2 size={11} className="animate-spin" />}
          {t("getCaptcha")}
        </button>
      )}

      {phase === "success" && (
        <p className="flex items-center gap-1.5 text-xs text-ok">
          <CheckCircle size={14} /> {t("success")}
        </p>
      )}
      {phase === "error" && (
        <p className="flex items-center gap-1.5 text-xs text-danger">
          <AlertCircle size={14} /> {t("failed")}
          {error ? `: ${error}` : ""}
        </p>
      )}
      <p className="text-[11px] text-muted">{t("hint")}</p>

      <div className="flex justify-end">
        <button
          onClick={submit}
          disabled={phase === "submitting" || !username.trim() || !password}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md bg-accent text-white hover:opacity-90 transition-all disabled:opacity-50"
        >
          {phase === "submitting" && <Loader2 size={12} className="animate-spin" />}
          {phase === "submitting" ? t("loggingIn") : t("submit")}
        </button>
      </div>
    </div>
  );
}

/**
 * AcFun 账号登录 — 扫码登录（推荐）/ 账号密码（含图形验证码）双通道。
 * 登录成功后凭据自动落盘（数据目录）并启动频道；已登录展示当前账号，可退出。
 */
export default function AcfunLogin({ compact = false }: { compact?: boolean }) {
  const { t } = useTranslation("channel-acfun");
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<LoginTab>("qr");
  const [loggingOut, setLoggingOut] = useState(false);
  const [error, setError] = useState("");

  const { data: status, refetch } = useQuery({
    queryKey: ["acfunLoginStatus"],
    queryFn: async () => (await acfunApi.status()).data,
    staleTime: 30_000,
  });

  const refreshChannelViews = () => {
    queryClient.invalidateQueries({ queryKey: ["adapters"] });
    queryClient.invalidateQueries({ queryKey: ["adapterConfigs"] });
    queryClient.invalidateQueries({ queryKey: ["acfunLoginStatus"] });
  };

  const logout = async () => {
    if (loggingOut) return;
    setLoggingOut(true);
    setError("");
    try {
      await acfunApi.logout();
      refreshChannelViews();
    } catch (e) {
      setError(apiErrorMessage(e, t("failed")));
    } finally {
      setLoggingOut(false);
    }
  };

  const close = () => setOpen(false);
  const logined = Boolean(status?.logined);

  return (
    <>
      <button
        onClick={() => {
          setOpen(true);
          refetch();
        }}
        title={t("login")}
        className={cn(
          "flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border transition-all",
          logined
            ? "border-[rgba(34,197,94,0.3)] text-ok hover:bg-ok-subtle"
            : "border-border text-muted hover:text-foreground hover:bg-hover",
          compact && "px-2",
        )}
      >
        <LogIn size={14} />
        {logined ? (status?.username || t("login")) : t("login")}
      </button>

      {open && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
          onClick={close}
        >
          <div
            className="w-[380px] rounded-lg border border-border bg-card p-5 space-y-4 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold text-heading">{t("loginTitle")}</h3>
              <button onClick={close} className="text-muted hover:text-foreground transition-colors">
                <X size={16} />
              </button>
            </div>

            {logined && status && (
              <div className="flex items-center justify-between px-3 py-2.5 rounded-md bg-ok-subtle border border-[rgba(34,197,94,0.2)] text-xs text-ok">
                <span className="flex items-center gap-1.5">
                  <CheckCircle size={14} />
                  {t("currentAccount")}: {status.username}
                  {status.uid && <span className="font-mono text-[10px] opacity-70">uid={status.uid}</span>}
                </span>
                <button
                  onClick={logout}
                  disabled={loggingOut}
                  className="flex items-center gap-1 px-2 py-1 text-[11px] font-medium rounded border border-[rgba(239,68,68,0.3)] text-danger hover:bg-danger-subtle transition-all disabled:opacity-70"
                >
                  {loggingOut ? <Loader2 size={12} className="animate-spin" /> : <LogOut size={12} />}
                  {t("logout")}
                </button>
              </div>
            )}

            {/* 登录方式 Tab */}
            <div className="flex rounded-md border border-border overflow-hidden">
              {(["qr", "password"] as LoginTab[]).map((key) => (
                <button
                  key={key}
                  onClick={() => setTab(key)}
                  className={cn(
                    "flex-1 flex items-center justify-center gap-1.5 py-1.5 text-xs font-medium transition-all",
                    tab === key
                      ? "bg-accent text-white"
                      : "bg-secondary text-muted hover:text-foreground",
                  )}
                >
                  {key === "qr" ? <QrCode size={13} /> : <KeyRound size={13} />}
                  {key === "qr" ? t("qrTab") : t("pwdTab")}
                </button>
              ))}
            </div>

            {error && <p className="text-xs text-danger">{error}</p>}
            {tab === "qr" ? (
              <QrPanel onLoggedIn={refreshChannelViews} />
            ) : (
              <PasswordPanel onLoggedIn={refreshChannelViews} />
            )}

            <div className="flex justify-end">
              <button
                onClick={close}
                className="px-3 py-1.5 text-xs font-medium rounded-md border border-border text-muted hover:text-foreground hover:bg-hover transition-all"
              >
                {t("close")}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
