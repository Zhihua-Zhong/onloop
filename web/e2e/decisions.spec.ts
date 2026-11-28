import { expect, test, type Page } from "@playwright/test";

// Decisions (0.7.0 plan, sections 1 and 4) against onloopd's real server over the demo ledger, read only. Every test
// fails on a console error and on any write. The tests that answer are in write-decisions.spec.ts, which runs last.

type Watched = Page & { problems: string[]; writes: string[]; answers?: boolean };
const ANSWERS = ["POST /ui/tasks/resolve", "POST /ui/tasks/withdraw", "POST /ui/tasks/tidy"];

test.beforeEach(async ({ page }) => {
  const p = page as Watched;
  p.problems = [];
  p.writes = [];
  page.on("console", (m) => m.type() === "error" && !(m.location().url ?? "").includes("/api/stream") && p.problems.push(`${m.text()} ${m.location().url ?? ""}`));
  page.on("pageerror", (e) => p.problems.push(String(e)));
  page.on("request", (r) => r.method() !== "GET" && !r.url().endsWith("/ui/visits") && p.writes.push(`${r.method()} ${new URL(r.url()).pathname}`));
});
test.afterEach(async ({ page }) => {
  const p = page as Watched;
  expect(p.problems).toEqual([]);
  expect(p.writes.filter((w) => !(p.answers && ANSWERS.includes(w)))).toEqual([]);
});

const isPhone = (name: string) => name === "phone";
const FIRST = "approve the schema change, so the import can run on real data";

async function openRow(page: Page, title: RegExp | string, phone: boolean) {
  await page.getByTestId("decision-row").filter({ hasText: title }).click();
  const panel = phone ? page.getByTestId("decision-sheet").getByTestId("decision-panel") : page.getByTestId("decision-panel");
  await expect(panel).toBeVisible();
  return panel;
}

test("Decisions lists the open decisions in their one order, one subject one decision, and agrees with Home and the sidebar", async ({ page }, info) => {
  const phone = isPhone(info.project.name);
  await page.goto("/#/decisions");
  const rows = page.getByTestId("decision-row");
  await expect(rows).toHaveCount(6);
  await expect(page.getByTestId("tab-open")).toContainText("6");
  await expect(page.getByTestId("sidebar").getByTestId("decisions-badge")).toHaveText("6");
  await expect(rows.first().getByTestId("row-title")).toHaveText(FIRST);
  // the operator asked, then the reviewer asked about the same change as PR#204: one decision, asked twice
  await expect(rows.first()).toContainText("asked 2×");
  await expect(rows.first()).toContainText("operator, reviewer");
  await expect(page.getByTestId("rule-asks")).toContainText("4 more asks are missing what they change or what waits on them");
  await expect(page.getByTestId("decisions-sub")).toContainText("7 asks became 6 decisions: one subject, one decision.");
  const panel = await openRow(page, FIRST, phone);
  await expect(panel.getByTestId("panel-title")).toHaveText(FIRST);
  await expect(panel.getByTestId("asked")).toContainText("asked 2× · one decision");
  await expect(panel.getByTestId("ask-log")).toContainText("reviewer asked");
  // the answers are the ones the ask recorded; nothing is generated before the smart layer
  await expect(panel.getByTestId("option")).toHaveText(["Approve", "Approve, import paused", "Wait for the review"]);
  await expect(panel.getByTestId("facts")).toContainText("nothing merges");
  if (phone) await page.keyboard.press("Escape");
  // no options recorded: Done and Decline, never a made-up answer
  const plain = await openRow(page, /register the CI runner/, phone);
  await expect(plain.getByTestId("option")).toHaveCount(0);
  await expect(plain.getByTestId("done")).toBeVisible();
  await expect(plain.getByTestId("decline")).toBeVisible();
  if (phone) await page.keyboard.press("Escape");
  // the answers of the last 14 days, with the one the person gave
  await page.getByTestId("tab-done").click();
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("Approve the copy");
  // Home shows the same count
  await page.goto("/#/");
  await expect(page.getByTestId("decisions-count")).toHaveText("6");
  await expect(page.getByTestId("decide-first")).toHaveText("Decide the first of 6");
});
