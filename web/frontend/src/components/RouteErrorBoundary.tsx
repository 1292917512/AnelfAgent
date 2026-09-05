import { Component, type ErrorInfo, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui";

/** 懒加载 chunk 失效（重新构建后旧哈希文件不存在）的特征判定 */
function isChunkLoadError(error: Error): boolean {
  return (
    error.name === "ChunkLoadError" ||
    /loading chunk|dynamically imported module|module script failed/i.test(error.message)
  );
}

const RELOAD_GUARD_KEY = "route-chunk-reload-at";

/** chunk 失效时自动刷新一次自愈；10s 守卫防止服务端持续异常时刷新死循环 */
function tryAutoReload(): boolean {
  const last = Number(sessionStorage.getItem(RELOAD_GUARD_KEY) ?? 0);
  if (Date.now() - last < 10_000) return false;
  sessionStorage.setItem(RELOAD_GUARD_KEY, String(Date.now()));
  window.location.reload();
  return true;
}

/** 错误回退 UI：函数组件以便使用 i18n hook */
function RouteErrorFallback({ error }: { error: Error }) {
  const { t } = useTranslation("common");
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
      <AlertTriangle size={28} className="text-warn" />
      <p className="text-sm font-medium text-heading">{t("pageErrorTitle")}</p>
      <p className="text-xs text-muted max-w-md break-all">{t("pageErrorDesc")}</p>
      <p className="text-[11px] text-muted opacity-60 max-w-md break-all">{error.message}</p>
      <Button variant="secondary" size="sm" onClick={() => window.location.reload()}>
        <RefreshCw size={12} /> {t("refresh")}
      </Button>
    </div>
  );
}

/**
 * 路由级错误边界：兜住页面懒加载/渲染异常。
 * 无边界时任何页面渲染异常会卸载整个 React 根（整页黑屏），
 * 这里收敛为内容区回退 UI，侧边栏与导航保持可用；
 * chunk 失效（发版后旧标签页）优先自动刷新一次自愈。
 */
export class RouteErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("[RouteErrorBoundary]", error, info.componentStack);
    if (isChunkLoadError(error)) tryAutoReload();
  }

  render() {
    const { error } = this.state;
    if (error) return <RouteErrorFallback error={error} />;
    return this.props.children;
  }
}
