import { fitAffine, applyAffine, median } from "./affine.js";
import { h, sleep } from "./dom.js";

// Normalised viewport positions.
const GRID = [0.1, 0.5, 0.9].flatMap((y) => [0.06, 0.5, 0.94].map((x) => [x, y]));
const VALIDATION = [[0.27, 0.3], [0.73, 0.3], [0.5, 0.55], [0.27, 0.78], [0.73, 0.78]];
const CLICKS_PER_POINT = 3;
const DWELL_MS = 1500;
const SETTLE_MS = 550; // ignore samples while the eyes are still moving to the dot

export const QUALITY = [
  { max: 90, label: "좋음", cls: "good", note: "문단 단위 분석에 충분한 정확도예요." },
  { max: 160, label: "보통", cls: "warn", note: "분석은 가능하지만, 다시 보정하면 더 정확해질 수 있어요." },
  { max: Infinity, label: "낮음", cls: "bad", note: "다시 보정하는 걸 권해요. 조명과 자세를 확인해 주세요." },
];
export const qualityOf = (px) => QUALITY.find((q) => px <= q.max);

const toPx = ([nx, ny]) => [nx * window.innerWidth, ny * window.innerHeight];
const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);

export class Calibration {
  constructor(tracker, root) {
    this.tracker = tracker;
    this.root = root;
    this.panel = root.querySelector("#calibPanel");
    this.status = root.querySelector("#calibStatus");
  }

  /** Runs the full procedure; resolves with the accepted result. */
  run() {
    this.root.hidden = false;
    return new Promise((resolve) => {
      this._resolve = resolve;
      this._intro();
    });
  }

  _close(result) {
    this.root.hidden = true;
    this.tracker.showPreview(false);
    this._resolve(result);
  }

  _setPanel(...children) {
    this.panel.replaceChildren(...children);
    this.panel.hidden = false;
  }

  _setStatus(text) {
    this.status.textContent = text;
    this.status.hidden = !text;
  }

  _intro() {
    this.tracker.showPreview(true);
    this._setStatus("");
    const startBtn = h("button", { class: "btn btn-primary btn-lg", type: "button", disabled: true }, "얼굴 인식 중…");
    this._setPanel(
      h("h2", {}, "시선 보정을 시작할게요"),
      h("p", {}, "오른쪽 아래 카메라 화면에서 얼굴이 초록색 상자 안에 들어오게 해 주세요."),
      h("p", {}, `점이 9개 차례로 나타나요. 점을 바라보면서 ${CLICKS_PER_POINT}번씩 클릭하고, 이어서 클릭 없이 점만 바라보면 돼요.`),
      h("div", { class: "actions" }, startBtn),
    );
    const ready = () => {
      startBtn.disabled = false;
      startBtn.textContent = "보정 시작";
    };
    if (this.tracker.last) ready();
    else {
      const off = this.tracker.onSample(() => { off(); ready(); });
      setTimeout(() => {
        if (startBtn.disabled) {
          ready();
          startBtn.textContent = "그래도 시작";
        }
      }, 12000);
    }
    startBtn.onclick = () => this._procedure();
  }

  async _procedure() {
    this.panel.hidden = true;
    this.tracker.showPreview(false);
    this.tracker.matrix = null;
    this._spreads = [];
    await this.tracker.resetModel?.();

    // 1. Train WebGazer's regression with clicks while looking at each dot.
    for (let i = 0; i < GRID.length; i++) {
      this._setStatus(`1/3 · 점을 바라보며 ${CLICKS_PER_POINT}번 클릭하세요 (${i + 1}/${GRID.length})`);
      await this._clickPoint(toPx(GRID[i]));
    }

    // 2. Fixate the same dots without clicking; fit an affine correction
    //    on top of the regression to remove systematic offset.
    this._setStatus("2/3 · 이제 클릭하지 말고 점만 바라보세요");
    await sleep(900);
    const fitRaw = [];
    const fitTarget = [];
    for (let i = 0; i < GRID.length; i++) {
      this._setStatus(`2/3 · 점만 바라보세요 (${i + 1}/${GRID.length})`);
      const target = toPx(GRID[i]);
      const raw = await this._dwellPoint(target);
      if (raw) { fitRaw.push(raw); fitTarget.push(target); }
    }
    const matrix = fitRaw.length >= 6 ? fitAffine(fitRaw, fitTarget) : null;

    // 3. Measure accuracy on points that were not used for fitting.
    const valRaw = [];
    const valTarget = [];
    for (let i = 0; i < VALIDATION.length; i++) {
      this._setStatus(`3/3 · 정확도 측정 (${i + 1}/${VALIDATION.length})`);
      const target = toPx(VALIDATION[i]);
      const raw = await this._dwellPoint(target);
      if (raw) { valRaw.push(raw); valTarget.push(target); }
    }
    this._setStatus("");

    if (valRaw.length < 3) return this._failed();

    const mean = (arr) => arr.reduce((a, b) => a + b, 0) / arr.length;
    const rawErr = mean(valRaw.map((r, i) => dist(r, valTarget[i])));
    const corrErr = matrix ? mean(valRaw.map((r, i) => dist(applyAffine(matrix, ...r), valTarget[i]))) : Infinity;
    // Keep the correction only when it actually helps on held-out points.
    const useMatrix = corrErr < rawErr;
    this._result({
      matrix: useMatrix ? matrix : null,
      errorPx: Math.round(useMatrix ? corrErr : rawErr),
      rawErrorPx: Math.round(rawErr),
      jitterPx: Math.round(median(this._spreads)),
      validPoints: valRaw.length,
    });
  }

