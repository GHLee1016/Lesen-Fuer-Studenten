/** Teacher-side gaze replay.
 *
 * The article is rendered again at the width the student read it at, the
 * recorded gaze samples and fixations are drawn on top over time, and word
 * lookups / difficult sentences are marked on a timeline.
 */
import { h, $, fmtDuration } from "./dom.js";
import { renderArticle } from "./article.js";
import { sizeCanvas } from "./overlay.js";

const TRAIL_MS = 4000;
const SPEEDS = [1, 2, 4, 8];

export async function openReplay({ sessionId, token, userId }) {
  const auth = { headers: { Authorization: `Bearer ${token}` } };
  const sess = await (await fetch(`/api/admin/sessions/${sessionId}`, auth)).json();
  const artRes = await fetch(`/api/articles/${encodeURIComponent(sess.article_id)}`);
  if (!artRes.ok) throw new Error("이 세션의 지문이 삭제되어 재생할 수 없어요.");
  const article = await artRes.json();
  new Replay(sess, article, userId).mount();
}

class Replay {
  constructor(sess, article, userId) {
    this.s = sess;
    this.a = article;
    this.userId = userId;
    this.samples = sess.samples; // [t, x, y]
    this.fix = sess.metrics.fixation_list || [];
    this.duration = sess.duration_ms;
    this.t = 0;
    this.speed = 2;
    this.playing = false;
    this.lookups = sess.lookups || [];
    this.diff = sess.metrics.difficulty || { difficult_sentences: [], sentences: [] };
    this.marks = new Set(sess.marks || []);
    this.words = sess.layout.words || [];
  }

  mount() {
    const L = this.s.layout;
    this.articleEl = h("article", { class: "article", lang: "de" });
    this.canvas = h("canvas", { class: "overlay" });
    this.stage = h("div", { class: "replay-stage", style: { width: `${L.width}px` } }, h("div", { class: "article-wrap" }, this.articleEl, this.canvas));
    // The stage is scaled with a transform; the sizer gives it its scaled
    // footprint so the viewport scrolls the right distance.
    this.sizer = h("div", { class: "replay-sizer" }, this.stage);
    this.viewport = h("div", { class: "replay-viewport" }, this.sizer);
    this.doc = renderArticle(this.articleEl, this.a);

    this.playBtn = h("button", { class: "btn btn-primary btn-sm", type: "button", onclick: () => this.toggle() }, "재생");
    this.scrub = h("input", { type: "range", min: "0", max: String(Math.round(this.duration)), step: "10", value: "0", "aria-label": "재생 위치" });
    this.scrub.addEventListener("input", () => { this.t = Number(this.scrub.value); this.draw(); });
    this.timeEl = h("span", { class: "num replay-time" }, "00:00");
    const speedSel = h("select", { class: "input replay-speed", "aria-label": "재생 속도" }, SPEEDS.map((v) => h("option", { value: String(v), selected: v === this.speed }, `${v}×`)));
    speedSel.addEventListener("change", () => { this.speed = Number(speedSel.value); });

    const markers = h("div", { class: "replay-markers" },
      this.lookups.map((l) => h("span", { class: "mk lookup", style: { left: `${(100 * l.t) / this.duration}%` }, title: `${fmtDuration(l.t)} 단어 찾음: ${l.word}` })),
    );
    this.log = h("ol", { class: "replay-log" });

    this.root = h("div", { class: "replay", role: "dialog", "aria-modal": "true", "aria-label": "시선 재생" },
      h("header", { class: "replay-head" },
        h("div", {}, h("h2", {}, this.userId),
          h("p", { class: "muted" }, h("span", { lang: "de" }, this.a.title), ` · ${new Date(this.s.created_at).toLocaleString("ko-KR")} · ${this.s.mode === "mouse" ? "마우스 데모" : `보정 오차 ${Math.round(this.s.calib_error_px ?? 0)}px`}`)),
        h("button", { class: "btn btn-sm btn-ghost", type: "button", onclick: () => this.close() }, "닫기"),
      ),
      h("div", { class: "replay-body" },
        this.viewport,
        h("aside", { class: "replay-side" },
          h("h3", {}, "표시"),
          h("ul", { class: "replay-legend" },
            h("li", {}, h("span", { class: "sq", style: { background: "var(--pink)" } }), "현재 시선"),
            h("li", {}, h("span", { class: "sq", style: { background: "var(--teal)" } }), "최근 4초의 고정"),
            h("li", {}, h("span", { class: "lg-wavy" }), "어려운 문장 (자동)"),
            h("li", {}, h("span", { class: "lg-mark" }), "학생이 표시한 문장"),
          ),
          h("h3", {}, "이벤트"),
          this.log,
        ),
      ),
      h("footer", { class: "replay-controls" },
        this.playBtn,
        h("div", { class: "replay-track" }, markers, this.scrub),
        this.timeEl, h("span", { class: "muted num" }, `/ ${fmtDuration(this.duration)}`),
        speedSel,
      ),
    );
    document.body.append(this.root);
    document.body.style.overflow = "hidden";
    this.onKey = (e) => { if (e.key === "Escape") this.close(); if (e.key === " " && e.target === document.body) { e.preventDefault(); this.toggle(); } };
    document.addEventListener("keydown", this.onKey);
    this.onResize = () => this.fit();

    window.addEventListener("resize", this.onResize);
    // fonts must be ready before measuring line breaks
    (document.fonts?.ready || Promise.resolve()).then(() => {
      this.decorate();
      this.fit();
      this.checkAlignment();
      this.draw();
    });
  }

