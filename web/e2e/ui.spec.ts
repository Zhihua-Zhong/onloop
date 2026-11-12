import { expect, test, type Page } from "@playwright/test";

// What a person does on each view, against onloopd's real server over the demo ledger (e2e/demo_server.py).
// Every test fails on a console error (a CSP refusal shows up as one) and on any request that is not a GET: the UI is
// read-only (decision 0009).

type Watched = Page & { problems: string[]; writes: string[]; allow400?: boolean };

test.beforeEach(async ({ page }) => {
  const p = page as Watched;
  p.problems = [];
  p.writes = [];
  // A refused stream request (the stale-banner test aborts it) is the network's error, not the page's.
  page.on("console", (m) => m.type() === "error" && !(m.location().url ?? "").includes("/api/stream") && p.problems.push(`${m.text()} ${m.location().url ?? ""}`));
  page.on("pageerror", (e) => p.problems.push(String(e)));
  page.on("request", (r) => r.method() !== "GET" && p.writes.push(`${r.method()} ${r.url()}`));
});
test.afterEach(async ({ page }) => {
  const p = page as Watched;
  expect(p.problems.filter((x) => !(p.allow400 && x.includes("status of 400")))).toEqual([]);
  expect(p.writes).toEqual([]);
});

const isPhone = (name: string) => name === "phone";

// A card that is drawn inside the visible part of the canvas, to click as a person would.
async function visibleCard(page: Page, selector: string) {
  const vp = page.viewportSize()!;
  for (const el of await page.locator(selector).all()) {
    const b = await el.boundingBox();
    if (b && b.x >= 0 && b.y >= 100 && b.x + b.width <= vp.width && b.y + b.height <= vp.height) return el;
  }
  throw new Error(`no ${selector} inside the viewport`);
}

test("Now answers the three questions: the constraint, what waits on you, where work stalled", async ({ page }) => {
  await page.goto("/#/now");
  await expect(page.getByTestId("constraint-headline")).toContainText("merge-check holds 5 changes");
  await expect(page.getByTestId("constraint-numbers")).toHaveText(/^5 changes queued\s*·\s*.+ waiting in total\s*·\s*oldest .+$/);
  await expect(page.getByTestId("constraint-related")).toContainText("3 changes conflict with main");
  await expect(page.getByTestId("next-text")).toHaveText(/^Merge the change only you may merge: it holds merge-check/);
  await expect(page.getByTestId("next-link")).toHaveAttribute("href", /\/pull\/99$/);
  const waiting = page.getByTestId("col-waiting");
  await expect(waiting.locator("article")).toHaveCount(3);
  await expect(waiting).toContainText("If nobody answers: next Monday");
  await expect(waiting.getByTestId("left").first()).toContainText("min left");
  await expect(waiting.getByTestId("command")).toContainText("onloop role resume reviewer");
  const stalls = page.getByTestId("col-stalls");
  await expect(stalls.locator("article")).toHaveCount(2);
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
  const first = page.getByTestId("col-waiting").locator("article").first();
  await expect(first).toBeInViewport();
  if (!isPhone(info.project.name)) {
    await expect(page.getByTestId("col-stalls").locator("article").first()).toBeInViewport();
    await expect(page.getByTestId("col-health").locator("article").first()).toBeInViewport();
  }
});

