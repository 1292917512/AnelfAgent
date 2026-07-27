import { defineConfig } from "vite";
import react from "@vitejs/plugin-react-swc";
import tailwindcss from "@tailwindcss/vite";
import path from "path";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: "/webui/",
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
    // 实体面板通过软链接引入（entities/*/panel.tsx → src/pages/entities/panels/），
    // preserveSymlinks 让 Vite 按链接路径（src/ 内）解析依赖，而非真实路径（entities/）
    preserveSymlinks: true,
  },
  server: {
    port: 3000,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8091",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks: {
          // React 全家桶单独成 chunk：内容稳定、缓存周期长
          "react-vendor": ["react", "react-dom", "react-router-dom"],
        },
      },
    },
  },
});
