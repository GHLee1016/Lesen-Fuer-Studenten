import { GazeTracker } from "./tracker.js";
import { Calibration, qualityOf } from "./calibration.js";
import { h, s, $, icon, toast, fmtDuration } from "./dom.js";
import { sizeCanvas, drawHeatmap, drawScanpath } from "./overlay.js";

const ARTICLE_ID = new URLSearchParams(location.search).get("article") || "schwarze-katze";
const USER_RE = /^[\w.\-가-힣]{1,40}$/;
const STATES = ["start", "calibrate", "read", "quiz", "results"];

const tracker = new GazeTracker();
const st = {
  article: null,
  userId: "",
  mode: "webcam",
  calib: null,
  samples: [],
  t0: 0,
  startedAt: 0,
  durationMs: 0,
  layout: null,
  result: null,
};

const el = {
  article: $("#article"),
  overlay: $("#overlay"),
  form: $("#startForm"),
  userId: $("#userId"),
  startBtn: $("#startBtn"),
  startError: $("#startError"),
  modeChip: $("#modeChip"),
  timer: $("#timer"),
  gazeDot: $("#gazeDot"),
  showGaze: $("#showGaze"),
  quizForm: $("#quizForm"),
  quizList: $("#quizList"),
  quizHint: $("#quizHint"),
  submitQuiz: $("#submitQuiz"),
  resultsTop: $("#resultsTop"),
  review: $("#review"),
};

// ------------------------------------------------------------ state ----

function setState(name) {
  document.body.dataset.state = name;
  for (const v of document.querySelectorAll("[data-view]")) {
    v.hidden = !v.dataset.view.split(" ").includes(name);
  }
  const idx = STATES.indexOf(name);
  for (const li of document.querySelectorAll("#steps li")) {
    const i = STATES.indexOf(li.dataset.step);
    li.classList.toggle("active", i === idx);
    li.classList.toggle("done", i < idx);
  }
  window.scrollTo({ top: 0 });
}

// ---------------------------------------------------------- article ----

function renderArticle(a) {
  const blocks = [h("h1", { "data-aoi": "title" }, a.title)];
  if (a.lead) blocks.push(h("p", { class: "lead", "data-aoi": "lead" }, a.lead));
  a.paragraphs.forEach((p, i) => blocks.push(h("p", { "data-aoi": `p${i + 1}` }, p)));
  el.article.replaceChildren(...blocks);
}

async function loadArticle() {
  try {
    const res = await fetch(`/api/articles/${encodeURIComponent(ARTICLE_ID)}`);
    if (!res.ok) throw new Error(res.status);
    st.article = await res.json();
    renderArticle(st.article);
    $("#articleTitle").textContent = st.article.title;
    $("#articleLevel").textContent = st.article.level;
    $("#frameTitle").textContent = `${st.article.title} · Niveau ${st.article.level}`;
    $("#frameMeta").textContent = `${st.article.words} Wörter`;
    const minutes = Math.max(1, Math.round(st.article.words / 120));
    $("#articleMeta").textContent = `${st.article.words}단어 · 약 ${minutes}분 · 퀴즈 ${st.article.quiz.length}문항`;
    el.startBtn.disabled = false;
  } catch {
    $("#articleTitle").textContent = "기사를 불러오지 못했어요";
    $("#articleMeta").textContent = "서버가 실행 중인지 확인해 주세요.";
  }
}

// ------------------------------------------------------------ start ----

function showError(target, msg) {
  target.textContent = msg;
  target.hidden = !msg;
}

try { el.userId.value = localStorage.getItem("lesen.userId") || ""; } catch { /* storage blocked */ }

el.form.addEventListener("change", () => {
  $("#webcamTips").hidden = el.form.mode.value !== "webcam";
});

el.form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const userId = el.userId.value.trim();
  if (!USER_RE.test(userId)) {
    showError($("#userIdError"), "이름이나 학번을 확인해 주세요 (최대 40자, 공백 불가).");
    el.userId.focus();
    return;
  }
  showError($("#userIdError"), "");
  showError(el.startError, "");
  try { localStorage.setItem("lesen.userId", userId); } catch { /* storage blocked */ }
  st.userId = userId;
  st.mode = el.form.mode.value;

  el.startBtn.disabled = true;
  el.startBtn.textContent = st.mode === "webcam" ? "카메라 준비 중…" : "시작하는 중…";
  try {
    await tracker.start(st.mode);
  } catch (err) {
    tracker.stop();
    el.startBtn.disabled = false;
    el.startBtn.textContent = "시작하기";
    const denied = err?.name === "NotAllowedError";
    showError(el.startError, denied
      ? "카메라 권한이 거부됐어요. 주소창의 카메라 아이콘에서 허용하거나, 마우스 데모로 체험해 보세요."
      : `카메라를 시작하지 못했어요. ${err?.message || ""} 마우스 데모로 체험할 수 있어요.`);
    return;
  }

  if (st.mode === "webcam") {
    setState("calibrate");
    st.calib = await new Calibration(tracker, $("#calib")).run();
    const q = qualityOf(st.calib.errorPx);
    el.modeChip.className = `chip ${q.cls}`;
    el.modeChip.replaceChildren(h("span", { class: "dot" }), `웹캠 · 오차 ${st.calib.errorPx}px`);
  } else {
    st.calib = null;
    el.modeChip.className = "chip accent";
    el.modeChip.replaceChildren(h("span", { class: "dot" }), "마우스 데모");
  }
  el.modeChip.hidden = false;
  startReading();
});

