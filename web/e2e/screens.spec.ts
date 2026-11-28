import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { demoDir } from "./demo";

// The vision loop's pictures (decision 0009): every view as a person first meets it, in both themes, at each
// project's width (1440 and 390), against the demo ledger, plus a few states a person reaches by one click.
// SCREENS_DIR says where they go (the evidence is kept outside the repository); without it they land in
// test-results/screens, which is not committed.
const OUT = process.env.SCREENS_DIR ?? path.resolve(process.cwd(), "test-results/screens");

const VIEWS: { id: string; hash?: string; ready: (p: Page) => Promise<void> }[] = [
  { id: "home", hash: "", ready: (p) => expect(p.getByTestId("brief")).toBeVisible() },
  { id: "rules", hash: "details/rules", ready: (p) => expect(p.getByTestId("rule-asks-missing")).toBeVisible() },
  { id: "replay", ready: (p) => expect(p.getByTestId("lane").first()).toBeVisible() },
  { id: "map", ready: (p) => expect(p.getByTestId("map-level").first()).toBeVisible() },
  { id: "team", ready: (p) => expect(p.getByTestId("lane-reviewer")).toBeVisible() },
  { id: "conversation", ready: (p) => expect(p.getByTestId("entry-run").first()).toBeVisible() },
  { id: "schedules", ready: (p) => expect(p.getByTestId("schedule-nightly-verify")).toBeVisible() },
  { id: "audit", ready: (p) => expect(p.getByTestId("event").first()).toBeVisible() },
];

for (const theme of ["light", "dark"] as const) {
  for (const v of VIEWS) {
    test(`screen: ${v.id} ${theme}`, async ({ page }, info) => {
      fs.mkdirSync(OUT, { recursive: true });
      const w = info.project.name === "phone" ? 390 : 1440;
      await page.goto(`/?theme=${theme}#/${v.hash ?? v.id}`);
      await v.ready(page);
      await page.mouse.move(1, 1);
      await page.screenshot({ path: path.join(OUT, `${v.id}-${w}-${theme}.png`), fullPage: v.id !== "map" });
    });
  }
}

test("screen: states one click away", async ({ page }, info) => {
  test.setTimeout(90_000);
  fs.mkdirSync(OUT, { recursive: true });
  const w = info.project.name === "phone" ? 390 : 1440;
  await page.goto("/?theme=light#/schedules?open=nightly-verify");
  await expect(page.getByTestId("log-tail")).toBeVisible();
  await page.mouse.move(1, 1);
  await page.screenshot({ path: path.join(OUT, `schedules-open-${w}-light.png`), fullPage: true });
});

test("screen: the other conversations", async ({ page }, info) => {
  fs.mkdirSync(OUT, { recursive: true });
  const w = info.project.name === "phone" ? 390 : 1440;
  for (const theme of ["light", "dark"] as const) {
    for (const role of ["designer", "reviewer"]) {
      await page.goto(`/?theme=${theme}#/conversation?role=${role}`);
      await expect(page.getByTestId("entry-message").first()).toBeVisible();
      await page.mouse.move(1, 1);
      await page.screenshot({ path: path.join(OUT, `conversation-${role}-${w}-${theme}.png`), fullPage: true });
    }
  }
});

// The states a person reaches by acting: a message just sent (queued), and the carrier stopped by its kill switch
// (the `carrier.off` file next to the demo ledger, as `onloop carrier stop` writes it). Last, so the other pictures
// are taken before them.
test("screen: a message sent, and the carrier stopped", async ({ page }, info) => {
  fs.mkdirSync(OUT, { recursive: true });
  const w = info.project.name === "phone" ? 390 : 1440;
  await page.goto(`/?theme=light#/conversation?role=builder`);
  await page.getByTestId("compose-text").fill("Please rebase on main before the review.");
  await page.getByTestId("compose-send").click();
  await expect(page.getByTestId("entry-message").filter({ hasText: "rebase on main" }).getByTestId("delivery")).toContainText("queued");
  await page.mouse.move(1, 1);
  await page.screenshot({ path: path.join(OUT, `conversation-sent-${w}-light.png`), fullPage: true });
  const kill = path.join(await demoDir(), "carrier.off");
  fs.writeFileSync(kill, "stopped by the screenshot test\n");
  try {
    for (const theme of ["light", "dark"] as const) {
      await page.goto(`/?theme=${theme}#/conversation?role=builder`);
      await expect(page.getByTestId("carrier-state")).toContainText("kill switch");
      await page.mouse.move(1, 1);
      await page.screenshot({ path: path.join(OUT, `conversation-stopped-${w}-${theme}.png`), fullPage: true });
    }
  } finally {
    fs.rmSync(kill, { force: true }); // the file this test wrote
  }
});

// The compose box when a message cannot go anywhere: no carrier configured (a default install; the demo server reads
// `demo-no-carrier` next to its ledger), and a role opted out by the CLI. Both are put back after.
test("screen: the compose box closed", async ({ page }, info) => {
  const { execFileSync } = await import("node:child_process");
  fs.mkdirSync(OUT, { recursive: true });
  const w = info.project.name === "phone" ? 390 : 1440;
  const dir = await demoDir();
  const off = path.join(dir, "demo-no-carrier");
  fs.writeFileSync(off, "written by the screenshot test\n");
  try {
    for (const theme of ["light", "dark"] as const) {
      await page.goto(`/?theme=${theme}#/conversation?role=designer`);
      await expect(page.getByTestId("compose-off")).toBeVisible();
      await page.mouse.move(1, 1);
      await page.screenshot({ path: path.join(OUT, `conversation-nocarrier-${w}-${theme}.png`), fullPage: true });
    }
  } finally {
    fs.rmSync(off, { force: true });
  }
  const python = process.env.ONLOOP_PYTHON ?? "python";
  const ledger = path.join(dir, "ledger.db");
  execFileSync(python, ["-m", "onloop", "role", "opt-out", "designer", "message", "--ledger", ledger], { stdio: "pipe" });
  try {
    for (const theme of ["light", "dark"] as const) {
      await page.goto(`/?theme=${theme}#/conversation?role=designer`);
      await expect(page.getByTestId("compose-off")).toContainText("opt-in");
      await page.mouse.move(1, 1);
      await page.screenshot({ path: path.join(OUT, `conversation-optedout-${w}-${theme}.png`), fullPage: true });
    }
  } finally {
    execFileSync(python, ["-m", "onloop", "role", "opt-in", "designer", "message", "--ledger", ledger], { stdio: "pipe" });
  }
});
