import { expect, test, type Page } from "@playwright/test";

// Decisions' writes (0.7.0): the person's answers, their withdrawal and the format-rule tidy, against onloopd's real
// server over the demo ledger. This file's name sorts after every other spec's on purpose: the specs run in name order
// against one server per project, and these writes add events the others count (Audit folds the heartbeat's pings
// into one row only while nothing else comes between them). Each test still undoes what it wrote.

type Watched = Page & { problems: string[]; writes: string[]; answers?: boolean };
const ANSWERS = ["POST /ui/tasks/resolve", "POST /ui/tasks/withdraw", "POST /ui/tasks/tidy", "POST /ui/tasks/merge", "POST /ui/tasks/unmerge", "POST /ui/tasks/keep_apart"];

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

test("Home's row answers with the suggested option in one click, with the same undo", async ({ page }, info) => {
  test.skip(info.project.name === "phone", "the row's suggestion is a wide-screen shortcut, as in F; a phone opens the row");
  (page as Watched).answers = true;
  await page.goto("/#/");
  const row = page.getByTestId("decision").first();
  const btn = row.getByTestId("row-suggested");
  await expect(btn).toHaveText("Approve");
  await expect(page.getByTestId("decision").nth(2).getByTestId("row-suggested")).toHaveCount(0); // no options: no suggestion
  await btn.click();
  const toast = page.getByTestId("toast").filter({ hasText: "Approve. operator sees it at its next prompt." });
  await expect(toast).toBeVisible();
  await expect(page.getByTestId("decisions-count")).toHaveText("6");
  await expect(page.getByTestId("sidebar").getByTestId("decisions-badge")).toHaveText("6");
  // right after Undo: every frame shows one count, Home's and the sidebar's together (lib/inStep.ts)
  await page.evaluate(() => {
    const w = window as unknown as { __counts: string[] };
    w.__counts = [];
    const read = () => {
      const badge = document.querySelector('[data-testid="sidebar"] [data-testid="decisions-badge"]')?.textContent;
      const home = document.querySelector('[data-testid="decisions-count"]')?.textContent;
      w.__counts.push(`${badge}/${home}`);
      if (w.__counts.length < 400) requestAnimationFrame(read);
    };
    requestAnimationFrame(read);
  });
  await toast.getByRole("button", { name: "Undo" }).click();
  await expect(page.getByTestId("decisions-count")).toHaveText("7");
  await expect(page.getByTestId("sidebar").getByTestId("decisions-badge")).toHaveText("7");
  const frames = await page.evaluate(() => (window as unknown as { __counts: string[] }).__counts);
  expect(frames.filter((f) => f !== "6/6" && f !== "7/7"), "a frame with two counts").toEqual([]);
  expect(frames).toContain("7/7");
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

test("Keep apart is logged, so the proposal stops in every browser, and its toast undoes it", async ({ page }) => {
  (page as Watched).answers = true;
  await page.goto("/#/decisions");
  const card = page.getByTestId("merge-proposal");
  await card.getByTestId("keep-apart").click();
  const toast = page.getByTestId("toast").filter({ hasText: "Kept apart" });
  await expect(toast).toBeVisible();
  await expect(card).toHaveCount(0);
  // kept apart in the ledger, not in this browser: a fresh browser context does not see the proposal either
  const fresh = await page.context().browser()!.newContext({ baseURL: new URL(page.url()).origin });
  const other = await fresh.newPage();
  await other.goto("/#/decisions");
  await expect(other.getByTestId("decision-row").first()).toBeVisible();
  await expect(other.getByTestId("merge-proposal")).toHaveCount(0);
  await fresh.close();
  await toast.hover(); // the toast holds while the pointer is on it
  await toast.getByRole("button", { name: "Undo" }).click();
  await expect(card).toHaveCount(1); // the same facts again: the proposal written for them is back
});

test("the person confirms a proposed merge: one decision with both asks counted, undone from the toast and from Done", async ({ page }, info) => {
  (page as Watched).answers = true;
  await page.goto("/#/decisions");
  const card = page.getByTestId("merge-proposal");
  await expect(card).toHaveCount(1);
  await card.getByTestId("merge").click();
  const toast = page.getByTestId("toast").filter({ hasText: "Merged: one decision now" });
  await expect(toast).toBeVisible();
  await expect(page.getByTestId("tab-open")).toContainText("6");
  const row = page.getByTestId("decision-row").filter({ hasText: /chart palette/i });
  await expect(row).toContainText("asked 2×");
  await expect(row).toContainText("operator, builder");
  // the toast's Undo: two decisions again
  await toast.getByRole("button", { name: "Undo" }).click();
  await expect(page.getByTestId("tab-open")).toContainText("7");
  await expect(row).not.toContainText("asked 2×");
  // merged again, then undone from Done
  await card.getByTestId("merge").click();
  await expect(page.getByTestId("tab-open")).toContainText("6");
  await page.getByTestId("tab-done").click();
  const merged = page.getByTestId("decision-row").filter({ hasText: "pick the report charts' colours" });
  await expect(merged).toContainText("Merged");
  await merged.click();
  const panel = isPhone(info.project.name) ? page.getByTestId("decision-sheet").getByTestId("decision-panel") : page.getByTestId("decision-panel");
  await panel.getByTestId("unmerge").click();
  await expect(page.getByTestId("toast").filter({ hasText: "Merge undone" })).toBeVisible();
  if (isPhone(info.project.name)) await page.keyboard.press("Escape");
  await page.getByTestId("tab-open").click();
  await expect(page.getByTestId("tab-open")).toContainText("7");
});
