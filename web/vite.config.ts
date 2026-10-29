import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// VITE_BASE lets the same build live at / (served by FastAPI) or at /bicopilot/ (GitHub Pages).
export default defineConfig({
  plugins: [react()],
  base: process.env.VITE_BASE || "/",
  server: { proxy: { "/chat": "http://127.0.0.1:8080", "/catalog": "http://127.0.0.1:8080", "/runs": "http://127.0.0.1:8080", "/healthz": "http://127.0.0.1:8080" } },
});
