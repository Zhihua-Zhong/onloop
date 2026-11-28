import { expect, test, type Page } from "@playwright/test";

// Decisions' writes (0.7.0): the person's answers, their withdrawal and the format-rule tidy, against onloopd's real
// server over the demo ledger. This file's name sorts after every other spec's on purpose: the specs run in name order
// against one server per project, and these writes add events the others count (Audit folds the heartbeat's pings
// into one row only while nothing else comes between them). Each test still undoes what it wrote.

type Watched = Page & { problems: string[]; writes: string[]; answers?: boolean };
const ANSWERS = ["POST /ui/tasks/resolve", "POST /ui/tasks/withdraw", "POST /ui/tasks/tidy", "POST /ui/tasks/merge"];

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
const TITLE = "Approve the schema change for the import"; // the smart layer's recorded short title

async function openRow(page: Page, title: RegExp | string, phone: boolean) {
  await page.getByTestId("decision-row").filter({ hasText: title }).click();
  const panel = phone ? page.getByTestId("decision-sheet").getByTestId("decision-panel") : page.getByTestId("decision-panel");
  await expect(panel).toBeVisible();
  return panel;
}

test("an answer is sent at once, says when the asker sees it, and can be withdrawn until then", async ({ page }, info) => {
  (page as Watched).answers = true;
  const phone = isPhone(info.project.name);
  await page.goto("/#/decisions");
  const panel = await openRow(page, TITLE, phone);
  await panel.getByTestId("option").filter({ hasText: "Wait for the review" }).click();
  await expect(page.getByTestId("toast").first()).toContainText("Wait for the review. operator sees it at its next prompt.");
  await expect(page.getByTestId("tab-open")).toContainText("6");
  await expect(page.getByTestId("sidebar").getByTestId("decisions-badge")).toHaveText("6");
  // the facts changed: the written texts were for the old ones, so the page shows the facts until new ones are written
  await expect(page.getByTestId("merge-proposal")).toHaveCount(0);
  await page.getByTestId("tab-done").click();
  const answered = await openRow(page, FIRST, phone);
  await expect(answered.getByTestId("answered")).toContainText("You answered Wait for the review");
  await expect(answered.getByTestId("answered")).toContainText("operator sees it at its next prompt");
  await answered.getByTestId("withdraw").click();
  await expect(page.getByTestId("toast").filter({ hasText: "Withdrawn" })).toBeVisible();
  if (phone) await page.keyboard.press("Escape");
  await page.getByTestId("tab-open").click();
  await expect(page.getByTestId("tab-open")).toContainText("7");
  // the same facts as before the answer: the same hash, so the texts written for them are back
  await expect(page.getByTestId("decision-row").first().getByTestId("row-title")).toHaveText(TITLE);
});

test("Rule alerts sets aside the asks that broke the format rule, each with its reason, and the toast undoes it", async ({ page }) => {
  (page as Watched).answers = true;
  await page.goto("/#/details/rules");
  const count = page.getByTestId("rule-asks-missing").getByTestId("count");
  await expect(count).toHaveText("4");
  // three of the four say nothing of what they change; the fourth says it but not what waits on it, and stays
  await expect(page.getByTestId("tidy")).toHaveText("Set aside these 3");
  await page.getByTestId("tidy").click();
  const toast = page.getByTestId("toast").filter({ hasText: "3 asks set aside: format rule, no effect stated." });
  await expect(toast).toBeVisible();
  await expect(count).toHaveText("1");
  await toast.getByRole("button", { name: "Undo" }).click();
  await expect(count).toHaveText("4");
});

// last: a merge closes one decision into another for good (the demo has no undo for it)
test("the person confirms a proposed merge: one decision, with both asks counted", async ({ page }, info) => {
  (page as Watched).answers = true;
  await page.goto("/#/decisions");
  const card = page.getByTestId("merge-proposal");
  await card.getByTestId("merge").click();
  await expect(page.getByTestId("toast").filter({ hasText: "Merged: one decision now" })).toBeVisible();
  await expect(page.getByTestId("tab-open")).toContainText("6");
  await expect(card).toHaveCount(0);
  const row = page.getByTestId("decision-row").filter({ hasText: /chart palette/i });
  await expect(row).toContainText("asked 2×");
  await expect(row).toContainText("operator, builder");
  if (isPhone(info.project.name)) return;
  await page.getByTestId("tab-done").click();
  await expect(page.getByTestId("decision-row").filter({ hasText: "pick the report charts' colours" })).toContainText("Merged");
});
