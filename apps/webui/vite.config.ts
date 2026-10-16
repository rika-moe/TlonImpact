import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

/** Tlon server (REST + SSE, protocol v2). */
const SERVER = "http://127.0.0.1:8080";

/** API path prefixes proxied to the server in dev.
 *  Drift-guarded by scripts/check_dev_proxy.py (check_all.sh --static):
 *  every REST route prefix is either listed here or explicitly exempted
 *  there with a reason. */
const API_PATHS = [
  "/messages",
  "/inbox",
  "/members",
  "/sessions",
  "/events",
  "/agents",
  "/participants",
  "/config",
  "/admin",
  "/traces",
  "/metrics",
  "/logs",
  "/health",
];

export default defineConfig({
  plugins: [react()],
  // Production: served by `tlon dream` under /ui/ (see docs/design/webui.md).
  base: "/ui/",
  build: {
    outDir: "dist",
  },
  server: {
    proxy: Object.fromEntries(
      API_PATHS.map((path) => [path, { target: SERVER, changeOrigin: true }]),
    ),
  },
});
