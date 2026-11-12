import { defineConfig } from "@playwright/test";

// The browser tests run against onloopd's real HTTP server (its Host check, its CSP, its read-only routes) over a
// fresh demo ledger of generic sample data (e2e/demo_server.py), serving the built UI. Port 8792, loopback only.
// They use the Chrome installed on the machine (channel "chrome"), so no browser is downloaded. ONLOOP_PYTHON names
// the interpreter that has onloop installed.
const PORT = 8792;
const python = process.env.ONLOOP_PYTHON ?? "python";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: { baseURL: `http://localhost:${PORT}`, channel: "chrome", trace: "retain-on-failure" },
  webServer: {
    command: `"${python}" e2e/demo_server.py ${PORT}`,
    url: `http://localhost:${PORT}/health`,
    reuseExistingServer: true,
    timeout: 30_000,
    stdout: "ignore",
  },
  projects: [
    { name: "desktop", use: { viewport: { width: 1440, height: 900 } } },
    { name: "phone", use: { viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: false } },
  ],
});
