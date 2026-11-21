// End-to-end run of the student flow in mouse-demo mode, then the admin
// dashboard. Usage:
//   ADMIN_TOKEN=dev-token uvicorn backend.main:app --port 8000
//   BASE=http://localhost:8000 ADMIN_TOKEN=dev-token node tests/e2e/demo-flow.mjs [outDir]
import { createRequire } from "node:module";
import { mkdirSync } from "node:fs";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const BASE = process.env.BASE || "http://localhost:8000";
const TOKEN = process.env.ADMIN_TOKEN || "dev-token";
const OUT = process.argv[2] || "e2e-out";
const USER = process.env.E2E_USER || "demo.student";
const MS_PER_FIX = Number(process.env.MS_PER_FIX || 220);
mkdirSync(OUT, { recursive: true });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const browser = await chromium.launch();
const errors = [];

try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 860 }, deviceScaleFactor: Number(process.env.SCALE || 1) });
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  // External fonts may be unreachable in CI; only app errors count.
  page.on("console", (m) => m.type() === "error" && !/Failed to load resource/.test(m.text()) && errors.push(`console: ${m.text()}`));

  await page.goto(BASE);
  await page.waitForSelector("#startBtn:not([disabled])");
  await page.screenshot({ path: `${OUT}/1-start.png` });

  await page.fill("#userId", USER);
  await page.selectOption("#langSelect", process.env.E2E_LANG || "ko");
  await page.click('label.mode:has(input[value="mouse"])');
  await page.click("#startBtn");
  await page.waitForSelector("#doneReading", { state: "visible" });

  // Look up a few words (the gloss appears under the word).
  for (const i of [6, 31, 58]) {
    await page.click(`.w[data-i="${i}"]`);
    await page.waitForSelector(`.w[data-i="${i}"] .gloss:not(.loading)`, { timeout: 15000 });
  }
  await page.mouse.move(5, 5);

  // Simulated reading: sweep each line left to right with short stops,
  // skip the third paragraph, linger on one line of p2 (slow sentence)
  // and jump back twice to the lead (re-reading).
  const blocks = await page.$$eval("[data-aoi]", (els) =>
    els.map((e) => {
      const r = e.getBoundingClientRect();
      const lh = parseFloat(getComputedStyle(e).lineHeight);
      return { id: e.dataset.aoi, x: r.left + scrollX, y: r.top + scrollY, w: r.width, h: r.height, lh };
    }),
  );
  for (const b of blocks) {
    if (b.id === "p3") continue;
    const lines = Math.max(1, Math.round(b.h / b.lh));
    for (let ln = 0; ln < lines; ln++) {
      const docY = b.y + b.lh * (ln + 0.5);
      // keep the current line around the middle of the viewport
      await page.evaluate((y) => window.scrollTo(0, Math.max(0, y - 380)), docY);
      const scrollY = await page.evaluate(() => window.scrollY);
      const slow = b.id === "p2" && ln === 2 ? 4 : 1;
      for (let k = 0; k < 5; k++) {
        await page.mouse.move(b.x + 30 + (k * (b.w - 60)) / 4, docY - scrollY, { steps: 3 });
        await sleep(MS_PER_FIX * slow);
      }
      if (b.id === "p1" && ln === 1) {
        // go back to the lead's first line twice
        const lead = blocks.find((x) => x.id === "lead");
        for (let r = 0; r < 2; r++) {
          await page.evaluate((y) => window.scrollTo(0, Math.max(0, y - 380)), lead.y + lead.lh / 2);
          const sy = await page.evaluate(() => window.scrollY);
          for (let k = 0; k < 3; k++) {
            await page.mouse.move(lead.x + 40 + k * 120, lead.y + lead.lh / 2 - sy, { steps: 3 });
            await sleep(MS_PER_FIX);
          }
          await page.evaluate((y) => window.scrollTo(0, Math.max(0, y - 380)), docY);
          const sy2 = await page.evaluate(() => window.scrollY);
          await page.mouse.move(b.x + 60, docY - sy2, { steps: 3 });
          await sleep(MS_PER_FIX);
        }
      }
    }
  }
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: `${OUT}/2-reading.png` });
  await page.click("#doneReading");

  await page.waitForSelector("#quizList input");
  const answers = { q1: 1, q2: 1, q3: 2, q4: 0, q5: 1 }; // q4 wrong on purpose
  for (const [q, v] of Object.entries(answers)) await page.check(`input[name="${q}"][value="${v}"]`);
  const lookedUp = await page.$$eval(".gloss", (g) => g.map((x) => x.textContent));
  console.log("glosses:", lookedUp.join(" | "));
  await page.screenshot({ path: `${OUT}/3-quiz.png`, fullPage: true });
  await page.click("#submitQuiz");
  await page.waitForSelector(".report-head");
  await sleep(300);
  console.log("difficulty:", await page.$eval("#hardSection", (e) => e.innerText.replace(/\s+/g, " ").slice(0, 300)));
  await page.screenshot({ path: `${OUT}/4-results.png`, fullPage: true });
  await page.check("#layerPath");
  await page.uncheck("#layerHeat");
  await page.screenshot({ path: `${OUT}/5-scanpath.png`, fullPage: true });
  const summary = await page.$eval(".report-head", (e) => e.innerText.replace(/\s+/g, " "));
  console.log("results:", summary);

  // Admin dashboard
  const admin = await browser.newPage({ viewport: { width: 1360, height: 900 }, deviceScaleFactor: Number(process.env.SCALE || 1) });
  admin.on("pageerror", (e) => errors.push(`admin pageerror: ${e.message}`));
  await admin.goto(`${BASE}/admin`);
  await admin.fill("#token", TOKEN);
  await admin.click("#loginBtn");
  await admin.waitForSelector(`[data-user="${USER}"]`);
  await sleep(300);
  await admin.screenshot({ path: `${OUT}/6-admin.png`, fullPage: true });
  await admin.click(`[data-user="${USER}"]`);
  await admin.waitForSelector(".drawer .para-bars");
  await sleep(300);
  await admin.screenshot({ path: `${OUT}/7-admin-detail.png` });

  // Gaze replay
  await admin.click("#replayBtn");
  await admin.waitForSelector(".replay canvas");
  await admin.selectOption(".replay-speed", "8");
  await admin.click(".replay-controls .btn-primary");
  await sleep(2500);
  await admin.screenshot({ path: `${OUT}/8-replay.png` });
  await admin.keyboard.press("Escape"); // close replay
  await admin.keyboard.press("Escape"); // close drawer

  // Import a DW lesson through the dashboard (needs network access to DW)
  if (process.env.DW_URL) {
    await admin.fill("#importUrl", process.env.DW_URL);
    await admin.click("#importBtn");
    await admin.waitForFunction(() => document.querySelectorAll("#articleRows tr").length > 1, null, { timeout: 30000 });
    await admin.$eval(".articles", (e) => e.scrollIntoView());
    await sleep(300);
    await admin.screenshot({ path: `${OUT}/9-articles.png` });
  }
} finally {
  await browser.close();
}

if (errors.length) {
  console.error(errors.join("\n"));
  process.exit(1);
}
console.log("ok");
