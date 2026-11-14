import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server proxies the API so the app can be developed with hot reload
// against a real core process; the production build is plain static files that
// core serves and Tauri bundles.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist", emptyOutDir: true },
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
});
