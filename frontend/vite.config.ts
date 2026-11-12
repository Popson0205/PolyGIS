/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import pkg from "./package.json";

const version = pkg.version;

// In development the API runs on :8012 (uvicorn, API_PREFIX=/api) and is reached through /api, as in production.
export default defineConfig({
  plugins: [react(), { name: "app-version", transformIndexHtml: (html: string) => html.replace(/%APP_VERSION%/g, version) }],
  server: { proxy: { "/api": "http://localhost:8012" } },   // run the API with API_PREFIX=/api
  test: { environment: "jsdom", globals: false },
});