  decorate() {
    const auto = new Set(this.diff.difficult_sentences || []);
    this.articleEl.querySelectorAll(".sent").forEach((n) => {
      const s = Number(n.dataset.s);
      n.classList.toggle("auto", auto.has(s));
      n.classList.toggle("marked", this.marks.has(s));
    });
    for (const l of this.lookups) this.doc.words[l.i]?.el.classList.add("looked-later");
  }

  fit() {
    const L = this.s.layout;
    const avail = this.viewport.clientWidth - 32;
    this.scale = Math.min(1, avail / L.width);
    this.stage.style.transform = `scale(${this.scale})`;
    this.sizer.style.width = `${L.width * this.scale}px`;
    this.sizer.style.height = `${this.articleEl.offsetHeight * this.scale}px`;
    this.ctx = sizeCanvas(this.canvas, this.articleEl.offsetWidth, this.articleEl.offsetHeight);
    this.draw();
  }

  /** Warn when the text wraps differently from the student's screen. */
  checkAlignment() {
    if (!this.words.length) return;
    const r0 = this.articleEl.getBoundingClientRect();
    let off = 0;
    const n = Math.min(this.words.length, this.doc.words.length, 80);
    for (let i = 0; i < n; i++) {
      const r = this.doc.words[i].el.getBoundingClientRect();
      off += Math.abs((r.left - r0.left) / this.scale - this.words[i][0]) + Math.abs((r.top - r0.top) / this.scale - this.words[i][1]);
    }
    if (n && off / n > 12) {
      this.viewport.prepend(h("p", { class: "flag" }, "학생 화면과 글꼴이나 줄바꿈이 달라 시선 위치가 조금 어긋나 보일 수 있어요."));
    }
  }

  toggle() {
    if (this.playing) { this.playing = false; this.playBtn.textContent = "재생"; return; }
    if (this.t >= this.duration) this.t = 0;
    this.playing = true;
    this.playBtn.textContent = "일시정지";
    let last = performance.now();
    const tick = (now) => {
      if (!this.playing) return;
      this.t = Math.min(this.duration, this.t + (now - last) * this.speed);
      last = now;
      this.scrub.value = String(Math.round(this.t));
      this.draw();
      if (this.t >= this.duration) { this.playing = false; this.playBtn.textContent = "다시 재생"; return; }
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }

  sampleAt(t) {
    // binary search for the last sample at or before t
    let lo = 0, hi = this.samples.length - 1, best = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (this.samples[mid][0] <= t) { best = mid; lo = mid + 1; } else hi = mid - 1;
    }
    return best >= 0 && t - this.samples[best][0] < 300 ? this.samples[best] : null;
  }

  draw() {
    if (!this.ctx) return;
    const t = this.t;
    const ctx = this.ctx;
    ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    // fixation trail
    const recent = this.fix.filter((f) => f.t <= t && f.t + f.d >= t - TRAIL_MS);
    ctx.save();
    ctx.strokeStyle = "rgba(31, 94, 255, .45)";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    recent.forEach((f, k) => (k ? ctx.lineTo(f.x, f.y) : ctx.moveTo(f.x, f.y)));
    ctx.stroke();
    for (const f of recent) {
      const age = Math.max(0, t - (f.t + f.d));
      ctx.globalAlpha = Math.max(0.1, 0.75 * (1 - age / TRAIL_MS));
      ctx.fillStyle = "#1f5eff";
      ctx.beginPath();
      ctx.arc(f.x, f.y, Math.min(26, 5 + Math.sqrt(Math.min(f.d, t - f.t)) / 2.2), 0, Math.PI * 2);
      ctx.fill();
    }
    // current gaze
    const s = this.sampleAt(t);
    if (s) {
      ctx.globalAlpha = 1;
      ctx.fillStyle = "rgba(240, 96, 77, .25)";
      ctx.strokeStyle = "#f0604d";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(s[1], s[2], 12, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
    }
    ctx.restore();

    // lookups shown so far
    const shown = this.lookups.filter((l) => l.t <= t);
    this.doc.words.forEach((w, i) => w.el.classList.toggle("looked", shown.some((l) => l.i === i)));
    this.timeEl.textContent = fmtDuration(t);
    this.renderLog(shown);
    if (s && this.playing) this.follow(s[2]);
  }

  renderLog(shown) {
    if (this._logCount === shown.length) return;
    this._logCount = shown.length;
    this.log.replaceChildren(
      ...(shown.length
        ? shown.map((l) => h("li", {}, h("span", { class: "num muted" }, fmtDuration(l.t)), " 단어 찾음 ", h("b", { lang: "de" }, l.word)))
        : [h("li", { class: "muted" }, "아직 이벤트가 없어요.")]),
    );
  }

  /** Keep the current gaze in view while playing. */
  follow(y) {
    const v = this.viewport;
    const py = y * this.scale;
    if (py < v.scrollTop + 80 || py > v.scrollTop + v.clientHeight - 120) {
      v.scrollTo({ top: Math.max(0, py - v.clientHeight / 2), behavior: "smooth" });
    }
  }

  close() {
    this.playing = false;
    document.removeEventListener("keydown", this.onKey);
    window.removeEventListener("resize", this.onResize);
    document.body.style.overflow = "";
    this.root.remove();
  }
}