// ---------------------------------------------------------- reading ----

let stopRecording = null;
let timerId = null;
let dot = null;

function startReading() {
  setState("read");
  st.samples = [];
  st.t0 = performance.now();
  st.startedAt = Date.now();
  el.showGaze.checked = st.mode === "webcam" && localStorageFlag("lesen.showGaze");
  el.gazeDot.hidden = !el.showGaze.checked;

  stopRecording = tracker.onSample((smp) => {
    const t = smp.t - st.t0;
    if (t < 0) return;
    const r = el.article.getBoundingClientRect();
    st.samples.push([Math.round(t), round1(smp.x - r.left), round1(smp.y - r.top)]);
    moveDot(smp.x, smp.y);
  });
  st.progress = 0;
  updateProgress();
  timerId = setInterval(() => { el.timer.textContent = fmtDuration(performance.now() - st.t0); }, 500);
}

function updateProgress() {
  if (document.body.dataset.state !== "read") return;
  const r = el.article.getBoundingClientRect();
  const p = Math.max(0, Math.min(1, (innerHeight * 0.6 - r.top) / r.height));
  st.progress = Math.max(st.progress || 0, p);
  $("#progressBar").style.width = `${Math.round(st.progress * 100)}%`;
  $("#progressText").textContent = `${Math.round(st.progress * 100)}%`;
}
window.addEventListener("scroll", updateProgress, { passive: true });

function round1(v) { return Math.round(v * 10) / 10; }

function localStorageFlag(key) {
  try { return localStorage.getItem(key) === "1"; } catch { return false; }
}

function moveDot(x, y) {
  if (el.gazeDot.hidden) return;
  dot = dot ? [dot[0] * 0.7 + x * 0.3, dot[1] * 0.7 + y * 0.3] : [x, y];
  el.gazeDot.style.transform = `translate(${dot[0]}px, ${dot[1]}px)`;
}

el.showGaze.addEventListener("change", () => {
  el.gazeDot.hidden = !el.showGaze.checked;
  try { localStorage.setItem("lesen.showGaze", el.showGaze.checked ? "1" : "0"); } catch { /* ignore */ }
});

function captureLayout() {
  const ar = el.article.getBoundingClientRect();
  const aois = [...el.article.querySelectorAll("[data-aoi]")].map((node) => {
    const r = node.getBoundingClientRect();
    return {
      id: node.dataset.aoi,
      x: round1(r.left - ar.left),
      y: round1(r.top - ar.top),
      w: round1(r.width),
      h: round1(r.height),
      words: node.textContent.trim().split(/\s+/).filter(Boolean).length,
    };
  });
  const lh = parseFloat(getComputedStyle(el.article.querySelector("p")).lineHeight) || 32;
  return {
    layout: { width: ar.width, height: ar.height, line_height: lh, viewport_w: innerWidth, viewport_h: innerHeight },
    aois,
  };
}

$("#doneReading").addEventListener("click", () => {
  st.durationMs = performance.now() - st.t0;
  stopRecording?.();
  clearInterval(timerId);
  st.layout = captureLayout();
  tracker.stop();
  el.gazeDot.hidden = true;
  if (st.samples.length < 30) toast("시선 데이터가 거의 수집되지 않았어요. 결과가 부정확할 수 있어요.", 4000);
  renderQuiz();
  setState("quiz");
});

// ------------------------------------------------------------- quiz ----

function renderQuiz() {
  el.quizList.replaceChildren(
    ...st.article.quiz.map((q, qi) =>
      h("fieldset", { class: "question" },
        h("h3", {}, h("span", { class: "qn" }, `Q${qi + 1}`), h("span", {}, q.question)),
        h("div", { class: "options" },
          q.options.map((opt, oi) =>
            h("label", { class: "option" }, h("input", { type: "radio", name: q.id, value: String(oi) }), opt),
          ),
        ),
      ),
    ),
  );
  el.quizHint.textContent = "";
}

let confirmEmpty = false;
el.quizForm.addEventListener("change", () => { confirmEmpty = false; el.quizHint.textContent = ""; });

