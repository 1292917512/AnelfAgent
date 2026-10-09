import { Component, type ErrorInfo, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { apiErrorMessage } from "@/lib/api/client";
import { recoverChunk } from "@/lib/chunk-recovery";

function RouteErrorFallback({ error, retry }: { error: Error; retry: () => void }) {
  const { t } = useTranslation("common");
  return (
    <div role="alert" className="mx-auto flex max-w-xl flex-col items-center gap-4 py-16 text-center">
      <div className="rounded-2xl bg-danger-subtle p-4 text-danger"><AlertTriangle size={28} /></div>
      <h1 className="text-lg font-semibold text-heading">{t("pageErrorTitle")}</h1>
      <p className="text-sm text-muted">{t("pageErrorDesc")}</p>
      <p className="max-w-full break-words text-xs text-muted">{apiErrorMessage(error, t("requestFailed"))}</p>
      <div className="flex gap-2">
        <Button onClick={retry}><RefreshCw size={14} />{t("retry")}</Button>
        <Button variant="ghost" onClick={() => window.location.reload()}>{t("refresh")}</Button>
      </div>
    </div>
  );
}

export class RouteErrorBoundary extends Component<{ children: ReactNode; onReset?: () => void }, { error: Error | null }> {
  state: { error: Error | null } = { error: null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("[RouteErrorBoundary]", error, info.componentStack);
    if (error.name === "ChunkLoadError" || /loading chunk|dynamically imported module|module script failed/i.test(error.message)) recoverChunk();
  }
  render() {
    if (this.state.error) return <RouteErrorFallback error={this.state.error} retry={() => {
      this.props.onReset?.();
      this.setState({ error: null });
    }} />;
    return this.props.children;
  }
}
