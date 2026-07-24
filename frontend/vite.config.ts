import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: proxy the API to the FastAPI server so cookies stay same-site.
// Build: emit into the backend's static dir, which FastAPI serves in production.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "../backend/static",
    emptyOutDir: true,
  },
});
