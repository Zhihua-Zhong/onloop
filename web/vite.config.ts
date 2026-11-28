import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import fs from "node:fs";
import path from "node:path";

// The font's licence travels with the font (SIL OFL 1.1 asks for it): the build writes it next to the bundle, so the
// wheel that carries Inter's files carries its licence too.
function fontLicence(): Plugin {
  return {
    name: "onloop-font-licence",
    apply: "build",
    generateBundle() {
      const text = fs.readFileSync(path.resolve(__dirname, "node_modules/@fontsource-variable/inter/LICENSE"), "utf-8");
      this.emitFile({ type: "asset", fileName: "licenses/inter-OFL-1.1.txt", source: text });
    },
  };
}

// onloopd serves the build from its own origin (decision 0009), so asset URLs start at /. The build goes into the
// Python package (src/onloop/ui/, gitignored) and the wheel carries it. The dev server binds loopback and forwards
// /api to an onloopd on its default port.
export default defineConfig({
  base: "/",
  plugins: [react(), fontLicence()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
      // Radix's scroll lock would write a <style> element, which the CSP refuses: src/lib/noStyleTags.ts says why none is needed
      "react-style-singleton": path.resolve(__dirname, "src/lib/noStyleTags.ts"),
    },
  },
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
    chunkSizeWarningLimit: 1600,
  },
});