el.quizForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const answers = {};
  for (const q of st.article.quiz) {
    const v = el.quizForm.querySelector(`input[name="${q.id}"]:checked`)?.value;
    if (v != null) answers[q.id] = Number(v);
  }
  const empty = st.article.quiz.length - Object.keys(answers).length;
  if (empty && !confirmEmpty) {
    confirmEmpty = true;
    el.quizHint.textContent = `${empty}문항이 비어 있어요. 이대로 제출하려면 한 번 더 눌러 주세요.`;
    return;
  }

  el.submitQuiz.disabled = true;
  el.submitQuiz.textContent = "분석 중…";
  try {
    const res = await fetch("/api/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_id: st.userId,
        article_id: st.article.id,
        mode: st.mode,
        started_at: st.startedAt,
        duration_ms: st.durationMs,
        calibration_error_px: st.calib?.errorPx ?? null,
        layout: st.layout.layout,
        aois: st.layout.aois,
        samples: st.samples,
        answers,
      }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    st.result = await res.json();
  } catch (err) {
    el.submitQuiz.disabled = false;
    el.submitQuiz.textContent = "다시 제출";
    toast(`결과를 전송하지 못했어요 (${err.message}). 잠시 후 다시 제출해 주세요.`, 4500);
    return;
  }
  setState("results");
  renderResults();
});

// ---------------------------------------------------------- results ----

const FLAG_TEXT = {
  high_calibration_error: "보정 오차가 커서 문단 판정이 부정확할 수 있어요.",
  low_sample_rate: "시선 데이터 수집 빈도가 낮았어요. 다른 탭이나 프로그램을 닫고 다시 해 보세요.",
  gaze_mostly_off_text: "시선이 대부분 본문 밖에서 측정됐어요. 보정을 다시 해 보세요.",
};

function ring(score) {
  const r = 64;
  const c = 2 * Math.PI * r;
  const color = score >= 70 ? "#22c55e" : score >= 40 ? "#fbbf24" : "#f87171";
  return h("div", { class: "ring" },
    s("svg", { width: 152, height: 152, viewBox: "0 0 152 152" },
      s("circle", { cx: 76, cy: 76, r, fill: "none", stroke: "rgba(255,255,255,.1)", "stroke-width": 12 }),
      s("circle", { cx: 76, cy: 76, r, fill: "none", stroke: color, "stroke-width": 12, "stroke-linecap": "round", "stroke-dasharray": `${(c * score) / 100} ${c}` }),
    ),
    h("div", { class: "val" }, h("div", {}, h("b", {}, score), h("span", {}, "최종 점수"))),
  );
}

function verdict(score) {
  if (score >= 70) return ["", "우수", "지문을 꼼꼼히 읽고 내용도 잘 이해했어요."];
  if (score >= 40) return ["warn", "보통", "건너뛴 문단이나 틀린 문항을 다시 확인해 보세요."];
  return ["bad", "노력 필요", "문단마다 충분히 머무르며 천천히 읽어 보세요."];
}

function barRow(label, value, color, shown = `${value}`) {
  return h("div", { class: "row" },
    h("div", {}, h("span", {}, label), h("b", {}, shown)),
    h("div", { class: "bar" }, h("i", { style: { width: `${Math.max(1, Math.min(100, value))}%`, background: color } })),
  );
}

function metric(iconName, label, value, unit) {
  return h("div", { class: "metric" },
    h("span", { class: "tile" }, icon(iconName)),
    h("div", {}, h("dt", {}, label), h("dd", {}, value, unit ? h("small", {}, unit) : null)),
  );
}

