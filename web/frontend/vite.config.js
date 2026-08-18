import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// 开发时 /api 转到 FastAPI；构建产物输出到 dist，由后端托管
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8787",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    assetsDir: "assets",
  },
});
