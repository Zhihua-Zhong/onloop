import { expect, test, type Page } from "@playwright/test";

// The Conversation view (blueprint section 4.4; decisions 0010 and 0011) against onloopd's real server over the demo
// ledger. Every test fails on a console error. The page's one write is the message to a role, POST /ui/messages: a
// test that does not send one fails on any request that is not a GET.

type Watched = Page & { problems: string[]; writes: string[]; sends?: boolean; refusals?: boolean };

test.beforeEach(async ({ page }) => {
  const p = page as Watched;
  p.problems = [];
  p.writes = [];
  page.on("console", (m) => m.type() === "error" && !(m.location().url ?? "").includes("/api/stream") && p.problems.push(`${m.text()} ${m.location().url ?? ""}`));
  page.on("pageerror", (e) => p.problems.push(String(e)));
  page.on("request", (r) => r.method() !== "GET" && p.writes.push(`${r.method()} ${new URL(r.url()).pathname}`));
});
test.afterEach(async ({ page }) => {
  const p = page as Watched;
  expect(p.problems.filter((x) => !(p.refusals && x.includes("status of 403")))).toEqual([]);
  expect(p.writes.filter((w) => !(p.sends && w === "POST /ui/messages"))).toEqual([]);
});

test("Conversation lists the carried roles and opens a thread of deliveries, runs and turns", async ({ page }) => {
  await page.goto("/#/conversation?role=builder");
  await expect(page.getByTestId("roles").getByRole("button")).toHaveCount(3);
  const thread = page.getByTestId("thread");
  const human = thread.getByTestId("entry-message").filter({ hasText: "open the PR as a draft" });
  await expect(human).toContainText("this page · local user, not verified");
  await expect(human.getByTestId("delivery")).toContainText("acknowledged");
  await expect(human.getByTestId("delivery")).toContainText("by the turn below");
  await expect(thread.getByTestId("entry-turn").filter({ hasText: "outcome: done" })).toBeVisible();
  await expect(thread.getByTestId("entry-turn").filter({ hasText: "without an outcome line" })).toBeVisible();
  const corrective = thread.getByTestId("entry-message").filter({ hasText: "asked for the outcome line" });
  await expect(corrective.getByTestId("delivery")).toContainText("being processed");
  await expect(thread.getByTestId("entry-run").first()).toContainText("run ok");
  await expect(page.getByTestId("command").first()).toContainText("claude --resume s-builder");
});

test("a role's own words are not in the thread, only its outcome line", async ({ page }) => {
  await page.goto("/#/conversation?role=builder");
  await expect(page.getByTestId("thread")).toBeVisible();
  // The demo builder wrote "PR not opened yet; I'll set the credential first." and "Opened the draft.": neither is stored.
  await expect(page.getByTestId("thread")).not.toContainText("PR not opened yet");
  await expect(page.getByTestId("thread")).not.toContainText("Opened the draft");
});

test("an observe-only role shows the message it did not send, and why", async ({ page }) => {
  await page.goto("/#/conversation?role=reviewer");
  const m = page.getByTestId("thread").getByTestId("entry-message").filter({ hasText: "export retry" });
  await expect(m.getByTestId("delivery")).toContainText("not sent: observe-only (would deliver)");
  await expect(m.getByTestId("delivery")).toContainText("has not opted in to message");
  // onloopd.yaml does not opt the reviewer in to messages: the box is closed, and says which setting opens it
  await expect(page.getByTestId("compose")).toHaveAttribute("data-open", "0");
  await expect(page.getByTestId("compose-off")).toContainText("add message to carrier.roles.reviewer.opt_in");
  await expect(page.getByTestId("roles").getByTestId("role-reviewer").getByTestId("paused")).toBeVisible();
});

test("a refused run is shown as refused, with its note", async ({ page }) => {
  await page.goto("/#/conversation?role=designer");
  await expect(page.getByTestId("thread").getByTestId("entry-run")).toContainText("run refused");
  await expect(page.getByTestId("thread").getByTestId("entry-run")).toContainText("1 tool refused");
  await expect(page.getByTestId("thread").getByTestId("entry-note")).toContainText("not re-run");
});

test("the compose box sends a message as a local, unverified request", async ({ page }) => {
  (page as Watched).sends = true;
  await page.goto("/#/conversation?role=builder");
  await expect(page.getByTestId("compose-promise")).toContainText("is woken with it");
  await expect(page.getByTestId("compose-promise")).toContainText("never an approval");
  await page.getByTestId("compose-text").fill("Please rebase on main before the review.");
  await page.getByTestId("compose-send").click();
  const sent = page.getByTestId("thread").getByTestId("entry-message").filter({ hasText: "rebase on main" });
  await expect(sent).toContainText("local user, not verified");
  await expect(sent.getByTestId("delivery")).toContainText("queued");
});

test("unread counts clear when a thread is opened", async ({ page }) => {
  await page.goto("/#/conversation?role=builder");
  await expect(page.getByTestId("thread")).toBeVisible();
  const designer = page.getByTestId("role-designer");
  await expect(designer.getByTestId("unread")).toBeVisible();
  await designer.click();
  await expect(page.getByTestId("thread").getByTestId("entry-run")).toBeVisible();
  await expect(designer.getByTestId("unread")).toHaveCount(0);
});

