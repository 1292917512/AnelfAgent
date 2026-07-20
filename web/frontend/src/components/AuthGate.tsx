import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useAuthStore } from "@/stores/auth-store";
import { useAppStore } from "@/stores/app-store";
import { Button, Input, Spinner } from "@/components/ui";
import { Lock } from "lucide-react";

export function AuthGate({ children }: { children: ReactNode }) {
  const { t } = useTranslation("common");
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
      <div className="fixed inset-0 flex items-center justify-center bg-bg">
        <Spinner size={24} />
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
    <div className="fixed inset-0 flex items-center justify-center bg-bg">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm mx-4 p-8 rounded-2xl border border-border bg-card shadow-2xl"
      >
        <div className="flex flex-col items-center gap-2 mb-6">
          <div className="w-12 h-12 rounded-full bg-accent/10 flex items-center justify-center">
            <Lock size={22} className="text-accent" />
          </div>
          <h1 className="text-lg font-bold text-heading">
            {branding.title}
          </h1>
          <p className="text-xs text-muted">{branding.subtitle}</p>
        </div>

        <Input
          type="password"
          autoFocus
          autoComplete="current-password"
          placeholder={t("passwordPlaceholder")}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="!h-10"
        />

        {error && (
          <p className="mt-2 text-xs text-danger">{error}</p>
        )}

        <Button
          type="submit"
          variant="primary"
          disabled={!password.trim()}
          loading={loading}
          className="w-full mt-4 !h-10"
        >
          {loading ? t("verifying") : t("login")}
        </Button>
      </form>
    </div>
  );
}
