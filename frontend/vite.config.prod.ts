import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(() => ({
  plugins: [react()],
  base: "/static/",  // Assets will be served from /static/ path
  build: {
    outDir: "dist",  // Build output goes to frontend/dist directory (will be copied to backend/static)
    emptyOutDir: true
  },
  server: {
    port: 5173,
    proxy: {
      "/sessions": {
        target: "http://localhost:8000",
        changeOrigin: true
      },
      "/ws": {
        target: "ws://localhost:8000",
        ws: true,
        changeOrigin: true
      }
    }
  }
}));