test("the message route refuses a write without the page's cookie, its CSRF token, or from another origin", async ({ page, request, baseURL }) => {
  (page as Watched).refusals = true; // the browser logs each refused fetch below
  await page.goto("/#/conversation?role=builder");
  const body = { role: "builder", text: "x" };
  const noCookie = await request.post("/ui/messages", { data: body, headers: { Origin: baseURL!, "X-CSRF-Token": "x" } });
  expect(noCookie.status()).toBe(403);
  const token = await page.evaluate(async () => (await (await fetch("/api/ui/csrf")).json()).csrf as string);
  const status = (init: RequestInit) => page.evaluate(async (i) => (await fetch("/ui/messages", i)).status, init as Record<string, unknown>);
  const json = { "Content-Type": "application/json" };
  expect(await status({ method: "POST", headers: { ...json }, body: JSON.stringify(body) })).toBe(403); // no CSRF token
  expect(await status({ method: "POST", headers: { ...json, "X-CSRF-Token": "wrong" }, body: JSON.stringify(body) })).toBe(403);
  const otherOrigin = await request.post("/ui/messages", {
    data: body,
    headers: { Origin: "http://example.invalid", "X-CSRF-Token": token, Cookie: (await page.context().cookies()).map((c) => `${c.name}=${c.value}`).join("; ") },
  });
  expect(otherOrigin.status()).toBe(403);
  (page as Watched).writes = []; // the refused attempts above are the point of this test
});

// The demo server's own directory (web/.demo/<pid>), where a test may drop a switch file and must remove it after.
async function demoDir(): Promise<string> {
  const fs = await import("node:fs");
  const path = await import("node:path");
  const demo = path.resolve(process.cwd(), ".demo");
  const dirs = fs.readdirSync(demo).map((d) => path.join(demo, d)).filter((d) => fs.existsSync(path.join(d, "ledger.db")));
  return dirs.sort((a, b) => fs.statSync(b).mtimeMs - fs.statSync(a).mtimeMs)[0];
}

async function withFile(name: string, fn: () => Promise<void>) {
  const fs = await import("node:fs");
  const path = await import("node:path");
  const file = path.join(await demoDir(), name);
  fs.writeFileSync(file, "written by a browser test\n");
  try {
    await fn();
  } finally {
    fs.rmSync(file, { force: true }); // the file this test wrote
  }
}

async function cli(args: string[]) {
  const { execFileSync } = await import("node:child_process");
  const path = await import("node:path");
  const python = process.env.ONLOOP_PYTHON ?? "python";
  execFileSync(python, ["-m", "onloop", ...args, "--ledger", path.join(await demoDir(), "ledger.db")], { stdio: "pipe" });
}

test("with the carrier stopped, the compose box is closed and says how to start it", async ({ page }) => {
  await withFile("carrier.off", async () => {
    await page.goto("/#/conversation?role=builder");
    await expect(page.getByTestId("carrier-state")).toContainText("kill switch");
    await expect(page.getByTestId("compose")).toHaveAttribute("data-open", "0");
    await expect(page.getByTestId("compose-text")).toBeDisabled();
    await expect(page.getByTestId("compose-send")).toBeDisabled();
    await expect(page.getByTestId("compose-off")).toContainText("the carrier is stopped");
    await expect(page.getByTestId("carrier-state").getByTestId("command")).toContainText("onloop carrier start");
    await expect(page.getByTestId("compose-off").getByTestId("command")).toHaveCount(0); // not twice
  });
});

test("with no carrier configured (a default install), the compose box is closed, never a box that fails", async ({ page }) => {
  await withFile("demo-no-carrier", async () => {
    await page.goto("/#/conversation?role=designer");
    await expect(page.getByTestId("carrier-state")).toContainText("not configured");
    await expect(page.getByTestId("acts-on")).toContainText("nothing (no carrier configured)");
    await expect(page.getByTestId("compose")).toHaveAttribute("data-open", "0");
    await expect(page.getByTestId("compose-text")).toBeDisabled();
    await expect(page.getByTestId("compose-send")).toBeDisabled();
    await expect(page.getByTestId("compose-off")).toHaveText("Messages are off: the carrier is not configured (docs/carrier.md)");
    await expect(page.getByTestId("compose-error")).toHaveCount(0);
    await expect(page.getByText("no write route")).toHaveCount(0);
  });
});

test("with the role opted out, the compose box is closed and shows the command that opts it in", async ({ page }) => {
  await cli(["role", "opt-out", "designer", "message"]);
  try {
    await page.goto("/#/conversation?role=designer");
    await expect(page.getByTestId("compose")).toHaveAttribute("data-open", "0");
    await expect(page.getByTestId("compose-send")).toBeDisabled();
    await expect(page.getByTestId("compose-off")).toContainText("designer has not opted in to messages");
    await expect(page.getByTestId("compose-off").getByTestId("command")).toContainText("onloop role opt-in designer message");
  } finally {
    await cli(["role", "opt-in", "designer", "message"]);
  }
  await page.reload();
  await expect(page.getByTestId("compose")).toHaveAttribute("data-open", "1"); // opted in again: open
});

test("with the role opted in and the carrier started, the compose box is open and promises the wake", async ({ page }) => {
  await page.goto("/#/conversation?role=designer");
  await expect(page.getByTestId("compose")).toHaveAttribute("data-open", "1");
  await expect(page.getByTestId("compose-text")).toBeEnabled();
  await expect(page.getByTestId("compose-promise")).toContainText("An idle designer is woken with it");
  await expect(page.getByTestId("compose-off")).toHaveCount(0);
});
