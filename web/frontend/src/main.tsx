import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { getInitialTheme } from "./stores/app-store";
import { toast } from "./stores/toast-store";
import { apiErrorMessage, setApiErrorHandler } from "./lib/api";
import i18n from "./i18n";
import "./styles/globals.css";

// 全局 API 错误反馈：任何请求失败都弹出 toast，避免静默失败（如保存 409 无提示）
setApiErrorHandler((err) => {
  toast.error(apiErrorMessage(err, i18n.t("requestFailed")));
});

// 前端重新构建后，已打开的旧标签页引用的懒加载 chunk 已不存在（哈希变更），
// 监听预加载失败自动刷新一次完成自愈，避免单页空白（如记忆页打不开）
window.addEventListener("vite:preloadError", (event) => {
  event.preventDefault();
  window.location.reload();
});

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
      staleTime: 10_000,
    },
  },
});

// 首帧前应用主题（index.html 内联脚本已先行设置，这里兜底并同步 dark class）
const initialTheme = getInitialTheme();
document.documentElement.setAttribute("data-theme", initialTheme);
document.documentElement.classList.toggle("dark", initialTheme === "dark");

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);
