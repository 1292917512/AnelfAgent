import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { AlertCircle, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { apiErrorMessage } from "@/lib/api/client";

export function PageSkeleton() {
  const { t } = useTranslation("common");
  return (
    <div role="status" aria-label={t("loading")} className="space-y-5 py-2">
      <div className="skeleton h-7 w-44" />
      <div className="skeleton h-4 w-72 max-w-full" />
      <div className="grid gap-4 sm:grid-cols-3">{[0, 1, 2].map((key) => <div key={key} className="skeleton h-28" />)}</div>
      <div className="skeleton h-64" />
    </div>
  );
}

export function QueryError({ error, retry, compact = false }: { error: unknown; retry?: () => void; compact?: boolean }) {
  const { t } = useTranslation("common");
  return (
    <div role="alert" className={compact ? "flex flex-wrap items-center gap-3 rounded-lg border border-danger/20 bg-danger-subtle p-3" : "flex flex-col items-center gap-3 rounded-xl border border-border bg-card px-6 py-12 text-center"}>
      <AlertCircle size={compact ? 18 : 28} className="shrink-0 text-danger" />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-heading">{t("loadFailed")}</p>
        <p className="mt-1 text-sm text-muted break-words">{apiErrorMessage(error, t("requestFailed"))}</p>
      </div>
      {retry && <Button size="sm" onClick={retry}><RefreshCw size={14} />{t("retry")}</Button>}
    </div>
  );
}

export function AsyncState({ pending, error, retry, children }: {
  pending: boolean; error?: unknown; retry?: () => void; children: ReactNode;
}) {
  if (pending) return <PageSkeleton />;
  if (error) return <QueryError error={error} retry={retry} />;
  return <>{children}</>;
}
