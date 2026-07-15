import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
      "@eaos/shared": path.resolve(__dirname, "../shared/src"),
    },
  },
  build: {
    target: "chrome105",
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      // Proxy all backend API calls. The FastAPI app has no /api prefix,
      // so we strip it here and forward the bare path to localhost:8000.
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
      // SSE stream endpoints also proxy to backend.
      "/invoke": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
      "/interrupt": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
      // Uploaded files served by FastAPI's StaticFiles mount.
      "/uploads": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
