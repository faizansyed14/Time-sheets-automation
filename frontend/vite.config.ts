import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In Docker dev the backend is another compose service, so the proxy target is
// set via VITE_PROXY_TARGET=http://backend:8000. Locally it defaults to
// http://localhost:8000.
const target = process.env.VITE_PROXY_TARGET || "http://localhost:8000";

// When the dev stack is fronted by nginx (single entry on one port), the HMR
// websocket must connect back through that port. Set VITE_HMR_CLIENT_PORT to the
// nginx port (e.g. 8080); unset, HMR uses the direct Vite port as before.
const hmrClientPort = process.env.VITE_HMR_CLIENT_PORT
  ? Number(process.env.VITE_HMR_CLIENT_PORT)
  : undefined;

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true,
    hmr: hmrClientPort ? { clientPort: hmrClientPort } : undefined,
    proxy: {
      "/api": { target, changeOrigin: true },
      "/auth": { target, changeOrigin: true },
      "/health": { target, changeOrigin: true },
    },
  },
});
