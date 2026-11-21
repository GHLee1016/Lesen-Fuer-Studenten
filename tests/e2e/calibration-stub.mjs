// Drives the webcam calibration with a stubbed WebGazer whose "gaze" is the
// mouse position distorted by a known affine error plus noise. Checks that
// the procedure completes and that the fitted correction removes the error.
//   BASE=http://localhost:8000 node tests/e2e/calibration-stub.mjs [outDir]
import { createRequire } from "node:module";
import { mkdirSync } from "node:fs";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");
const BASE = process.env.BASE || "http://localhost:8000";
const OUT = process.argv[2] || "e2e-out";
mkdirSync(OUT, { recursive: true });

const STUB = `
(() => {
  let cb = null, mx = 0, my = 0;
  addEventListener("mousemove", (e) => { mx = e.clientX; my = e.clientY; }, true);
  const noise = () => (Math.random() - 0.5) * 30;
  const wg = {
    saveDataAcrossSessions() { return wg; }, clearData() { return Promise.resolve(); },
    setRegression() { return wg; }, applyKalmanFilter() { return wg; },
    setGazeListener(f) { cb = f; return wg; },
    begin() { setInterval(() => cb && cb({ x: mx * 1.12 + 60 + noise(), y: my * 0.9 - 45 + noise() }), 33); return Promise.resolve(wg); },
    removeMouseEventListeners() { return wg; }, showPredictionPoints() { return wg; }, showFaceOverlay() { return wg; },
    showVideoPreview() { return wg; }, showFaceFeedbackBox() { return wg; },
    recordScreenPosition() { return wg; }, stopVideo() { return wg; }, end() { return wg; },
  };
  window.webgazer = wg;
})();`;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const browser = await chromium.launch();
const errors = [];
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 }, deviceScaleFactor: Number(process.env.SCALE || 1) });
  page.on("pageerror", (e) => errors.push(e.message));
  await page.addInitScript(STUB);
  await page.goto(BASE);
  await page.waitForSelector("#startBtn:not([disabled])");
  await page.fill("#userId", "calib.tester");
  await page.click("#startBtn");

  await page.waitForSelector("#calibPanel button:not([disabled])");
  await page.screenshot({ path: `${OUT}/c1-intro.png` });
  await page.click("#calibPanel button");

  // Phase 1: click each dot three times. Phase 2/3: look (= hover) at each dot.
  let shot = false;
  while (true) {
    const status = await page.$eval("#calibStatus", (e) => (e.hidden ? "" : e.textContent));
    if (!status && (await page.isVisible("#calibPanel"))) break;
    const dot = await page.$(".calib-dot");
    if (!dot) { await sleep(50); continue; }
    const box = await dot.boundingBox();
    if (!box) { await sleep(30); continue; }
    const cx = box.x + box.width / 2, cy = box.y + box.height / 2;
    if (await dot.evaluate((d) => d.classList.contains("dwell"))) {
      await page.mouse.move(cx, cy, { steps: 4 });
      if (!shot) { await page.screenshot({ path: `${OUT}/c2-dwell.png` }); shot = true; }
      await sleep(200);
    } else {
      await page.mouse.click(cx, cy);
      await sleep(60);
    }
  }
  await page.screenshot({ path: `${OUT}/c3-result.png` });
  const text = await page.$eval("#calibPanel", (e) => e.innerText.replace(/\s+/g, " "));
  console.log(text);
  const m = text.match(/적용 전 (\d+)px → 적용 후 (\d+)px/);
  if (!m) throw new Error("correction was not applied");
  const [raw, corr] = [Number(m[1]), Number(m[2])];
  if (!(raw > 50 && corr < 25)) throw new Error(`unexpected errors raw=${raw} corrected=${corr}`);

  await page.click("#calibPanel .btn-primary");
  await page.waitForSelector("#doneReading", { state: "visible" });
  console.log("chip:", await page.textContent("#modeChip"));
} finally {
  await browser.close();
}
if (errors.length) { console.error(errors.join("\n")); process.exit(1); }
console.log("ok");
