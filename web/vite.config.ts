import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// onloopd serves the build from its own origin (decision 0009), so asset URLs start at /. The build goes into the
// Python package (src/onloop/ui/, gitignored) and the wheel carries it. The dev server binds loopback and forwards
// /api to an onloopd on its default port.
export default defineConfig({
  base: "/",
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(__dirname, "src") } },
  server: {
    host: "127.0.0.1", // scan:allow
    port: 5173,
    strictPort: true,
    proxy: { "/api": "http://localhost:8790" },
  },
  build: {
    outDir: path.resolve(__dirname, "../src/onloop/ui"),
    emptyOutDir: true,
    sourcemap: false,
    // elkjs, the layout engine, is large; it is split into its own chunk and loaded by the Flow view only.
    chunkSizeWarningLimit: 1600,
  },
});
