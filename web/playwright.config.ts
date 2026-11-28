import { defineConfig } from "@playwright/test";

// The browser tests run against onloopd's real HTTP server (its Host check, its CSP, its read-only routes) over a
// fresh demo ledger of generic sample data (e2e/demo_server.py), serving the built UI, loopback only. Each project has
// its own server and its own fresh ledger (desktop on 8792, phone on 8793), so what one project writes (a message
// sent, a visit) never shows in the other's run; a server is never reused, so every run starts from the demo's story.
// They use the Chrome installed on the machine (channel "chrome"), so no browser is downloaded; CI sets PW_CHANNEL
// empty to use the Chromium it installs. ONLOOP_PYTHON names the interpreter that has onloop installed.
// E2E_PORT_DESKTOP and E2E_PORT_PHONE move them, and E2E_ONLY=desktop|phone starts one project's server alone, for a
// machine where those ports are taken.
const PORTS = { desktop: Number(process.env.E2E_PORT_DESKTOP ?? 8792), phone: Number(process.env.E2E_PORT_PHONE ?? 8793) };
const ONLY = process.env.E2E_ONLY as keyof typeof PORTS | undefined;
const python = process.env.ONLOOP_PYTHON ?? "python";
const channel = process.env.PW_CHANNEL ?? "chrome";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : [["list"]],
  use: { channel: channel || undefined, trace: "retain-on-failure" },
  webServer: Object.entries(PORTS).filter(([name]) => !ONLY || name === ONLY).map(([, port]) => ({
    command: `"${python}" e2e/demo_server.py ${port}`,
    url: `http://localhost:${port}/health`,
    reuseExistingServer: false,
    timeout: 30_000,
    stdout: "ignore" as const,
  })),
  projects: [
    { name: "desktop", use: { baseURL: `http://localhost:${PORTS.desktop}`, viewport: { width: 1440, height: 900 } } },
    {
      name: "phone",
      use: { baseURL: `http://localhost:${PORTS.phone}`, viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: false },
    },
  ].filter((p) => !ONLY || p.name === ONLY),
});