test("Flow draws the resources, links the waiting cards and overlays the constraint", async ({ page }, info) => {
  test.skip(isPhone(info.project.name), "the desktop canvas");
  await page.goto("/#/flow");
  await expect(page.getByTestId("canvas")).toHaveAttribute("data-opening", "constraint");
  await expect(page.getByTestId("res-merge-check")).toBeInViewport();
  await expect(page.getByTestId("res-human")).toBeInViewport();
  expect(await page.locator(".react-flow__viewport").evaluate((el) => new DOMMatrix(getComputedStyle(el).transform).a)).toBeCloseTo(0.9, 2);
  await expect(page.getByTestId("res-merge-check")).toHaveAttribute("data-constraint", "1");
  await expect(page.getByTestId("res-human")).toHaveAttribute("data-constraint", "0");
  await expect(page.getByTestId("res-main")).toBeVisible();
  // By default no line runs from a resource: each waiting card names it with a chip.
  expect(await page.locator(".react-flow__edge.edge-wait-constraint").count()).toBe(0);
  await expect(page.getByTestId("card-pr#101").getByTestId("wait-chip")).toHaveText("Queued → merge-check");
  await expect(page.getByTestId("card-pr#106").getByTestId("wait-chip")).toHaveText("Blocked → main");
  await expect(page.getByTestId("card-wg:n-date")).toContainText("Waiting on you");
  // Selecting the queue traces its five cards; selecting the pane hides the trace again.
  await page.getByTestId("res-merge-check").click();
  await expect(page.locator(".react-flow__edge.edge-wait-constraint")).toHaveCount(5);
  await page.locator(".react-flow__pane").click({ position: { x: 5, y: 5 } });
  await page.mouse.move(1, 1);
  await expect(page.locator(".react-flow__edge.edge-wait-constraint")).toHaveCount(0);
  await expect(page.getByTestId("card-pr#101")).toHaveAttribute("data-dim", "0"); // held by the constraint
  await expect(page.getByTestId("card-wg:n-notes")).toHaveAttribute("data-dim", "1");
  await expect(page.getByTestId("card-wg:n-date")).toHaveAttribute("data-dim", "0"); // what waits on you is never dimmed
  await page.getByTestId("overlay").click();
  await expect(page.getByTestId("overlay")).not.toBeChecked();
  await expect(page.getByTestId("card-wg:n-notes")).toHaveAttribute("data-dim", "0");
  await expect(page.getByTestId("zone-unassigned").getByTestId("zone-source")).toHaveAttribute("title", /neither a direction nor an owner/);
  await expect(page.getByTestId("zone-role:builder").getByTestId("zone-source")).toHaveAttribute("title", /role that owns/);
  await expect(page.getByTestId("zone-wg:t-release").getByTestId("zone-source")).toHaveAttribute("title", /its direction/);
});

for (const mode of ["graph", "you"]) {
  test(`no line in the default ${mode} view joins two zones`, async ({ page }) => {
    await page.goto(mode === "graph" ? "/#/flow" : "/#/flow?mode=you");
    await expect(page.getByTestId("canvas")).toHaveAttribute("data-opening", /.+/);
    await page.mouse.move(1, 1);
    const crossing = await page.locator(".react-flow__edge").evaluateAll(async (els) => {
      const m = await (await fetch("/api/flow")).json();
      const zone = new Map(m.cards.map((c: { id: string; zone: string }) => [c.id, c.zone]));
      const ends = new Map(m.edges.map((e: { id: string; source: string; target: string }) => [e.id, e]));
      return els
        .map((el) => el.getAttribute("data-id") ?? "")
        .filter((id) => {
          if (id.startsWith("wait:")) return true; // a line from a resource is a trace, never drawn by default
          const e = ends.get(id) as { source: string; target: string } | undefined;
          return !e || zone.get(e.source) !== zone.get(e.target);
        });
    });
    expect(crossing).toEqual([]);
  });
}

test("a waiting card traces its line to what it waits on while it is selected", async ({ page }) => {
  await page.goto("/#/flow");
  await expect(page.getByTestId("canvas")).toHaveAttribute("data-opening", /.+/);
  await (await visibleCard(page, '[data-look="human"], [data-look]:has([data-testid="wait-chip"])')).click();
  await expect(page.getByTestId("canvas")).toHaveAttribute("data-focus", /.+/);
  expect(await page.locator('.react-flow__edge[data-id^="wait:"]').count()).toBeGreaterThanOrEqual(1);
});

test("a card opens its details: the zone, what it waits on, and the command where there is one", async ({ page }) => {
  await page.goto("/#/flow");
  await expect(page.getByTestId("canvas")).toHaveAttribute("data-opening", /.+/);
  await (await visibleCard(page, '[data-look="human"]')).click();
  const panel = page.getByTestId("side-panel");
  await expect(panel).toBeVisible();
  await expect(panel).toContainText("Waits on");
  await page.keyboard.press("Escape");
  await expect(panel).toBeHidden();
});