  _dot([x, y], cls = "") {
    const dot = h("button", { class: `calib-dot ${cls}`, type: "button", "aria-label": "보정 점", style: { left: `${x}px`, top: `${y}px` } });
    this.root.append(dot);
    return dot;
  }

  _clickPoint(target) {
    return new Promise((resolve) => {
      const dot = this._dot(target);
      let n = 0;
      dot.style.setProperty("--ring", "1.6");
      dot.onclick = (e) => {
        this.tracker.train(e.clientX, e.clientY);
        n++;
        dot.style.setProperty("--ring", String(1.6 - n * 0.4));
        dot.classList.add("hit");
        setTimeout(() => dot.classList.remove("hit"), 120);
        if (n >= CLICKS_PER_POINT) { dot.remove(); setTimeout(resolve, 200); }
      };
    });
  }

  async _dwellPoint(target) {
    const dot = this._dot(target, "dwell");
    dot.style.setProperty("--dwell", `${DWELL_MS}ms`);
    const xs = [];
    const ys = [];
    const t0 = performance.now();
    const off = this.tracker.onSample((s) => {
      if (s.t - t0 >= SETTLE_MS) { xs.push(s.rawX); ys.push(s.rawY); }
    });
    await sleep(DWELL_MS);
    off();
    dot.remove();
    await sleep(150);
    if (xs.length < 5) return null;
    const mx = median(xs);
    const my = median(ys);
    // Frame-to-frame spread while the eyes rest on one dot: a robust
    // (MAD-based) estimate of the tracker's jitter.
    const mad = (v, m) => 1.4826 * median(v.map((a) => Math.abs(a - m)));
    this._spreads.push((mad(xs, mx) + mad(ys, my)) / 2);
    return [mx, my];
  }

  _failed() {
    this.tracker.showPreview(true);
    this._setPanel(
      h("h2", {}, "시선을 충분히 읽지 못했어요"),
      h("p", {}, "카메라에 얼굴이 잘 보이는지, 조명이 너무 어둡지 않은지 확인해 주세요."),
      h("div", { class: "actions" }, h("button", { class: "btn btn-primary", type: "button", onclick: () => this._intro() }, "다시 보정")),
    );
  }

  _result(res) {
    const q = qualityOf(res.errorPx);
    this.tracker.showPreview(true);
    const accept = h("button", { class: "btn btn-primary btn-lg", type: "button" }, "읽기 시작");
    const retry = h("button", { class: "btn btn-lg", type: "button" }, "다시 보정");
    this._setPanel(
      h("h2", {}, "보정이 끝났어요"),
      h("dl", { class: "quality" },
        h("div", {}, h("dt", {}, "평균 오차"), h("dd", { class: "num" }, `${res.errorPx}px`)),
        h("div", {}, h("dt", {}, "판정"), h("dd", {}, h("span", { class: `chip ${q.cls}` }, q.label))),
      ),
      h("p", {}, q.note),
      res.matrix
        ? h("p", { class: "hint" }, `보정 행렬 적용 전 ${res.rawErrorPx}px → 적용 후 ${res.errorPx}px · 흔들림 ±${res.jitterPx}px (검증 점 ${res.validPoints}개)`)
        : h("p", { class: "hint" }, `보정 행렬이 오차를 줄이지 못해 적용하지 않았어요 (검증 점 ${res.validPoints}개)`),
      h("div", { class: "actions" }, retry, accept),
    );
    retry.onclick = () => this._intro();
    accept.onclick = () => {
      this.tracker.matrix = res.matrix;
      this._close(res);
    };
  }
}
