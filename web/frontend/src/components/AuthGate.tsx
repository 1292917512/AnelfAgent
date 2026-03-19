import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { useAuthStore } from "@/stores/auth-store";
import { useAppStore } from "@/stores/app-store";
import { Lock } from "lucide-react";

export function AuthGate({ children }: { children: ReactNode }) {
  const { checked, required, authenticated, error, checkAuth, login } =
    useAuthStore();
  const branding = useAppStore((s) => s.branding);
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    checkAuth();
  }, [checkAuth]);

  if (!checked) {
    return (
      <div className="fixed inset-0 flex items-center justify-center bg-[var(--bg)]">
        <p className="text-sm text-[var(--muted)]">Loading…</p>
      </div>
    );
  }

  if (!required || authenticated) {
    return <>{children}</>;
  }

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!password.trim() || loading) return;
    setLoading(true);
    await login(password);
    setLoading(false);
  };

  return (
    <div className="fixed inset-0 flex items-center justify-center bg-[var(--bg)]">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm mx-4 p-8 rounded-2xl border border-[var(--border)] bg-[var(--bg-surface)] shadow-2xl"
      >
        <div className="flex flex-col items-center gap-2 mb-6">
          <div className="w-12 h-12 rounded-full bg-[var(--accent)]/10 flex items-center justify-center">
            <Lock size={22} className="text-[var(--accent)]" />
          </div>
          <h1 className="text-lg font-bold text-[var(--text-strong)]">
            {branding.title}
          </h1>
          <p className="text-xs text-[var(--muted)]">{branding.subtitle}</p>
        </div>

        <input
          type="password"
          autoFocus
          autoComplete="current-password"
          placeholder="输入访问密码"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="w-full px-4 py-2.5 text-sm rounded-[var(--radius-md)]
            bg-[var(--bg-elevated)] border border-[var(--border)]
            text-[var(--text-strong)] placeholder:text-[var(--muted)]
            focus:outline-none focus:border-[var(--accent)] transition-colors"
        />

        {error && (
          <p className="mt-2 text-xs text-[var(--error)]">{error}</p>
        )}

        <button
          type="submit"
          disabled={loading || !password.trim()}
          className="w-full mt-4 py-2.5 text-sm font-semibold rounded-[var(--radius-md)]
            bg-[var(--accent)] text-white hover:opacity-90 transition-all
            disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {loading ? "验证中…" : "登录"}
        </button>
      </form>
    </div>
  );
}
