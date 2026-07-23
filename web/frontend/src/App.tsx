import { lazy, useEffect } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { Layout } from "./components/layout/Layout";
import { AuthGate } from "./components/AuthGate";
import { Toaster } from "./components/ui/Toast";
import { ApprovalDialog } from "./components/ApprovalDialog";
import { CommandPalette } from "./components/palette/CommandPalette";
import { useAppStore } from "./stores/app-store";
import { useChatStore } from "./stores/chat-store";
import { configApi } from "./lib/api";

// 页面级懒加载：重依赖（xyflow / CodeMirror 等）随路由按需拆分，避免全部打进主 chunk
const Chat = lazy(() => import("./pages/Chat"));
const Dashboard = lazy(() => import("./pages/Dashboard"));
const Models = lazy(() => import("./pages/Models"));
const Personas = lazy(() => import("./pages/Personas"));
const Memory = lazy(() => import("./pages/Memory"));
const Tools = lazy(() => import("./pages/Tools"));
const Skills = lazy(() => import("./pages/Skills"));
const Mcp = lazy(() => import("./pages/Mcp"));
const Config = lazy(() => import("./pages/Config"));
const Channels = lazy(() => import("./pages/Channels"));
const Approvals = lazy(() => import("./pages/Approvals"));
const Settings = lazy(() => import("./pages/Settings"));
const Heartbeat = lazy(() => import("./pages/Heartbeat"));
const Thinking = lazy(() => import("./pages/Thinking"));
const Tags = lazy(() => import("./pages/Tags"));
const Tasks = lazy(() => import("./pages/Tasks"));
const Stickers = lazy(() => import("./pages/Stickers"));
const Data = lazy(() => import("./pages/Data"));

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
        <Routes>          <Route element={<Layout />}>
            <Route index element={<Chat />} />
            <Route path="dashboard" element={<Dashboard />} />
            <Route path="status" element={<Navigate to="/" replace />} />
            <Route path="models" element={<Models />} />
            <Route path="capabilities" element={<Navigate to="/tools" replace />} />
            <Route path="tools" element={<Tools />} />
            <Route path="skills" element={<Skills />} />
            <Route path="mcp" element={<Mcp />} />
            <Route path="tags" element={<Tags />} />
            <Route path="personas" element={<Personas />} />
            <Route path="memory" element={<Memory />} />
            <Route path="stickers" element={<Stickers />} />
            <Route path="data" element={<Data />} />
            <Route path="config" element={<Config />} />
            <Route path="channels" element={<Channels />} />
            <Route path="approvals" element={<Approvals />} />
            <Route path="tasks" element={<Tasks />} />
            <Route path="heartbeat" element={<Heartbeat />} />
            <Route path="thinking" element={<Thinking />} />
            <Route path="settings" element={<Settings />} />
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
