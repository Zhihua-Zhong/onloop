import { expect, test, type Page } from "@playwright/test";

// Decisions (0.7.0 plan, sections 1 and 4) against onloopd's real server over the demo ledger, read only. Every test
// fails on a console error and on any write. The tests that answer are in write-decisions.spec.ts, which runs last.

type Watched = Page & { problems: string[]; writes: string[]; answers?: boolean; textsDown?: boolean };
const ANSWERS = ["POST /ui/tasks/resolve", "POST /ui/tasks/withdraw", "POST /ui/tasks/tidy"];

test.beforeEach(async ({ page }) => {
  const p = page as Watched;
  p.problems = [];
  p.writes = [];
  // a test that makes GET /api/texts fail expects the browser to log that one failed request
  page.on("console", (m) => m.type() === "error" && !(m.location().url ?? "").includes("/api/stream") && !(p.textsDown && (m.location().url ?? "").includes("/api/texts")) && p.problems.push(`${m.text()} ${m.location().url ?? ""}`));
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
// the demo serves the smart layer's recorded texts (tests/fixtures/brief): the short title onloop wrote for it
const TITLE = "Approve the schema change for the import";

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
  await expect(rows).toHaveCount(7);
  await expect(page.getByTestId("tab-open")).toContainText("7");
  await expect(page.getByTestId("sidebar").getByTestId("decisions-badge")).toHaveText("7");
  await expect(rows.first().getByTestId("row-title")).toHaveText(TITLE);
  // the operator asked, then the reviewer asked about the same change as PR#204: one decision, asked twice
  await expect(rows.first()).toContainText("asked 2×");
  await expect(rows.first()).toContainText("operator, reviewer");
  await expect(page.getByTestId("rule-asks")).toContainText("4 more asks are missing what they change or what waits on them");
  await expect(page.getByTestId("decisions-sub")).toContainText("9 asks became 7 decisions: one subject, one decision.");
  const panel = await openRow(page, TITLE, phone);
  await expect(panel.getByTestId("panel-title")).toHaveText(TITLE);
  await expect(panel.getByTestId("panel-subject")).toHaveText("Schema change");
  await expect(panel.getByTestId("facts")).toContainText(FIRST); // the ask's own words stay, under the written title
  await expect(panel.getByTestId("asked")).toContainText("asked 2× · one decision");
  await expect(panel.getByTestId("ask-log")).toContainText("reviewer asked");
  // the answers are the ones the ask recorded; the suggested one, with its grounds, is the one primary button
  await expect(panel.getByTestId("suggested-answer")).toHaveText("Approve");
  await expect(panel.getByTestId("suggested")).toContainText("holds the import walk");
  await expect(panel.getByTestId("suggested").getByTestId("written-by")).toContainText(/Written by onloop from this decision's facts · facts [0-9a-f]{7}/);
  await expect(panel.getByTestId("option")).toHaveText(["Approve", "Approve, import paused", "Wait for the review"]);
  await expect(panel.locator('[data-testid="option"][data-suggested="true"]')).toHaveText("Approve");
  await expect(panel.getByTestId("facts")).toContainText("nothing merges");
  if (phone) await page.keyboard.press("Escape");
  // no options recorded: Done and Decline, never a made-up answer
  const plain = await openRow(page, /register the CI runner/i, phone);
  await expect(plain.getByTestId("option")).toHaveCount(0);
  await expect(plain.getByTestId("suggested")).toHaveCount(0); // no options: nothing to suggest
  await expect(plain.getByTestId("done")).toBeVisible();
  await expect(plain.getByTestId("decline")).toBeVisible();
  if (phone) await page.keyboard.press("Escape");
  // the answers of the last 14 days, with the one the person gave
  await page.getByTestId("tab-done").click();
  await expect(rows).toHaveCount(4);
  await expect(rows.first()).toContainText("Approve the copy");
  // Home shows the same count
  await page.goto("/#/");
  await expect(page.getByTestId("decisions-count")).toHaveText("7");
  await expect(page.getByTestId("decide-first")).toHaveText(`Decide: ${TITLE}`);
});

test("the smart layer proposes merging two near-duplicate decisions, and nothing merges unless the person does", async ({ page }) => {
  await page.goto("/#/decisions");
  const card = page.getByTestId("merge-proposal");
  await expect(card).toHaveCount(1);
  await expect(card).toContainText("These two look like one decision");
  await expect(card).toContainText("Choose the chart palette");
  await expect(card).toContainText("Pick the report chart colours");
  await expect(card).toContainText("Nothing merges unless you do.");
  await expect(page.getByTestId("decision-row")).toHaveCount(7); // proposed, not merged
  // Keep apart is this browser's: no write, and the card stays gone on reload
  await card.getByTestId("keep-apart").click();
  await expect(card).toHaveCount(0);
  await page.reload();
  await expect(page.getByTestId("decision-row")).toHaveCount(7);
  await expect(page.getByTestId("merge-proposal")).toHaveCount(0);
});

test("when the smart layer has nothing for today's facts, every page shows the facts alone", async ({ page }, info) => {
  (page as Watched).textsDown = true;
  await page.route("**/api/texts", (r) => r.fulfill({ status: 503, contentType: "application/json", body: '{"error":"down"}' }));
  await page.goto("/#/");
  await expect(page.getByTestId("brief-facts")).toContainText("11 asks wait on you, 7 of them saying what they change.");
  await expect(page.getByTestId("brief-mode")).toHaveText("facts only");
  await expect(page.getByTestId("written-by")).toHaveCount(0);
  await expect(page.getByTestId("decide-first")).toHaveText("Decide the first of 7");
  await page.goto("/#/decisions");
  await expect(page.getByTestId("decision-row").first().getByTestId("row-title")).toHaveText(FIRST);
  await expect(page.getByTestId("merge-proposal")).toHaveCount(0);
  const panel = await openRow(page, FIRST, isPhone(info.project.name));
  await expect(panel.getByTestId("suggested")).toHaveCount(0);
  await expect(panel.locator('[data-suggested="true"]')).toHaveCount(0); // every option outline, none picked for you
  await expect(panel.getByTestId("option")).toHaveText(["Approve", "Approve, import paused", "Wait for the review"]);
});