function renderResults() {
  const r = st.result;
  const m = r.metrics;
  const regress = m.regressions_within_line + m.regressions_between_paragraphs;
  const flags = r.flags.map((f) => FLAG_TEXT[f]).filter(Boolean);
  if (st.mode === "mouse") flags.unshift("마우스 데모 결과예요. 마우스 위치를 시선으로 간주했어요.");
  const [vcls, vlabel, vnote] = verdict(r.final_score);

  el.resultsTop.replaceChildren(
    h("div", { class: "section-head", style: { marginTop: 0 } },
      h("div", {}, h("span", { class: "eyebrow" }, "Reading report"), h("h2", {}, `${st.userId}님의 읽기 리포트`)),
      h("span", { class: "chip" }, new Date(st.startedAt).toLocaleString("ko-KR", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })),
    ),
    h("div", { class: "report" },
      h("div", { class: "report-score dark" },
        ring(r.final_score),
        h("div", { class: "verdict" }, h("b", { class: vcls }, vlabel), vnote),
      ),
      h("div", { class: "card report-bars" },
        h("h3", {}, "점수 구성"),
        barRow("시선 점수 (문단 커버리지)", r.gaze_score, "var(--green)"),
        barRow("퀴즈 점수", r.quiz_score, "var(--violet)"),
        barRow("본문 응시 비율", Math.round(m.on_text_ratio * 100), "var(--cyan)", `${Math.round(m.on_text_ratio * 100)}%`),
        barRow("읽기 속도", (m.wpm / 300) * 100, "var(--orange)", `${m.wpm} WPM`),
        h("p", { class: "formula" }, "최종 점수 = 시선 ", h("b", {}, r.gaze_score), " × 0.6 + 퀴즈 ", h("b", {}, r.quiz_score), " × 0.4 = ", h("b", {}, r.final_score)),
      ),
    ),
    h("dl", { class: "metrics" },
      metric("i-clock", "읽기 시간", fmtDuration(m.duration_ms)),
      metric("i-eye", "고정(fixation)", m.fixations, `회 · 평균 ${m.mean_fixation_ms}ms`),
      metric("i-rewind", "되돌아 읽기", regress, `회 · ${Math.round(m.regression_rate * 100)}%`),
      metric("i-layers", "건너뛴 문단", m.skipped_paragraphs.length, "개"),
      st.calib ? metric("i-target", "보정 오차", st.calib.errorPx, "px") : metric("i-scan", "측정 방식", "마우스"),
      metric("i-chart", "수집 빈도", m.sample_rate_hz, "Hz"),
    ),
    flags.length ? h("div", { class: "flags" }, flags.map((f) => h("div", { class: "flag" }, f))) : null,
  );

  annotateParagraphs(m.paragraphs);
  drawLayers();
  renderReview(r.quiz_feedback);
}

function annotateParagraphs(paragraphs) {
  el.article.querySelectorAll(".aoi-badge, .aoi-bar").forEach((n) => n.remove());
  for (const p of paragraphs) {
    const node = el.article.querySelector(`[data-aoi="${CSS.escape(p.id)}"]`);
    if (!node) continue;
    const pct = Math.round(p.coverage * 100);
    const cls = p.coverage >= 0.75 ? "good" : p.coverage >= 0.25 ? "warn" : "bad";
    const label = p.coverage < 0.25 ? `건너뜀 · ${(p.dwell_ms / 1000).toFixed(1)}초` : `읽음 ${pct}% · ${(p.dwell_ms / 1000).toFixed(1)}초`;
    node.append(
      h("span", { class: `aoi-badge chip ${cls}` }, label),
      h("span", { class: "aoi-bar", style: { background: `var(--${cls})` } }),
    );
  }
}

function drawLayers() {
  const w = el.article.offsetWidth;
  const hh = el.article.offsetHeight;
  const ctx = sizeCanvas(el.overlay, w, hh);
  const fx = st.result?.metrics.fixation_list || [];
  if ($("#layerHeat").checked) drawHeatmap(ctx, w, hh, fx);
  if ($("#layerPath").checked) drawScanpath(ctx, fx, getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() || "#2f54c9");
  const showAoi = $("#layerAoi").checked;
  el.article.querySelectorAll(".aoi-badge, .aoi-bar").forEach((n) => { n.hidden = !showAoi; });
}

for (const id of ["layerHeat", "layerPath", "layerAoi"]) $(`#${id}`).addEventListener("change", drawLayers);
window.addEventListener("resize", () => { if (document.body.dataset.state === "results") drawLayers(); });

function renderReview(feedback) {
  const correct = feedback.filter((f) => f.correct).length;
  el.review.replaceChildren(
    h("div", { class: "section-head" },
      h("div", {}, h("span", { class: "eyebrow" }, "Quiz review"), h("h2", {}, "퀴즈 리뷰")),
      h("span", { class: `chip ${correct === feedback.length ? "good" : "accent"}` }, `${correct} / ${feedback.length} 정답`)),
    ...st.article.quiz.map((q, qi) => {
      const f = feedback.find((x) => x.id === q.id);
      return h("div", { class: "question" },
        h("h3", {}, h("span", { class: "qn" }, `Q${qi + 1}`), h("span", { style: { flex: 1 } }, q.question),
          h("span", { class: `chip ${f.correct ? "good" : "bad"}` }, f.correct ? "정답" : f.given == null ? "미응답" : "오답")),
        h("div", { class: "options" },
          q.options.map((opt, oi) =>
            h("div", { class: `option ${oi === f.answer ? "correct" : oi === f.given ? "wrong" : ""}` }, opt),
          ),
        ),
      );
    }),
  );
}

$("#ctaStart").addEventListener("click", () => {
  el.form.scrollIntoView({ behavior: "smooth", block: "center" });
  setTimeout(() => el.userId.focus({ preventScroll: true }), 400);
});

$("#restart").addEventListener("click", () => location.reload());

setState("start");
loadArticle();
