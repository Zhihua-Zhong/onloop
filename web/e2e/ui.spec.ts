import { expect, test, type Page } from "@playwright/test";

// What a person does on each view, against onloopd's real server over the demo ledger (e2e/demo_server.py).
// Every test fails on a console error (a CSP refusal shows up as one) and on any request that is not a GET: the UI is
// read-only (decision 0009), but for the visit the page records for "since you were away" (decision 0013), which is
// allowed here, and the writes a test makes on purpose.

type Watched = Page & { problems: string[]; writes: string[]; allow400?: boolean };

test.beforeEach(async ({ page }) => {
  const p = page as Watched;
  p.problems = [];
  p.writes = [];
  // A refused stream request (the stale-banner test aborts it) is the network's error, not the page's.
  page.on("console", (m) => m.type() === "error" && !(m.location().url ?? "").includes("/api/stream") && p.problems.push(`${m.text()} ${m.location().url ?? ""}`));
  page.on("pageerror", (e) => p.problems.push(String(e)));
  page.on("request", (r) => r.method() !== "GET" && !r.url().endsWith("/ui/visits") && p.writes.push(`${r.method()} ${r.url()}`));
});
test.afterEach(async ({ page }) => {
  const p = page as Watched;
  expect(p.problems.filter((x) => !(p.allow400 && x.includes("status of 400")))).toEqual([]);
  expect(p.writes).toEqual([]);
});

const isPhone = (name: string) => name === "phone";

test("Now answers the three questions: the constraint, what waits on you, where work stalled", async ({ page }) => {
  await page.goto("/#/now");
  await expect(page.getByTestId("constraint-headline")).toContainText("merge-check holds 5 changes");
  // with tasks waiting, the constraint is one line above the list (vision round 3)
  await expect(page.getByTestId("next-text")).toHaveText(/^Merge the change only you may merge: it holds merge-check/);
  await expect(page.getByTestId("next-link")).toHaveAttribute("href", /\/pull\/99$/);
  const waiting = page.getByTestId("col-waiting");
  const rows = waiting.getByTestId("task-rows").locator("li");
  await expect(rows).toHaveCount(10); // the task list (decision 0013), one row each, in its one order
  await expect(rows.first().getByTestId("row-effect")).toHaveText("approve the schema change, so the import can run on real data");
  await expect(rows.first().getByTestId("row-cost")).toHaveText("costing now");
  await expect(rows.last()).toBeInViewport(); // every task on the first screen
  await rows.first().locator("button").first().click(); // a row opens to its card, with the default and the actions
  await expect(waiting).toContainText("If nobody answers: nothing merges");
  await expect(waiting.getByTestId("mark-done")).toBeVisible();
  const stalls = page.getByTestId("col-stalls");
  await expect(stalls.locator("article")).toHaveCount(4);
  await expect(stalls.getByTestId("would").first()).toContainText("would wake (observe only)");
  await expect(stalls.getByTestId("ack").first()).toHaveText("not acknowledged");
  const health = page.getByTestId("col-health");
  await expect(health.getByTestId("health-nightly-verify")).toContainText("failing");
  await expect(health.getByTestId("health-dependency-scan")).toContainText("overdue");
  await expect(health.getByTestId("dead-man")).toContainText("dead-man switch OK");
  await expect(health.getByTestId("health-ok")).toContainText("4 other schedules within their period");
});

test("Now's first screen holds the banner and the first card of each question", async ({ page }, info) => {
  await page.goto("/#/now");
  await expect(page.getByTestId("constraint")).toBeInViewport();
  // the list comes first, after a one-line constraint and the one-line band; on a phone, at least five rows show
  const rows = page.getByTestId("task-rows").locator("li");
  await expect(rows.nth(4)).toBeInViewport();
  if (!isPhone(info.project.name)) {
    await expect(page.getByTestId("col-stalls").locator("article").first()).toBeInViewport();
  }
});

test("Team shows one lane per role, who needs you first", async ({ page }) => {
  await page.goto("/#/team");
  const lanes = page.locator('[data-testid^="lane-"]');
  await expect(lanes).toHaveCount(6);
  await expect(lanes.first()).toHaveAttribute("data-testid", "lane-reviewer");
  await expect(page.getByTestId("lane-reviewer")).toContainText("paused by you");
  await expect(page.getByTestId("lane-reviewer").getByTestId("command")).toContainText("onloop role resume reviewer");
  await expect(page.getByTestId("lane-builder")).toContainText("stalled");
  await expect(page.getByTestId("lane-operator")).toContainText("working");
  await expect(page.getByTestId("lane-data-eng")).toContainText("waiting on operator");
});

test("Schedules lists every job, failing first, and opens a log tail", async ({ page }) => {
  await page.goto("/#/schedules");
  const rows = page.locator('[data-testid^="schedule-"]');
  await expect(rows).toHaveCount(6);
  await expect(rows.first()).toHaveAttribute("data-testid", "schedule-nightly-verify");
  await expect(page.getByTestId("schedule-dependency-scan").getByTestId("job-state")).toContainText("overdue");
  await page.getByTestId("schedule-nightly-verify").getByRole("row").click();
  await expect(page.getByTestId("log-tail")).toContainText("checking the contract tests ... FAILED");
  await expect(page).toHaveURL(/open=nightly-verify/);
});

