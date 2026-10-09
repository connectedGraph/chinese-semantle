import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// 开发时 /api 代理到 FastAPI(:8001)，生产构建后同源部署（由反向代理或后端提供）
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5175,
    host: "127.0.0.1",
    proxy: {
      "/api": { target: "http://127.0.0.1:8001", changeOrigin: true },
    },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
