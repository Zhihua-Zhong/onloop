import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

// The vision loop's pictures (decision 0009): every view as a person first meets it, in both themes, at each
// project's width (1440 and 390), against the demo ledger, plus a few states a person reaches by one click.
// SCREENS_DIR says where they go (the evidence is kept outside the repository); without it they land in
// test-results/screens, which is not committed.
const OUT = process.env.SCREENS_DIR ?? path.resolve(process.cwd(), "test-results/screens");

const VIEWS: { id: string; ready: (p: Page) => Promise<void> }[] = [
  { id: "now", ready: (p) => expect(p.getByTestId("constraint")).toBeVisible() },
  { id: "flow", ready: async (p) => { await expect(p.getByTestId("canvas")).toHaveAttribute("data-opening", /.+/); await p.waitForTimeout(300); } },
  { id: "team", ready: (p) => expect(p.getByTestId("lane-reviewer")).toBeVisible() },
  { id: "schedules", ready: (p) => expect(p.getByTestId("schedule-nightly-verify")).toBeVisible() },
  { id: "audit", ready: (p) => expect(p.getByTestId("event").first()).toBeVisible() },
];

for (const theme of ["light", "dark"] as const) {
  for (const v of VIEWS) {
    test(`screen: ${v.id} ${theme}`, async ({ page }, info) => {
      fs.mkdirSync(OUT, { recursive: true });
      const w = info.project.name === "phone" ? 390 : 1440;
      await page.goto(`/?theme=${theme}#/${v.id}`);
      await v.ready(page);
      await page.mouse.move(1, 1);
      await page.screenshot({ path: path.join(OUT, `${v.id}-${w}-${theme}.png`), fullPage: v.id !== "flow" });
    });
  }
}

test("screen: states one click away", async ({ page }, info) => {
  test.setTimeout(90_000);
  fs.mkdirSync(OUT, { recursive: true });
  const w = info.project.name === "phone" ? 390 : 1440;
  await page.goto("/?theme=light#/flow");
  await expect(page.getByTestId("canvas")).toHaveAttribute("data-opening", /.+/);
  // A card a person can see: one waiting on them if the opening shows one, else the first card on screen.
  const vp = page.viewportSize()!;
  let clicked = false;
  for (const sel of ['[data-look="human"]', "[data-look]"]) {
    for (const el of await page.locator(sel).all()) {
      const b = await el.boundingBox();
      if (!clicked && b && b.x >= 0 && b.y >= 100 && b.x + b.width <= vp.width && b.y + b.height <= vp.height) {
        await el.click();
        clicked = true;
      }
    }
  }
  await expect(page.getByTestId("side-panel")).toBeVisible();
  await page.screenshot({ path: path.join(OUT, `flow-card-${w}-light.png`) });
  // The queue selected: its five lines traced, nothing else.
  await page.goto("/?theme=light#/flow");
  await page.reload();
  await expect(page.getByTestId("canvas")).toHaveAttribute("data-opening", /.+/);
  if (w === 390) {
    await page.getByTestId("show-all").click(); // on a phone the queue is off the opening view
    await page.waitForTimeout(400);
  }
  await page.getByTestId("res-merge-check").click();
  await page.mouse.move(1, 1);
  await page.waitForTimeout(300);
  await page.screenshot({ path: path.join(OUT, `flow-traced-${w}-light.png`) });
  await page.locator(".react-flow__pane").click({ position: { x: 5, y: 5 } });
  await page.goto("/?theme=light#/flow");
  await page.getByTestId("show-all").click();
  await page.waitForTimeout(400);
  await page.screenshot({ path: path.join(OUT, `flow-showall-${w}-light.png`) });
  for (const theme of ["light", "dark"]) {
    for (const mode of ["you", "roadmap", "calendar"]) {
      await page.goto(`/?theme=${theme}#/flow?mode=${mode}`);
      await page.reload(); // the theme is read at load
      await expect(page.getByTestId("view-flow")).toHaveAttribute("data-mode", mode);
      await page.waitForTimeout(500);
      await page.mouse.move(1, 1);
      await page.screenshot({ path: path.join(OUT, `flow-${mode}-${w}-${theme}.png`) });
    }
  }
  // The roadmap's lower lanes, scrolled into view (the chart scrolls inside the page, so a full-page shot misses them).
  await page.goto("/?theme=light#/flow?mode=roadmap");
  await page.reload();
  await expect(page.getByTestId("roadmap")).toBeVisible();
  await page.getByTestId("roadmap").locator(".overflow-y-auto").evaluate((el) => el.scrollTo(0, el.scrollHeight));
  await page.waitForTimeout(200);
  await page.screenshot({ path: path.join(OUT, `flow-roadmap-bottom-${w}-light.png`) });
  await page.goto("/?theme=light#/schedules?open=nightly-verify");
  await expect(page.getByTestId("log-tail")).toBeVisible();
  await page.mouse.move(1, 1);
  await page.screenshot({ path: path.join(OUT, `schedules-open-${w}-light.png`), fullPage: true });
});
