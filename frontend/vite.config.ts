import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In Docker dev the backend is another compose service, so the proxy target is
// set via VITE_PROXY_TARGET=http://backend:8000. Locally it defaults to
// http://localhost:8000.
const target = process.env.VITE_PROXY_TARGET || "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true,
    proxy: {
      "/api": { target, changeOrigin: true },
      "/auth": { target, changeOrigin: true },
      "/health": { target, changeOrigin: true },
    },
  },
});
