import { expect, test, type Locator, type Page } from "@playwright/test";

// 0.7.0 plan, section 7: every overlay, ⌘K above all, opened and its content checked at every breakpoint; an overlay's
// content never fades in (only the dim behind it does), so it is fully opaque within 50 ms of opening. ⌘K's question is
// answered from the demo's recorded answer (tests/fixtures/brief/ask.json); the model is never called. POST /ui/ask is
// the only write these tests make (a question is a POST, under the page's CSRF rules); nothing else is written.

type Watched = Page & { problems: string[]; writes: string[] };

test.beforeEach(async ({ page }) => {
  const p = page as Watched;
  p.problems = [];
  p.writes = [];
  page.on("console", (m) => m.type() === "error" && !(m.location().url ?? "").includes("/api/stream") && p.problems.push(`${m.text()} ${m.location().url ?? ""}`));
  page.on("pageerror", (e) => p.problems.push(String(e)));
  page.on("request", (r) => r.method() !== "GET" && !r.url().endsWith("/ui/visits") && !r.url().endsWith("/ui/ask") && p.writes.push(`${r.method()} ${r.url()}`));
});
test.afterEach(async ({ page }) => {
  const p = page as Watched;
  expect(p.problems).toEqual([]);
  expect(p.writes).toEqual([]);
});

const BREAKPOINTS = [390, 640, 768, 800, 1024, 1280, 1536] as const;
const QUESTION = "Who is working, and who is waiting on me?";

/** The element and every ancestor fully opaque, and its box inside the viewport. */
async function opaqueAndInside(box: Locator): Promise<{ opacity: number; inside: boolean; why: string }> {
  // where it comes to rest: a sheet slides in (its opacity is 1 throughout; that is checked on its own)
  await box.evaluate((el) =>
    Promise.all(
      el
        .getAnimations({ subtree: true })
        .filter((a) => a.effect?.getComputedTiming().iterations !== Infinity) // a spinner never ends
        .map((a) => a.finished.catch(() => undefined)),
    ),
  );
  return box.evaluate((el) => {
    let o = 1;
    for (let n: Element | null = el; n; n = n.parentElement) o *= Number(getComputedStyle(n).opacity);
    const r = el.getBoundingClientRect();
    const inside = r.width > 0 && r.height > 0 && r.left >= -1 && r.top >= -1 && r.right <= window.innerWidth + 1 && r.bottom <= window.innerHeight + 1;
    return { opacity: o, inside, why: `${Math.round(r.left)},${Math.round(r.top)} ${Math.round(r.width)}x${Math.round(r.height)} in ${window.innerWidth}x${window.innerHeight}` };
  });
}

/** Ctrl+K pressed in the page, and the dialog's opacity sampled every frame until 50 ms: opaque at the first sample. */
async function openCommandTimed(page: Page): Promise<{ ms: number; opacity: number }> {
  return page.evaluate(
    () =>
      new Promise<{ ms: number; opacity: number }>((resolve) => {
        const t0 = performance.now();
        window.dispatchEvent(new KeyboardEvent("keydown", { key: "k", ctrlKey: true, bubbles: true }));
        const tick = () => {
          const el = document.querySelector('[data-testid="command"]');
          const ms = performance.now() - t0;
          if (el) {
            let o = 1;
            for (let n: Element | null = el; n; n = n.parentElement) o *= Number(getComputedStyle(n).opacity);
            resolve({ ms, opacity: o });
          } else if (ms > 1000) resolve({ ms, opacity: 0 });
          else requestAnimationFrame(tick);
        };
        tick();
      }),
  );
}

