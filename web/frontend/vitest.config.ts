import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react-swc";
import path from "node:path";
import { syncEntityPanels, syncModuleLinks } from "./scripts/module-links.mjs";

syncEntityPanels();
syncModuleLinks();

export default defineConfig({
  root: path.resolve(__dirname, "../.."),
  cacheDir: path.resolve(__dirname, "node_modules/.vite"),
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(__dirname, "src"), "@entities": path.resolve(__dirname, "../../entities"), "@channels": path.resolve(__dirname, "../../channels") } },
  test: {
    environment: "jsdom",
    include: ["web/frontend/src/**/*.test.{ts,tsx}", "entities/*/panels/**/*.test.{ts,tsx}", "channels/*/frontend/**/*.test.{ts,tsx}"],
    setupFiles: [path.resolve(__dirname, "src/test/setup.ts")],
    restoreMocks: true,
  },
});
