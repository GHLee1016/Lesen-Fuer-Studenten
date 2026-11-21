import { applyAffine } from "./affine.js";

const WEBGAZER_SRC = "https://cdn.jsdelivr.net/npm/webgazer@3.4.0/dist/webgazer.js";
const MOUSE_HZ = 30;

function loadScript(src) {
  return new Promise((resolve, reject) => {
    if (window.webgazer) return resolve();
    const el = document.createElement("script");
    el.src = src;
    el.onload = resolve;
    el.onerror = () => reject(new Error("WebGazer를 불러오지 못했어요. 네트워크를 확인해 주세요."));
    document.head.append(el);
  });
}

/**
 * Unified gaze source. Emits samples in viewport (client) coordinates:
 *   { x, y }       corrected by the calibration matrix
 *   { rawX, rawY } as reported by the tracker
 * In "mouse" mode the pointer position is sampled at a fixed rate, so the
 * analysis sees the same kind of continuous stream as from a real tracker.
 */
export class GazeTracker {
  constructor() {
    this.mode = null;
    this.matrix = null;
    this.listeners = new Set();
    this.last = null;
    this._mouseTimer = null;
    this._mousePos = null;
    this._onMouse = (e) => { this._mousePos = [e.clientX, e.clientY]; };
  }

  onSample(fn) {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  _emit(rawX, rawY) {
    const [x, y] = applyAffine(this.matrix, rawX, rawY);
    const sample = { x, y, rawX, rawY, t: performance.now() };
    this.last = sample;
    for (const fn of this.listeners) fn(sample);
  }

  async start(mode) {
    this.mode = mode;
    if (mode === "mouse") {
      window.addEventListener("mousemove", this._onMouse, { passive: true });
      this._mouseTimer = setInterval(() => {
        if (this._mousePos) this._emit(...this._mousePos);
      }, 1000 / MOUSE_HZ);
      return;
    }

    if (!navigator.mediaDevices?.getUserMedia) throw new Error("이 브라우저는 카메라를 지원하지 않아요.");
    await loadScript(WEBGAZER_SRC);
    const wg = window.webgazer;
    wg.saveDataAcrossSessions(false);
    await wg.clearData?.();
    wg.setRegression("ridge")
      .applyKalmanFilter(true)
      .setGazeListener((data) => {
        if (data && Number.isFinite(data.x) && Number.isFinite(data.y)) this._emit(data.x, data.y);
      });
    await wg.begin();
    // Training data comes only from calibration clicks, not from ordinary
    // clicks and mouse moves while reading.
    wg.removeMouseEventListeners();
    wg.showPredictionPoints(false).showFaceOverlay(false);
    this.showPreview(true);
  }

  /** Teach the regression model that the user is looking at (x, y) now. */
  train(x, y) {
    if (this.mode === "webcam") window.webgazer.recordScreenPosition(x, y, "click");
  }

  /** Forget previous training data (used when calibration is repeated). */
  async resetModel() {
    if (this.mode === "webcam") await window.webgazer.clearData?.();
  }

  showPreview(on) {
    if (this.mode !== "webcam") return;
    window.webgazer.showVideoPreview(on).showFaceFeedbackBox(on);
  }

  stop() {
    clearInterval(this._mouseTimer);
    window.removeEventListener("mousemove", this._onMouse);
    if (this.mode === "webcam" && window.webgazer) {
      try { window.webgazer.stopVideo(); } catch { /* already stopped */ }
      try { window.webgazer.end(); } catch { /* not started */ }
    }
    this.listeners.clear();
    this.mode = null;
    this.matrix = null;
  }
}