for (const w of BREAKPOINTS) {
  test(`⌘K at ${w} px: opaque at once, and the answer to a question is visible inside the viewport`, async ({ page }, info) => {
    test.skip(info.project.name === "phone" && w !== 390, "the phone project checks its own width");
    await page.setViewportSize({ width: w, height: w < 700 ? 844 : 900 });
    await page.goto("/#/");
    await expect(page.getByTestId("brief")).toBeVisible();
    const { ms, opacity } = await openCommandTimed(page);
    expect(ms, "the dialog is on screen within 50 ms").toBeLessThan(50);
    expect(opacity, "and fully opaque when it first shows").toBe(1);
    const cmd = page.getByTestId("command");
    await page.getByTestId("command-input").fill(QUESTION);
    await page.keyboard.press("Enter");
    const answer = page.getByTestId("command-answer-text");
    await expect(answer).toContainText("Only builder is working");
    const seen = await opaqueAndInside(answer);
    expect(seen.opacity, seen.why).toBe(1);
    expect(seen.inside, seen.why).toBe(true);
    expect((await opaqueAndInside(cmd)).inside).toBe(true);
    await expect(page.getByTestId("command-cites")).toContainText("builder");
    await expect(page.getByTestId("command-answer")).toContainText(/Written by onloop from this week's data · facts [0-9a-f]{7}/);
    // a cited fact opens where it is
    await page.getByTestId("command-cites").getByRole("button", { name: "Approve the schema change for the import" }).click();
    await expect(page).toHaveURL(/#\/decisions\?id=t-demo0001$/);
    await expect(page.getByTestId("command")).toHaveCount(0);
  });

  test(`every menu and sheet at ${w} px is opaque and inside the viewport`, async ({ page }, info) => {
    test.skip(info.project.name === "phone" && w !== 390, "the phone project checks its own width");
    await page.setViewportSize({ width: w, height: w < 700 ? 844 : 900 });
    await page.goto("/#/");
    await expect(page.getByTestId("brief")).toBeVisible();
    for (const [trigger, content] of [
      ["bell", "bell-menu"],
      ["theme", "theme-menu"],
      ["account", "account-menu"],
    ] as const) {
      await page.getByTestId(trigger).click();
      const box = page.getByTestId(content);
      await expect(box).toBeVisible();
      const seen = await opaqueAndInside(box);
      expect(seen.opacity, `${content} ${seen.why}`).toBe(1);
      expect(seen.inside, `${content} ${seen.why}`).toBe(true);
      await page.keyboard.press("Escape");
      await expect(box).toHaveCount(0);
    }
    if (w < 1024) {
      // the drawer (the sidebar on a narrow screen)
      await page.getByTestId("menu").click();
      const drawer = page.getByTestId("drawer");
      await expect(drawer).toBeVisible();
      expect((await opaqueAndInside(drawer)).opacity).toBe(1);
      await page.keyboard.press("Escape");
      // the decision sheet
      await page.goto("/#/decisions");
      await page.getByTestId("decision-row").first().click();
      const sheet = page.getByTestId("decision-sheet");
      await expect(sheet.getByTestId("answers")).toBeVisible();
      const seen = await opaqueAndInside(sheet);
      expect(seen.opacity, seen.why).toBe(1);
      expect(seen.inside, seen.why).toBe(true);
      await expect(sheet.getByTestId("answers")).toBeInViewport();
      await page.keyboard.press("Escape");
    }
  });
}

test("⌘K goes anywhere: a page, a decision, an agent", async ({ page }, info) => {
  await page.goto("/#/");
  await page.locator('[data-testid="search"]:visible').click();
  await page.getByTestId("command-input").fill("timeline");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/#\/timeline$/);
  await page.keyboard.press("Control+k");
  await page.getByTestId("command-input").fill("chart palette");
  await page.getByTestId("command-item").filter({ hasText: "Choose the chart palette" }).click();
  await expect(page).toHaveURL(/#\/decisions\?id=t-demo0004$/);
  // the decision opens: beside the list, or as a sheet on a phone (closed before the next search)
  const phone = info.project.name === "phone";
  const panel = phone ? page.getByTestId("decision-sheet").getByTestId("decision-panel") : page.getByTestId("decision-panel");
  await expect(panel).toHaveAttribute("data-id", "t-demo0004");
  if (phone) {
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("decision-sheet")).toHaveCount(0);
  }
  await page.keyboard.press("Control+k");
  await page.getByTestId("command-input").fill("designer");
  await page.getByTestId("command-item").filter({ hasText: "designer" }).first().click();
  await expect(page).toHaveURL(/#\/agents\/designer$/);
});

test("a question onloop does not answer says why and searches instead; with nothing matching, it offers the pages", async ({ page }) => {
  await page.goto("/#/");
  await page.keyboard.press("Control+k");
  await page.getByTestId("command-input").fill("which decision is about the chart palette?");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("command-off")).toContainText("onloop can't answer that here: the demo answers only its recorded question. Here is what matches.");
  await expect(page.getByTestId("command-item").filter({ hasText: "Choose the chart palette" })).toBeVisible();
  await expect(page.getByTestId("command-ask")).toHaveCount(0);
  await page.getByTestId("command-input").fill("zzz nothing like this?");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("command-off")).toBeVisible();
  await expect(page.getByTestId("command-item").filter({ hasText: "Decisions" })).toBeVisible();
  await expect(page.getByTestId("command-results")).toContainText("Where to look");
});

test("the morning digest is a preview: what to open first, marked as not sent", async ({ page }) => {
  await page.goto("/#/");
  await page.getByTestId("open-digest").click();
  await expect(page).toHaveURL(/#\/digest$/);
  await expect(page.getByTestId("digest-preview")).toHaveText("Preview · not sent anywhere yet");
  await expect(page.getByTestId("digest-decision")).toHaveCount(3);
  await expect(page.getByTestId("digest-decision").first()).toContainText("Approve the schema change for the import");
  await expect(page.getByTestId("digest-since")).toContainText("New asks");
  await page.getByTestId("digest-decision").first().click();
  await expect(page).toHaveURL(/#\/decisions\?id=t-demo0001$/);
});

test("an old link that named a task opens that decision", async ({ page }, info) => {
  await page.goto("/#/tasks?id=t-demo0007");
  await expect(page).toHaveURL(/#\/decisions\?id=t-demo0007$/);
  const panel = info.project.name === "phone" ? page.getByTestId("decision-sheet").getByTestId("decision-panel") : page.getByTestId("decision-panel");
  await expect(panel).toHaveAttribute("data-id", "t-demo0007");
});

test("the header's controls: search, the bell with what is new, and the account marked as not built", async ({ page }) => {
  await page.goto("/#/");
  await page.getByTestId("bell").click();
  await expect(page.getByTestId("bell-menu")).toContainText("Shown here only: nothing is sent to you yet.");
  await page.keyboard.press("Escape");
  await page.getByTestId("account").click();
  await expect(page.getByTestId("account-menu")).toContainText("Local user on this machine");
  await expect(page.getByTestId("account-signin")).toHaveAttribute("data-disabled", "");
  await expect(page.getByTestId("account-signin")).toContainText("later");
});

test("Unusual says when in plain words, and the exact time on hover", async ({ page }, info) => {
  test.skip(info.project.name === "phone", "hover");
  await page.goto("/#/");
  const quiet = page.getByTestId("unusual-item").filter({ hasText: "quiet for" }).getByTestId("unusual-text");
  await expect(quiet).toContainText(/its last turn ended \d+ days? ago$/);
  await expect(quiet).not.toContainText("UTC");
  await quiet.hover();
  await expect(page.getByRole("tooltip")).toContainText("Last turn ended");
});
