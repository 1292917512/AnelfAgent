import { Suspense, useEffect, useState } from "react";
import { createBrowserRouter, RouterProvider, useLocation } from "react-router-dom";
import { FileDraftLifecycle } from "./components/layout/FileDraftLifecycle";
import { Layout } from "./components/layout/Layout";
import { AuthGate } from "./components/AuthGate";
import { Toaster } from "./components/ui/Toast";
import { ApprovalDialog } from "./components/ApprovalDialog";
import { CommandPalette } from "./components/palette/CommandPalette";
import { useChatStore } from "./stores/chat-store";
import { useThinkingStore } from "./stores/thinking-store";
import { useAppStore } from "./stores/app-store";
import { configApi, warnApiError } from "./lib/api";
import { CORE_ROUTES } from "./lib/core-routes";
import { listPluginRoutes } from "./lib/channel-plugins";
import { RouteErrorBoundary } from "./components/RouteErrorBoundary";
import { PageSkeleton } from "./components/common/AsyncState";
import { NotFound } from "./components/layout/NotFound";
import { QueryErrorResetBoundary } from "@tanstack/react-query";
import { UnsavedChangesProvider } from "./components/common/UnsavedChanges";

function ApplicationLayout() {
  return <UnsavedChangesProvider><FileDraftLifecycle /><Layout /><CommandPalette /></UnsavedChangesProvider>;
}

function PageBoundary({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  return (
    <QueryErrorResetBoundary>
      {({ reset }) => <RouteErrorBoundary key={location.pathname} onReset={reset}>
        <Suspense fallback={<PageSkeleton />}>{children}</Suspense>
      </RouteErrorBoundary>}
    </QueryErrorResetBoundary>
  );
}

export function createAppRoutes() {
  return [{
    element: <ApplicationLayout />,
    children: [
      ...CORE_ROUTES.map(({ path, page: Page }) => ({ path, element: <PageBoundary><Page /></PageBoundary> })),
      ...listPluginRoutes().map(({ path, page: Page }) => ({ path, element: <PageBoundary><Page /></PageBoundary> })),
      { path: "*", element: <NotFound /> },
    ],
  }];
}

function AuthenticatedApp() {
  const [router] = useState(() => createBrowserRouter(createAppRoutes(), { basename: "/webui" }));
  useEffect(() => {
    useChatStore.getState().startSSE();
    return () => { useChatStore.getState().stopSSE(); useThinkingStore.getState().shutdown(); };
  }, []);
  const setConfig = useAppStore((state) => state.setConfig);
  useEffect(() => {
    configApi.webui().then(({ data }) => setConfig({ branding: data.branding })).catch(warnApiError);
  }, [setConfig]);
  return <><RouterProvider router={router} /><ApprovalDialog /></>;
}

export default function App() {
  return <><AuthGate><AuthenticatedApp /></AuthGate><Toaster /></>;
}
