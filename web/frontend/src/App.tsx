import { lazy, Suspense, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { Layout } from "./components/layout/Layout";
import { AuthGate } from "./components/AuthGate";
import { Toaster } from "./components/ui/Toast";
import { ApprovalDialog } from "./components/ApprovalDialog";
import { CommandPalette } from "./components/palette/CommandPalette";
import { useAppStore } from "./stores/app-store";
import { configApi } from "./lib/api";
import { warnApiError } from "@/lib/api";
import { CORE_ROUTES } from "@/lib/core-routes";
import { listPluginRoutes } from "@/lib/channel-plugins";
import { RouteErrorBoundary } from "@/components/RouteErrorBoundary";

// 页面模块自动发现：pages/*.tsx 按文件名映射路由 path
// 命名约定：Share.tsx → /share, Dashboard.tsx → /dashboard
const pageModules = import.meta.glob("./pages/*.tsx");

// lazy 组件必须模块级缓存：render 中重复调用 lazy() 会被 React 视为新组件类型，
// 导致页面每次渲染都卸载重挂（状态丢失）
const pageCache = new Map<string, React.LazyExoticComponent<React.ComponentType>>();

function lazyPage(name: string) {
  let comp = pageCache.get(name);
  if (comp) return comp;
  const loader = pageModules[`./pages/${name}.tsx`];
  if (!loader) return null;
  comp = lazy(loader as () => Promise<{ default: React.ComponentType }>);
  pageCache.set(name, comp);
  return comp;
}

export default function App() {
  const { t } = useTranslation();
  const setConfig = useAppStore((s) => s.setConfig);

  useEffect(() => {
    configApi.webui().then((r) => {
      const data = r.data;
      setConfig({
        branding: data.branding,
        navigation: data.navigation,
      });
    }).catch(warnApiError);
    // chat SSE 由 Chat 页启动（startSSE 幂等，重复进入不会重建连接）
  }, [setConfig]);

  return (
    <AuthGate>
      <BrowserRouter basename="/webui">
        <Routes>
          <Route element={<Layout />}>
            {CORE_ROUTES.map((r) => {
              if (r.redirectTo) {
                return (
                  <Route
                    key={r.path ?? "index"}
                    path={r.path}
                    element={<Navigate to={r.redirectTo} replace />}
                  />
                );
              }
              const Page = r.page ? lazyPage(r.page) : null;
              if (!Page) return null;
              const element = (
                <RouteErrorBoundary>
                  <Suspense fallback={<div className="p-4 text-muted">{t("common:loading")}</div>}>
                    <Page />
                  </Suspense>
                </RouteErrorBoundary>
              );
              return r.index ? (
                <Route key="index" index element={element} />
              ) : (
                <Route key={r.path} path={r.path} element={element} />
              );
            })}
            {listPluginRoutes().map((r) => (
              <Route
                key={`plugin:${r.path}`}
                path={r.path}
                element={
                  <RouteErrorBoundary>
                    <Suspense fallback={<div className="p-4 text-muted">{t("common:loading")}</div>}>
                      <r.page />
                    </Suspense>
                  </RouteErrorBoundary>
                }
              />
            ))}
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
        <CommandPalette />
      </BrowserRouter>
      <Toaster />
      <ApprovalDialog />
    </AuthGate>
  );
}
