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
  const details = ["map", "outcome", "conversation", "schedules", "audit", "rules"].includes(key);
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

test("Home answers what needs me, how it went and what came out, and lists no rule alert", async ({ page }) => {
  await page.goto("/#/");
  await expect(page.getByTestId("brief-facts")).toContainText("10 asks wait on you, 6 of them saying what they change.");
  await expect(page.getByTestId("kpi-delivered").getByTestId("kpi-value")).toHaveText("7");
  await expect(page.getByTestId("kpi-waited").getByTestId("kpi-value")).toHaveText(/^\d+ h$/);
  await expect(page.getByTestId("decisions-count")).toHaveText("6");
  const rows = page.getByTestId("decision");
  await expect(rows).toHaveCount(5); // the first five in the one order, collapsed
  await expect(rows.first().getByTestId("decision-title")).toHaveText("approve the schema change, so the import can run on real data");
  await page.getByTestId("decisions-all").click();
  await expect(rows).toHaveCount(6);
  // the asks that do not say what they change are counted, with where they are; none is listed
  await expect(page.getByTestId("rule-asks")).toContainText("4 more asks do not say what they change");
  const text = await page.getByTestId("view-home").innerText();
  for (const word of ["An ask with no effect stated", "outcome line", "Constraint", "Since you", "Stalled", "merge-check holds"]) expect(text).not.toContain(word);
  // the brief's one button has a short label that is never cut, and opens the first decision to its facts and the
  // answers its ask offered (no options recorded: Done and Decline)
  await expect(page.getByTestId("decide-first")).toHaveText("Decide the first of 6");
  await expect(page.getByTestId("decision-facts")).toHaveCount(0);
  await page.getByTestId("decide-first").click();
  await expect(rows.first().getByTestId("decision-facts")).toContainText("If nobody answers");
  await expect(rows.first().getByTestId("decision-facts")).toContainText("nothing merges");
  await expect(rows.first().getByTestId("option")).toHaveText(["Approve", "Approve, import paused", "Wait for the review"]);
  await expect(page.getByTestId("kpi-waited")).toContainText("summed");
  // the evidence: one lane per role, a flag per change merged this week
  await expect(page.getByTestId("lane-names")).toContainText("builder");
  await expect(page.getByTestId("lanes").getByTestId("flag")).toHaveCount(7);
  await expect(page.getByTestId("unusual")).toBeVisible();
});

test("Home's first screen holds the brief, the three numbers and the first decision", async ({ page }, info) => {
  await page.goto("/#/");
  await expect(page.getByTestId("brief")).toBeInViewport();
  await expect(page.getByTestId("numbers")).toBeInViewport();
  if (!isPhone(info.project.name)) await expect(page.getByTestId("decision").first()).toBeInViewport();
});

test("Details · Rule alerts counts what Home does not list, and says where the rows are", async ({ page }) => {
  await page.goto("/#/details/rules");
  await expect(page.getByTestId("rule-asks-missing").getByTestId("count")).toHaveText("4");
  await expect(page.getByTestId("rule-no_outcome").getByTestId("count")).toHaveText("3");
  await expect(page.getByTestId("rule-no_outcome")).toContainText("turns that ended without a valid outcome line");
  await expect(page.getByTestId("rule-expiring").getByTestId("count")).toHaveText("1");
  await expect(page.getByTestId("rule-constraint")).toContainText("waits on merge-check");
  await expect(page.getByTestId("since-visit")).toBeVisible();
  // Now's health card moved here: the failing and overdue jobs, and the dead-man switch
  await expect(page.getByTestId("health-failing").getByTestId("count")).toHaveText("1");
  await expect(page.getByTestId("health-late").getByTestId("count")).toHaveText("1");
  await expect(page.getByTestId("dead-man")).toContainText("is OK");
  await expect(nav(page, "rules")).toHaveAttribute("aria-current", "page");
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
  await page.goto("/#/");
  await expect(page.getByTestId("brief")).toBeVisible();
  await expect(page.getByTestId("stale")).toBeVisible({ timeout: 12_000 });
  await expect(page.getByTestId("stale")).toContainText("Live updates stopped");
  await expect(page.getByTestId("brief")).toBeVisible();
});

test("under a dark system theme the page is dark, and the toggle still wins", async ({ browser }) => {
  // A fresh context, so nothing is stored: the system's preference decides.
  const ctx = await browser.newContext({ colorScheme: "dark" });
  const page = await ctx.newPage();
  await page.goto("/#/");
  await expect(page.getByTestId("brief")).toBeVisible();
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
  await p2.goto("/#/");
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
    ["/#/now", "decisions", /#\/decisions$/], // 0.6's Now and its task list are Decisions
    ["/#/tasks", "decisions", /#\/decisions$/],
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

test("leaving the Timeline is never undone by the Timeline writing its own address", async ({ page }) => {
  // Replay keeps its cursor and speed in the address from timers; one write landing after a link was followed used to
  // pull the page back to the Timeline (found by this spec, 0.7.0 step 2)
  for (let i = 0; i < 6; i++) {
    await page.goto("/#/timeline?hours=24");
    await expect(page.getByTestId("replay")).toBeVisible();
    await page.goto("/#/details/audit");
    await expect(nav(page, "audit")).toHaveAttribute("aria-current", "page");
    await page.waitForTimeout(400);
    await expect(page).toHaveURL(/#\/details\/audit$/);
    await expect(nav(page, "audit")).toHaveAttribute("aria-current", "page");
  }
});

test("the theme menu switches, and the navigation reaches every view, Details one click down", async ({ page }, info) => {
  const phone = isPhone(info.project.name);
  await page.goto("/?theme=light#/");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await chooseTheme(page, "dark");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  // F's order: Home, Decisions (with its count), Agents, Timeline; then Details, folded
  const order = await page.getByTestId("sidebar").locator('[data-testid^="nav-"]').evaluateAll((els) => els.map((e) => `${e.tagName}:${e.getAttribute("data-testid")}`));
  expect(order.slice(0, 4)).toEqual(["A:nav-home", "A:nav-decisions", "A:nav-agents", "A:nav-timeline"]);
  await expect(nav(page, "decisions").getByTestId("decisions-badge")).toHaveText("6");
  if (!phone) await expect(nav(page, "map")).toBeHidden(); // Details is folded on a main page
  const root: Record<string, string> = { home: "brief", timeline: "replay", map: "map-level", agents: "view-team" };
  for (const v of ["decisions", "timeline", "map", "agents", "schedules", "audit", "outcome", "rules", "home"]) {
    await go(page, v, phone);
    await expect(page.getByTestId(root[v] ?? `view-${v}`).first()).toBeVisible();
    await expect(nav(page, v)).toHaveAttribute("aria-current", "page");
  }
  // Home is 0.7.0's own page; a page still shown by its 0.6 view says so until its 0.7.0 page lands
  await expect(page.getByTestId("interim")).toHaveCount(0);
  await go(page, "agents", phone);
  await expect(page.getByTestId("interim")).toContainText("Agents shows 0.6's Team view");
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
