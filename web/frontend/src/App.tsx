import { lazy, Suspense, useEffect } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { Layout } from "./components/layout/Layout";
import { AuthGate } from "./components/AuthGate";
import { Toaster } from "./components/ui/Toast";
import { ApprovalDialog } from "./components/ApprovalDialog";
import { CommandPalette } from "./components/palette/CommandPalette";
import { useAppStore } from "./stores/app-store";
import { useChatStore } from "./stores/chat-store";
import { configApi } from "./lib/api";

// 页面模块自动发现：pages/*.tsx 按文件名映射路由 path
// 命名约定：Share.tsx → /share, Dashboard.tsx → /dashboard
const pageModules = import.meta.glob("./pages/*.tsx");

function lazyPage(name: string) {
  const loader = pageModules[`./pages/${name}.tsx`];
  return loader ? lazy(loader as () => Promise<{ default: React.ComponentType }>) : null;
}

// 核心路由注册表：声明式集中管理，新增页面只需在此追加一行
// - index: true → 首页（path="/"）
// - redirectTo → Navigate 重定向
// - path 含 ":" → 参数路由（如 entities/:name）
interface CoreRoute {
  path?: string;
  page: string;
  index?: boolean;
  redirectTo?: string;
}

const CORE_ROUTES: CoreRoute[] = [
  { index: true, page: "Chat" },
  { path: "dashboard", page: "Dashboard" },
  { path: "status", page: "Chat", redirectTo: "/" },
  { path: "models", page: "Models" },
  { path: "capabilities", page: "Tools", redirectTo: "/tools" },
  { path: "tools", page: "Tools" },
  { path: "entities/:name", page: "EntityDetail" },
  { path: "skills", page: "Skills" },
  { path: "mcp", page: "Mcp" },
  { path: "tags", page: "Tags" },
  { path: "personas", page: "Personas" },
  { path: "memory", page: "Memory" },
  { path: "stickers", page: "Stickers" },
  { path: "share", page: "Share" },
  { path: "data", page: "Data" },
  { path: "config", page: "Config" },
  { path: "channels", page: "Channels" },
  { path: "approvals", page: "Approvals" },
  { path: "tasks", page: "Tasks" },
  { path: "heartbeat", page: "Heartbeat" },
  { path: "thinking", page: "Thinking" },
  { path: "settings", page: "Settings" },
];

export default function App() {
  const setConfig = useAppStore((s) => s.setConfig);
  const startSSE = useChatStore((s) => s.startSSE);

  useEffect(() => {
    configApi.webui().then((r) => {
      const data = r.data;
      setConfig({
        branding: data.branding,
        navigation: data.navigation,
      });
    }).catch((e) => console.warn("[API]", e));
    // 全局启动 chat SSE（幂等）：审批弹窗等事件不依赖 Chat 页
    startSSE();
  }, [setConfig, startSSE]);

  return (
    <AuthGate>
      <BrowserRouter basename="/webui">
        <Routes>
          <Route element={<Layout />}>
            {CORE_ROUTES.map((r) => {
              const Page = lazyPage(r.page);
              if (!Page) return null;
              const element = (
                <Suspense fallback={<div className="p-4 text-muted">加载中…</div>}>
                  <Page />
                </Suspense>
              );
              if (r.redirectTo) {
                return (
                  <Route
                    key={r.path ?? "index"}
                    path={r.path}
                    element={<Navigate to={r.redirectTo} replace />}
                  />
                );
              }
              return r.index ? (
                <Route key="index" index element={element} />
              ) : (
                <Route key={r.path} path={r.path} element={element} />
              );
            })}
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
