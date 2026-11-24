import path from "node:path";
import { test } from "@playwright/test";

/** The directory of the demo server this test's project runs against (web/.demo/<pid>, where its ledger is), read
 * from that server's own /health. Each project has its own server and ledger (playwright.config.ts), so a test never
 * reaches another project's. */
export async function demoDir(): Promise<string> {
  const base = test.info().project.use.baseURL;
  if (!base) throw new Error("the project has no baseURL");
  const health = (await (await fetch(new URL("/health", base))).json()) as { ledger: { path: string } };
  return path.dirname(health.ledger.path);
}