test("Audit filters the timeline by kind and by subject, and pages back", async ({ page }) => {
  await page.goto("/#/audit");
  const view = page.getByTestId("view-audit");
  await expect(page.getByTestId("event").first()).toBeVisible();
  await expect(view).toHaveAttribute("data-events", "100");
  // The heartbeat's pings, one a minute, are one row with their count.
  const counts = await page.locator('[data-testid="event"]', { hasText: "schedule:onloop-heartbeat" }).evaluateAll((els) => els.map((e) => Number(e.getAttribute("data-count"))));
  expect(Math.max(...counts)).toBeGreaterThan(1); // folded into one row; how many fit depends on the carrier events around them
  await page.getByTestId("older").click();
  await expect.poll(async () => Number(await view.getAttribute("data-events"))).toBeGreaterThan(100);
  if (await page.getByRole("button", { name: /^Filters/ }).isVisible()) await page.getByRole("button", { name: /^Filters/ }).click();
  await page.getByLabel("Kind").fill("role.paused");
  await page.getByRole("button", { name: "Filter", exact: true }).click();
  await expect(page.getByTestId("event")).toHaveCount(1);
  await expect(page.getByTestId("event")).toContainText("role:reviewer");
  await page.goto("/#/audit?subject=schedule:nightly-verify");
  await expect(view).toHaveAttribute("data-events", "5"); // declared, one success, three failures
  await expect(page.getByTestId("event")).toHaveCount(3); // the three failures in a row are one row
  await expect(page.getByTestId("event").first()).toHaveAttribute("data-count", "3");
  (page as Watched).allow400 = true; // the refusal is the point of this step
  await page.goto("/#/audit?kind=Bad Kind");
  await expect(page.getByTestId("error")).toContainText("kind is lowercase");
});

test("when the event stream drops, the page says so and keeps what it read", async ({ page }) => {
  await page.route("**/api/stream**", (r) => r.abort());
  await page.goto("/#/now");
  await expect(page.getByTestId("constraint")).toBeVisible();
  await expect(page.getByTestId("stale")).toBeVisible({ timeout: 12_000 });
  await expect(page.getByTestId("stale")).toContainText("Live updates stopped");
  await expect(page.getByTestId("constraint")).toBeVisible();
});

test("under a dark system theme the page is dark, and the toggle still wins", async ({ browser }) => {
  // A fresh context, so nothing is stored: the system's preference decides.
  const ctx = await browser.newContext({ colorScheme: "dark" });
  const page = await ctx.newPage();
  await page.goto("/#/now");
  await expect(page.getByTestId("constraint")).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  const bg = () => page.locator("body").evaluate((el) => getComputedStyle(el).backgroundColor);
  expect(await bg()).toBe("rgb(17, 20, 28)"); // --bg in dark: hsl(224 24% 9%)
  await page.getByTestId("theme").click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light"); // the person's choice is kept
  await page.emulateMedia({ colorScheme: "light" });
  await ctx.close();
  const fresh = await browser.newContext({ colorScheme: "light" });
  const p2 = await fresh.newPage();
  await p2.goto("/#/now");
  await expect(p2.locator("html")).toHaveAttribute("data-theme", "light");
  await p2.emulateMedia({ colorScheme: "dark" }); // the system theme changes while the page is open
  await expect(p2.locator("html")).toHaveAttribute("data-theme", "dark");
  await fresh.close();
});

test("an old Flow or Activity link opens where its parts moved", async ({ page }) => {
  await page.goto("/#/flow");
  await expect(page.getByTestId("nav-map")).toHaveAttribute("aria-current", "page");
  await expect(page).toHaveURL(/#\/map$/); // the address names the view on screen
  await page.goto("/#/activity");
  await expect(page.getByTestId("nav-replay")).toHaveAttribute("aria-current", "page");
  await expect(page).toHaveURL(/#\/replay/);
});

test("the theme toggles and the navigation reaches every view", async ({ page }) => {
  await page.goto("/?theme=light#/now");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.getByTestId("theme").click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  // Now, Replay and the map first, then the rest (decision 0019); Flow and Activity are gone from the bar
  const order = await page.locator('nav[aria-label="Views"] a').evaluateAll((els) => els.map((e) => e.getAttribute("data-testid")));
  expect(order.slice(0, 3)).toEqual(["nav-now", "nav-replay", "nav-map"]);
  expect(order).not.toContain("nav-flow");
  expect(order).not.toContain("nav-activity");
  const root: Record<string, string> = { replay: "replay", map: "map-level" };
  for (const v of ["replay", "map", "team", "schedules", "audit", "now"]) {
    await page.getByTestId(`nav-${v}`).click();
    await expect(page.getByTestId(root[v] ?? `view-${v}`).first()).toBeVisible();
    await expect(page.getByTestId(`nav-${v}`)).toHaveAttribute("aria-current", "page");
  }
});
