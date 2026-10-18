import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL(".", import.meta.url));
export default defineConfig({
  root,
  plugins: [react()],
  build: { outDir: "dist", emptyOutDir: true },
  server: { port: 5173, host: true, proxy: { "/api": "http://localhost:4310", "/renders": "http://localhost:4310" } },
});
