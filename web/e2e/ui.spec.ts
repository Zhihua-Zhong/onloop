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

// 0.7.0's shell (plan, section 1, step 1): the sidebar on a wide screen, the same list in a drawer on a phone.
const nav = (page: Page, key: string) => page.getByTestId("sidebar").getByTestId(`nav-${key}`);
async function go(page: Page, key: string, phone: boolean) {
  const details = ["map", "outcome", "conversation", "schedules", "audit"].includes(key);
  if (phone) await page.getByTestId("menu").click();
  const box = phone ? page.getByTestId("drawer") : page.getByTestId("sidebar");
  if (details && (await box.getByTestId("details-toggle").getAttribute("aria-expanded")) === "false") await box.getByTestId("details-toggle").click();
  await box.getByTestId(`nav-${key}`).click();
  if (phone) await expect(page.getByTestId("drawer")).toHaveCount(0);
}
async function chooseTheme(page: Page, theme: "light" | "dark" | "system") {
  await page.getByTestId("theme").click();
  await page.getByTestId(`theme-${theme}`).click();
}

test("Now answers the three questions: the constraint, what waits on you, where work stalled", async ({ page }, info) => {
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
  // every task on the first screen; on a phone the first screen holds five (the next test), and a person scrolls on
  if (isPhone(info.project.name)) await rows.last().scrollIntoViewIfNeeded();
  await expect(rows.last()).toBeInViewport();
  await rows.first().locator("button").first().click(); // a row opens to its card, with the default and the actions
  await expect(waiting).toContainText("If nobody answers: nothing merges");
  await expect(waiting.getByTestId("mark-done")).toBeVisible();
  const stalls = page.getByTestId("col-stalls");
  // the demo's four stalls, and its one item expiring within a day, which 0.6.0 lists in the same column
  await expect(stalls.locator('article[data-testid^="stall-"]')).toHaveCount(4);
  await expect(stalls.getByTestId("expiring").locator("article")).toHaveCount(1);
  await expect(stalls.locator("article")).toHaveCount(5);
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
  expect(await bg()).toBe("rgb(17, 17, 19)"); // --bg in dark: hsl(240 6% 7%), F's background
  await chooseTheme(page, "light");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light"); // the person's choice is kept
  await chooseTheme(page, "system"); // "System" forgets the choice: the system's dark decides again
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
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

test("the map's zoom 0 opens into the Timeline", async ({ page }) => {
  await page.goto("/#/details/map");
  await page.getByTestId("map-to-replay").click();
  await expect(nav(page, "timeline")).toHaveAttribute("aria-current", "page");
  await expect(page).toHaveURL(/#\/timeline\?hours=24/);
  await expect(page.getByTestId("replay")).toBeVisible();
});

test("an old link opens where its view lives now, and the address names it", async ({ page }) => {
  for (const [old, key, now] of [
    ["/#/flow", "map", /#\/details\/map$/],
    ["/#/activity", "timeline", /#\/timeline(\?|$)/], // Replay adds its own speed to the address
    ["/#/now", "home", /#\/$/],
    ["/#/team", "agents", /#\/agents$/],
    ["/#/replay?hours=24", "timeline", /#\/timeline\?hours=24(&|$)/],
    ["/#/map/changes", "map", /#\/details\/map\/changes$/],
    ["/#/audit?kind=role.paused", "audit", /#\/details\/audit\?kind=role\.paused$/],
  ] as const) {
    await page.goto(old);
    await expect(nav(page, key)).toHaveAttribute("aria-current", "page");
    await expect(page).toHaveURL(now);
  }
});

test("the theme menu switches, and the navigation reaches every view, Details one click down", async ({ page }, info) => {
  const phone = isPhone(info.project.name);
  await page.goto("/?theme=light#/");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await chooseTheme(page, "dark");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  // F's order: Home, Decisions (not built yet: marked, and not a link), Agents, Timeline; then Details, folded
  const order = await page.getByTestId("sidebar").locator('[data-testid^="nav-"]').evaluateAll((els) => els.map((e) => `${e.tagName}:${e.getAttribute("data-testid")}`));
  expect(order.slice(0, 4)).toEqual(["A:nav-home", "SPAN:nav-decisions", "A:nav-agents", "A:nav-timeline"]);
  await expect(nav(page, "decisions")).toHaveAttribute("aria-disabled", "true");
  await expect(nav(page, "decisions")).toContainText("not built yet");
  if (!phone) await expect(nav(page, "map")).toBeHidden(); // Details is folded on a main page
  const root: Record<string, string> = { home: "constraint", timeline: "replay", map: "map-level", agents: "view-team" };
  for (const v of ["timeline", "map", "agents", "schedules", "audit", "outcome", "home"]) {
    await go(page, v, phone);
    await expect(page.getByTestId(root[v] ?? `view-${v}`).first()).toBeVisible();
    await expect(nav(page, v)).toHaveAttribute("aria-current", "page");
  }
  // a page shown by its 0.6 view says so until its 0.7.0 page lands
  await expect(page.getByTestId("interim")).toContainText("Home shows 0.6's Now view");
});

test("every overlay's content is opaque the moment it opens, inside the window, and Escape closes it", async ({ page }, info) => {
  const phone = isPhone(info.project.name);
  await page.goto("/#/details/schedules");
  await expect(page.getByTestId("schedule-nightly-verify")).toBeVisible();
  // the product of every ancestor's opacity, read in the first animation frame the overlay is there (plan, section 7)
  const opacity = (testid: string) =>
    page
      .waitForFunction(
        (id) => {
          const el = document.querySelector(`[data-testid="${id}"]`);
          if (!el) return null;
          let o = 1;
          for (let n: Element | null = el; n; n = n.parentElement) o *= Number(getComputedStyle(n).opacity);
          return o;
        },
        testid,
        { polling: "raf" },
      )
      .then((h) => h.jsonValue());
  const inside = (testid: string) =>
    page.getByTestId(testid).evaluate((el) => {
      const r = el.getBoundingClientRect();
      return r.left >= -0.5 && r.top >= -0.5 && r.right <= innerWidth + 0.5 && r.bottom <= innerHeight + 0.5;
    });
  if (phone) {
    await page.getByTestId("menu").click();
    expect(await opacity("drawer")).toBe(1);
    await expect.poll(() => inside("drawer")).toBe(true);
    await expect(page.getByTestId("drawer").getByTestId("nav-schedules")).toBeFocused(); // it opens where you are
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("drawer")).toHaveCount(0);
  }
  await page.getByTestId("theme").click();
  expect(await opacity("theme-menu")).toBe(1);
  await expect.poll(() => inside("theme-menu")).toBe(true);
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("theme-menu")).toHaveCount(0);
  await expect(page.getByTestId("theme")).toBeFocused();
});