test("the view switch: waiting on you, roadmap and calendar", async ({ page }) => {
  await page.goto("/#/flow");
  await page.getByTestId("mode-you").click();
  await expect(page.getByTestId("view-flow")).toHaveAttribute("data-mode", "you");
  await expect(page.getByTestId("canvas")).toHaveAttribute("data-opening", /.+/);
  const looks = await page.locator("[data-look]").evaluateAll((els) => els.map((e) => e.getAttribute("data-look")));
  expect(looks.length).toBe(6);
  expect(new Set(looks)).toEqual(new Set(["human"]));
  await page.getByTestId("mode-roadmap").click();
  await expect(page.getByTestId("roadmap")).toBeVisible();
  await expect(page.getByTestId("lane-wg:t-release")).toContainText("Pick the release date");
  // No title starts under the lane names, and none runs off the screen.
  const clash = await page.getByTestId("roadmap").evaluate((root) => {
    const vw = window.innerWidth;
    return [...root.querySelectorAll('[data-testid^="lane-"]')].flatMap((lane) => {
      const names = (lane.firstElementChild as HTMLElement).getBoundingClientRect();
      return [...lane.querySelectorAll('[data-testid="roadmap-title"]')]
        .map((t) => t.getBoundingClientRect())
        .filter((b) => b.left < names.right - 1 || b.left > vw - 24)
        .map(() => lane.getAttribute("data-testid"));
    });
  });
  expect(clash).toEqual([]);
  await page.getByTestId("mode-calendar").click();
  await expect(page.getByTestId("cal-done")).toContainText("Refresh the contributor guide");
  await expect(page.getByTestId("cal-now")).toContainText("Add retry to the export job");
  await expect(page.getByTestId("cal-next")).toContainText("backup-db");
});

test("on a phone, Flow opens on the first zone with a human wait, readable, with Show all", async ({ page }, info) => {
  test.skip(!isPhone(info.project.name), "the phone opening");
  await page.goto("/#/flow");
  const canvas = page.getByTestId("canvas");
  await expect(canvas).toHaveAttribute("data-opening", /^(?!all$).+/);
  const opening = await canvas.getAttribute("data-opening");
  const zoom = () => page.locator(".react-flow__viewport").evaluate((el) => new DOMMatrix(getComputedStyle(el).transform).a);
  expect(await zoom()).toBeCloseTo(0.9, 2);
  // The zone it opened on is the first, in reading order, that holds a card waiting on the human, and it is on screen.
  const zone = page.getByTestId(`zone-${opening}`);
  await expect(zone).toBeInViewport();
  await expect(zone).toContainText("for you");
  const human = await visibleCard(page, '[data-look="human"]');
  await expect(human).toBeInViewport();
  const order = await page.locator('[data-testid^="zone-"][data-source]').evaluateAll((els) =>
    els
      .map((e) => ({ id: e.getAttribute("data-testid")!.slice(5), y: e.getBoundingClientRect().y, x: e.getBoundingClientRect().x, you: (e.textContent ?? "").includes("for you") }))
      .sort((a, b) => a.y - b.y || a.x - b.x),
  );
  expect(order.find((z) => z.you && z.id !== "resources")?.id).toBe(opening);
  await page.getByTestId("show-all").click();
  await expect(canvas).toHaveAttribute("data-opening", "all");
  await page.waitForTimeout(400);
  expect(await zoom()).toBeLessThan(0.9);
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
  expect(Math.max(...counts)).toBeGreaterThan(10);
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

test("the theme toggles and the navigation reaches every view", async ({ page }) => {
  await page.goto("/?theme=light#/now");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.getByTestId("theme").click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  for (const v of ["flow", "team", "schedules", "audit", "now"]) {
    await page.getByTestId(`nav-${v}`).click();
    await expect(page.getByTestId(`view-${v}`)).toBeVisible();
    await expect(page.getByTestId(`nav-${v}`)).toHaveAttribute("aria-current", "page");
  }
});
