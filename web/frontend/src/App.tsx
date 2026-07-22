import { useEffect } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { Layout } from "./components/layout/Layout";
import { AuthGate } from "./components/AuthGate";
import { Toaster } from "./components/ui/Toast";
import { ApprovalDialog } from "./components/ApprovalDialog";
import { useAppStore } from "./stores/app-store";
import { useChatStore } from "./stores/chat-store";
import { configApi } from "./lib/api";
import Dashboard from "./pages/Dashboard";
import Chat from "./pages/Chat";
import Models from "./pages/Models";
import Personas from "./pages/Personas";
import Memory from "./pages/Memory";
import Capabilities from "./pages/Capabilities";
import Config from "./pages/Config";
import Channels from "./pages/Channels";
import Settings from "./pages/Settings";
import Heartbeat from "./pages/Heartbeat";
import Thinking from "./pages/Thinking";
import Tags from "./pages/Tags";
import Tasks from "./pages/Tasks";

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
            <Route index element={<Chat />} />
            <Route path="dashboard" element={<Dashboard />} />
            <Route path="status" element={<Navigate to="/" replace />} />
            <Route path="models" element={<Models />} />
            <Route path="capabilities" element={<Capabilities />} />
            <Route path="tools" element={<Navigate to="/capabilities?tab=tools" replace />} />
            <Route path="skills" element={<Navigate to="/capabilities?tab=skills" replace />} />
            <Route path="mcp" element={<Navigate to="/capabilities?tab=mcp" replace />} />
            <Route path="tags" element={<Tags />} />
            <Route path="personas" element={<Personas />} />
            <Route path="memory" element={<Memory />} />
            <Route path="config" element={<Config />} />
            <Route path="channels" element={<Channels />} />
            <Route path="tasks" element={<Tasks />} />
            <Route path="heartbeat" element={<Heartbeat />} />
            <Route path="thinking" element={<Thinking />} />
            <Route path="settings" element={<Settings />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
      <Toaster />
      <ApprovalDialog />
    </AuthGate>
  );
}
