/// <reference types="vitest/config" />
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// The build lands in the Python package's static/ dir so FastAPI serves it
// directly (see api.py). In dev, these paths proxy to the running API —
// "/conversations" as a prefix also covers "/conversations/{id}".
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../src/nba_projection_bot/static",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/ask": "http://127.0.0.1:8000",
      "/health": "http://127.0.0.1:8000",
      "/conversations": "http://127.0.0.1:8000",
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test/setup.ts",
    css: true,
  },
});
