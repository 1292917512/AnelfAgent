import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { QueryError } from "./components/common/AsyncState";
import { getInitialTheme } from "./stores/app-store";
import { useAuthStore } from "./stores/auth-store";
import { toast } from "./stores/toast-store";
import { apiErrorMessage, setApiErrorHandler, setUnauthorizedHandler } from "./lib/api";
import { createQueryClient } from "./lib/query-client";
import { initChannelPlugins } from "./lib/channel-plugins";
import { initUiContributions } from "./lib/ui-contribution-registry";
import { recoverChunk } from "./lib/chunk-recovery";
import i18n from "./i18n";
import "./styles/globals.css";

const queryClient = createQueryClient();
setApiErrorHandler((error) => toast.error(apiErrorMessage(error, i18n.t("requestFailed"))));
setUnauthorizedHandler(() => {
  queryClient.clear();
  useAuthStore.getState().expireSession();
});
window.addEventListener("vite:preloadError", (event) => {
  if (recoverChunk()) event.preventDefault();
});
const theme = getInitialTheme();
document.documentElement.dataset.theme = theme;
document.documentElement.classList.toggle("dark", theme === "dark");

async function bootstrap() {
  const container = document.getElementById("root");
  if (!container) throw new Error("Application root is missing");
  const root = createRoot(container);
  try {
    await initChannelPlugins();
    await initUiContributions();
    root.render(<StrictMode><QueryClientProvider client={queryClient}><App /></QueryClientProvider></StrictMode>);
  } catch (error) {
    root.render(<div className="mx-auto max-w-xl p-8"><QueryError error={error} retry={() => location.reload()} /></div>);
  }
}
void bootstrap();
