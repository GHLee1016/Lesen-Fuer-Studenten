import { GazeTracker } from "./tracker.js";
import { Calibration, qualityOf } from "./calibration.js";
import { h, s, $, icon, toast, fmtDuration } from "./dom.js";
import { sizeCanvas, drawHeatmap, drawScanpath } from "./overlay.js";
import { renderArticle, wordLayout } from "./article.js";
import { vocabTable, openVocabDialog } from "./vocab.js";

const ARTICLE_PARAM = new URLSearchParams(location.search).get("article");
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
  doc: null, // { words, sentences } from renderArticle
  lang: "ko",
  lookups: [], // { t, i, word }
  glosses: new Map(), // word index -> lookup response
  marks: new Set(),
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

const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* storage blocked */ } },
};

async function loadArticleList() {
  const sel = $("#articleSelect");
  try {
    const list = await (await fetch("/api/articles")).json();
    const byLevel = new Map();
    for (const a of list) {
      if (!byLevel.has(a.level)) byLevel.set(a.level, []);
      byLevel.get(a.level).push(a);
    }
    sel.replaceChildren(
      ...[...byLevel].map(([lv, items]) =>
        h("optgroup", { label: `Niveau ${lv}` },
          items.map((a) => h("option", { value: a.id }, `${a.title} (${a.words} Wörter)`)),
        ),
      ),
    );
    const want = ARTICLE_PARAM || store.get("lesen.article");
    if (want && list.some((a) => a.id === want)) sel.value = want;
    sel.disabled = false;
    await loadArticle(sel.value);
  } catch {
    $("#articleTitle").textContent = "기사를 불러오지 못했어요";
    $("#articleMeta").textContent = "서버가 실행 중인지 확인해 주세요.";
  }
}

async function loadArticle(id) {
  el.startBtn.disabled = true;
  const res = await fetch(`/api/articles/${encodeURIComponent(id)}`);
  if (!res.ok) throw new Error(res.status);
  st.article = await res.json();
  st.doc = renderArticle(el.article, st.article);
  store.set("lesen.article", id);
  const a = st.article;
  $("#articleTitle").textContent = a.title;
  $("#articleLevel").textContent = a.level;
  $("#frameTitle").textContent = a.title;
  $("#frameMeta").textContent = `Niveau ${a.level} · ${a.words} Wörter`;
  const minutes = Math.max(1, Math.round(a.words / 120));
  $("#articleMeta").textContent = `${a.words}단어 · 약 ${minutes}분 · ${a.quiz.length ? `퀴즈 ${a.quiz.length}문항` : "퀴즈 없음"}`;
  const src = $("#articleSource");
  src.hidden = !a.source;
  if (a.source) { src.href = a.source.url; src.textContent = `출처: ${a.source.name} ↗`; }
  el.startBtn.disabled = false;
}

$("#articleSelect").addEventListener("change", (e) => loadArticle(e.target.value).catch(() => toast("기사를 불러오지 못했어요.")));

async function loadLanguages() {
  const sel = $("#langSelect");
  try {
    const { languages } = await (await fetch("/api/languages")).json();
    sel.replaceChildren(...Object.entries(languages).map(([code, name]) => h("option", { value: code }, name)));
    const saved = store.get("lesen.lang");
    const guess = (navigator.language || "ko").slice(0, 2);
    sel.value = saved && languages[saved] ? saved : languages[guess] && guess !== "de" ? guess : "ko";
  } catch {
    sel.replaceChildren(h("option", { value: "ko" }, "한국어"));
  }
  st.lang = sel.value;
}
$("#langSelect").addEventListener("change", (e) => { st.lang = e.target.value; store.set("lesen.lang", st.lang); });

// ------------------------------------------------------------ start ----

function showError(target, msg) {
  target.textContent = msg;
  target.hidden = !msg;
}

el.userId.value = store.get("lesen.userId") || "";

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
  store.set("lesen.userId", userId);
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
  st.lookups = [];
  st.glosses = new Map();
  el.article.querySelectorAll(".gloss").forEach((n) => n.remove());
  el.article.querySelectorAll(".w.looked").forEach((n) => n.classList.remove("looked"));
  $("#vocabCount").textContent = "0";
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

// ---------------------------------------------------------- lookups ----

el.article.addEventListener("click", (e) => {
  const state = document.body.dataset.state;
  if (state === "read") {
    const w = e.target.closest(".w");
    if (w) lookup(Number(w.dataset.i));
  } else if (state === "results") {
    const sent = e.target.closest(".sent");
    if (sent) toggleMark(Number(sent.dataset.s));
  }
});

async function lookup(i) {
  const word = st.doc.words[i];
  st.lookups.push({ t: Math.round(performance.now() - st.t0), i, word: word.text });
  if (st.glosses.has(i)) return; // already shown
  const gloss = h("span", { class: "gloss loading", "aria-live": "polite" }, "…");
  word.el.append(gloss);
  word.el.classList.add("looked");
  st.glosses.set(i, null);
  try {
    const res = await fetch("/api/lookup", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_id: st.userId, article_id: st.article.id, word: word.text,
        sentence: st.doc.sentences[word.s].text, lang: st.lang,
      }),
    });
    if (!res.ok) throw new Error(res.status);
    const g = await res.json();
    st.glosses.set(i, g);
    gloss.className = "gloss";
    gloss.textContent = g.translation;
    word.el.title = [g.article ? `${g.article} ${g.lemma}` : g.lemma, g.pos, g.dw ? `DW: ${g.dw.definition}` : ""].filter(Boolean).join(" · ");
    $("#vocabCount").textContent = String([...st.glosses.values()].filter(Boolean).length);
  } catch {
    st.glosses.delete(i);
    gloss.className = "gloss err";
    gloss.textContent = "뜻을 못 찾았어요";
    setTimeout(() => { gloss.remove(); word.el.classList.remove("looked"); }, 2500);
  }
}

$("#vocabBtn").addEventListener("click", () => openVocabDialog(st.userId));

function captureLayout() {
  const ar = el.article.getBoundingClientRect();
  const aois = [...el.article.querySelectorAll("[data-aoi]")].map((node) => {
    const r = node.getBoundingClientRect();
    const count = node.querySelectorAll(".w").length;
    return {
      id: node.dataset.aoi,
      x: round1(r.left - ar.left),
      y: round1(r.top - ar.top),
      w: round1(r.width),
      h: round1(r.height),
      words: count,
    };
  });
  const lh = parseFloat(getComputedStyle(el.article.querySelector("p")).lineHeight) || 32;
  return {
    layout: { width: ar.width, height: ar.height, line_height: lh, viewport_w: innerWidth, viewport_h: innerHeight },
    aois,
    words: wordLayout(el.article, st.doc.words),
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
  if (!st.article.quiz.length) { submit({}); return; }
  renderQuiz();
  setState("quiz");
});

// ------------------------------------------------------------- quiz ----

function renderQuiz() {
  el.quizList.replaceChildren(
    ...st.article.quiz.map((q, qi) =>
      h("fieldset", { class: "question" },
        h("h3", {}, h("span", { class: "qn" }, `Q${qi + 1}`), h("span", { lang: st.article.source ? "de" : null }, q.question)),
        q.multiple ? h("p", { class: "hint", style: { margin: "-6px 0 10px 42px" } }, "정답을 모두 고르세요.") : null,
        h("div", { class: "options" },
          q.options.map((opt, oi) =>
            h("label", { class: "option" },
              h("input", { type: q.multiple ? "checkbox" : "radio", name: q.id, value: String(oi) }),
              h("span", { lang: st.article.source ? "de" : null }, opt)),
          ),
        ),
      ),
    ),
  );
  el.quizHint.textContent = "";
}

let confirmEmpty = false;
el.quizForm.addEventListener("change", () => { confirmEmpty = false; el.quizHint.textContent = ""; });

el.quizForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const answers = {};
  for (const q of st.article.quiz) {
    const vals = [...el.quizForm.querySelectorAll(`input[name="${q.id}"]:checked`)].map((i) => Number(i.value));
    if (vals.length) answers[q.id] = vals;
  }
  const empty = st.article.quiz.length - Object.keys(answers).length;
  if (empty && !confirmEmpty) {
    confirmEmpty = true;
    el.quizHint.textContent = `${empty}문항이 비어 있어요. 이대로 제출하려면 한 번 더 눌러 주세요.`;
    return;
  }
  submit(answers);
});

async function submit(answers) {
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
        words: st.layout.words,
        samples: st.samples,
        lookups: st.lookups,
        answers,
      }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    st.result = await res.json();
  } catch (err) {
    el.submitQuiz.disabled = false;
    el.submitQuiz.textContent = "다시 제출";
    if (document.body.dataset.state !== "quiz") { renderQuiz(); setState("quiz"); }
    toast(`결과를 전송하지 못했어요 (${err.message}). 잠시 후 다시 제출해 주세요.`, 4500);
    return;
  }
  setState("results");
  renderResults();
}

// ---------------------------------------------------------- results ----

const FLAG_TEXT = {
  high_calibration_error: "보정 오차가 커서 문단 판정이 부정확할 수 있어요.",
  low_sample_rate: "시선 데이터 수집 빈도가 낮았어요. 다른 탭이나 프로그램을 닫고 다시 해 보세요.",
  gaze_mostly_off_text: "시선이 대부분 본문 밖에서 측정됐어요. 보정을 다시 해 보세요.",
};

const LEVEL = [
  { min: 70, label: "기대 수준 이상", color: "var(--teal)" },
  { min: 40, label: "기대 수준 근접", color: "var(--teal-light)" },
  { min: -1, label: "지원 필요", color: "var(--pink)" },
];
const levelOf = (score) => LEVEL.find((l) => score >= l.min);
const aoiLabel = (id) => (id === "title" ? "제목" : id === "lead" ? "도입부" : `${id.slice(1)}문단`);
const coverageColor = (c) => (c >= 0.75 ? "var(--teal)" : c >= 0.25 ? "var(--teal-light)" : "var(--pink)");

function partRow(label, value, color, shown = `${value}`) {
  return h("div", { class: "row" },
    h("span", {}, label),
    h("div", { class: "bar" }, h("i", { style: { width: `${Math.max(1, Math.min(100, value))}%`, background: color } })),
    h("b", {}, shown),
  );
}

function recommendations(r, wrong) {
  const m = r.metrics;
  const out = [];
  const body = m.paragraphs.filter((p) => p.id !== "title");
  const skipped = body.filter((p) => p.coverage < 0.25).map((p) => aoiLabel(p.id));
  const partial = body.filter((p) => p.coverage >= 0.25 && p.coverage < 0.75).map((p) => aoiLabel(p.id));
  if (skipped.length) out.push(`${skipped.join(", ")}을(를) 거의 읽지 않았어요. 이 부분부터 다시 읽어 보세요.`);
  if (partial.length) out.push(`${partial.join(", ")}은(는) 빠르게 훑고 지나갔어요. 문장 단위로 천천히 읽어 보세요.`);
  if (m.wpm > 250) out.push(`읽기 속도가 분당 ${m.wpm}단어로 빠른 편이에요. 모르는 단어에서 잠시 멈추며 읽어 보세요.`);
  if (m.regression_rate > 0.2) out.push(`되돌아 읽은 비율이 ${Math.round(m.regression_rate * 100)}%로 높았어요. 어려웠던 문장을 표시해 두고 선생님께 물어보세요.`);
  const hard = m.difficulty?.difficult_sentences.length || 0;
  if (hard) out.push(`읽는 데 오래 걸리거나 다시 돌아간 문장이 ${hard}개 있어요. 아래 '어려웠던 구간'에서 확인하고 모르는 단어를 단어장에 모아 보세요.`);
  if (wrong) out.push(`퀴즈에서 ${wrong}문항을 틀렸어요. 아래 퀴즈 리뷰에서 정답과 관련 문단을 확인해 보세요.`);
  if (st.mode === "webcam" && m.on_text_ratio < 0.6) out.push("시선이 본문 밖에서 측정된 시간이 길었어요. 다음에는 보정을 다시 하고 시작해 보세요.");
  if (!out.length) out.push("모든 문단을 충분히 읽고 내용도 잘 이해했어요. 한 단계 높은 지문에 도전해 보세요.");
  return out;
}

function renderResults() {
  const r = st.result;
  const m = r.metrics;
  const regress = m.regressions_within_line + m.regressions_between_paragraphs;
  const flags = r.flags.map((f) => FLAG_TEXT[f]).filter(Boolean);
  if (st.mode === "mouse") flags.unshift("마우스 데모 결과예요. 마우스 위치를 시선으로 간주했어요.");
  const lv = levelOf(r.final_score);
  const correct = r.quiz_feedback.filter((f) => f.correct).length;
  const total = r.quiz_feedback.length;
  const diff = m.difficulty || { difficult_sentences: [], difficult_words: [], penalty: 0 };
  const coverage = Math.round(m.reading_coverage * 100);
  st.marks = new Set(r.marks || []);

  // word-weighted reading profile
  const words = m.paragraphs.reduce((a, p) => a + p.words, 0) || 1;
  const share = (pred) => Math.round((100 * m.paragraphs.filter(pred).reduce((a, p) => a + p.words, 0)) / words);
  const full = share((p) => p.coverage >= 0.75);
  const skip = share((p) => p.coverage < 0.25);
  const part = Math.max(0, 100 - full - skip);

  el.resultsTop.replaceChildren(
    h("div", { class: "report-head" },
      h("div", {}, h("span", { class: "eyebrow" }, "Reading report"), h("h2", {}, st.userId)),
      h("span", { class: "when" }, new Date(st.startedAt).toLocaleString("ko-KR", { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })),
    ),
    h("div", { class: "facts" },
      h("span", {}, h("span", { class: "sq", style: { background: lv.color } }), "읽기 수준 ", h("b", {}, lv.label)),
      total ? h("span", {}, "퀴즈 ", h("b", {}, `${correct}/${total}`), " 정답") : h("span", {}, "퀴즈 ", h("b", {}, "없음")),
      h("span", {}, "읽기 속도 ", h("b", {}, `${m.wpm} WPM`)),
      h("span", {}, "읽기 시간 ", h("b", {}, fmtDuration(m.duration_ms))),
      h("span", {}, st.calib ? ["보정 오차 ", h("b", {}, `${st.calib.errorPx}px`)] : ["측정 ", h("b", {}, "마우스 데모")]),
    ),
    h("div", { class: "score-row" },
      h("div", { class: "big-score" },
        h("b", {}, r.final_score, h("small", {}, "/100")),
        h("span", {}, "최종 점수"),
        h("span", { class: "verdict" }, h("span", { class: "sq", style: { background: lv.color } }), lv.label),
      ),
      h("div", { class: "score-parts" },
        partRow("문단 커버리지", coverage, "var(--teal-light)", `${coverage}`),
        partRow("어려움 감점", diff.penalty * 4, "var(--pink)", `−${diff.penalty}`),
        partRow("시선 점수", r.gaze_score, "var(--teal)"),
        total ? partRow("퀴즈 점수", r.quiz_score, "var(--teal-2)") : null,
        h("p", { class: "formula" },
          `시선 점수 = 문단 커버리지 ${coverage} − 어려움 감점 ${diff.penalty}. `,
          total ? `최종 점수 = 시선 ${r.gaze_score} × 0.6 + 퀴즈 ${r.quiz_score} × 0.4.` : "퀴즈가 없는 지문이라 시선 점수가 최종 점수예요."),
      ),
    ),
    h("div", { class: "profile" },
      h("h3", {}, "단어 기준 읽기 프로필"),
      h("div", { class: "stack" },
        h("i", { style: { width: `${full}%`, background: "var(--teal)" } }),
        h("i", { style: { width: `${part}%`, background: "var(--teal-light)" } }),
        h("i", { style: { width: `${skip}%`, background: "var(--pink)" } }),
      ),
      h("div", { class: "legend-row" },
        h("span", {}, h("span", { class: "sq", style: { background: "var(--teal)" } }), `충분히 읽음 ${full}%`),
        h("span", {}, h("span", { class: "sq", style: { background: "var(--teal-light)" } }), `훑어 읽음 ${part}%`),
        h("span", {}, h("span", { class: "sq", style: { background: "var(--pink)" } }), `건너뜀 ${skip}%`),
      ),
    ),
    hardSection(diff),
    h("div", { class: "two-col" },
      h("div", { class: "next" }, h("h3", {}, "다음에 해 볼 것"), h("ul", {}, recommendations(r, total - correct).map((t) => h("li", {}, t)))),
      h("dl", { class: "metric-list" },
        mrow("고정(fixation)", `${m.fixations}회 · 평균 ${m.mean_fixation_ms}ms`),
        mrow("되돌아 읽기", `${regress}회 · ${Math.round(m.regression_rate * 100)}%`),
        mrow("건너뛴 문단", `${m.skipped_paragraphs.filter((id) => id !== "title").length}개`),
        mrow("수집 빈도", `${m.sample_rate_hz} Hz`),
      ),
    ),
    flags.length ? h("div", { class: "flags" }, flags.map((f) => h("div", { class: "flag" }, f))) : null,
  );

  annotateParagraphs(m.paragraphs);
  applyHardMarks(diff);
  drawLayers();
  renderSessionVocab();
  renderReview(r.quiz_feedback);
}

const REASON = { slow: "다른 문장보다 훨씬 오래 머묾", reread: "읽고 지나간 뒤 다시 돌아옴" };

function hardSection(diff) {
  const auto = new Set(diff.difficult_sentences);
  const list = [...new Set([...auto, ...st.marks])].sort((a, b) => a - b);
  const sents = st.doc.sentences;
  const stats = new Map((diff.sentences || []).map((x) => [x.s, x]));
  return h("div", { class: "hard", id: "hardSection" },
    h("h3", {}, `어려웠던 구간 ${list.length ? `· ${list.length}문장` : ""}`),
    diff.penalty
      ? h("p", { class: "penalty-note" }, `자동으로 찾은 구간 때문에 시선 점수에서 ${diff.penalty}점이 깎였어요. 직접 표시하거나 해제해도 점수는 바뀌지 않아요.`)
      : h("p", { class: "penalty-note" }, "시선으로 찾은 어려운 구간이 없어요. 어려웠던 문장이 있다면 아래 지문에서 문장을 눌러 표시해 보세요."),
    list.length
      ? h("ul", { class: "hard-list" },
          list.map((s) => {
            const x = stats.get(s);
            const why = auto.has(s)
              ? [(x?.reasons || []).map((r) => REASON[r]).join(" · "), x ? `문장 ${x.dwell_ms / 1000}초 · 단어당 ${x.ms_per_word}ms` : ""].filter(Boolean).join(" — ")
              : "직접 표시함";
            return h("li", {},
              h("q", { lang: "de" }, sents[s]?.text || ""),
              h("button", { class: "btn btn-sm btn-ghost", type: "button", onclick: () => toggleMark(s) }, st.marks.has(s) ? "표시 해제" : "표시"),
              h("span", { class: "why" }, why),
            );
          }),
        )
      : null,
    diff.difficult_words.length
      ? h("div", {}, h("p", { class: "hint", style: { margin: "4px 0 8px" } }, "오래 머문 단어 (시선 정확도 때문에 후보로만 봐 주세요)"),
          h("div", { class: "word-cands" }, diff.difficult_words.map((w) => h("span", { lang: "de" }, st.doc.words[w.i]?.text.replace(/[^\p{L}\p{N}-]/gu, "") || ""))))
      : null,
    h("p", { class: "hint", style: { margin: 0 } }, "지문의 문장을 누르면 어려웠던 구간으로 표시하거나 해제할 수 있어요."),
  );
}

function applyHardMarks(diff) {
  const auto = new Set(diff.difficult_sentences);
  el.article.querySelectorAll(".sent").forEach((n) => {
    const s = Number(n.dataset.s);
    n.classList.toggle("auto", auto.has(s));
    n.classList.toggle("marked", st.marks.has(s));
  });
  el.article.querySelectorAll(".w.hard-word").forEach((n) => n.classList.remove("hard-word"));
  for (const w of diff.difficult_words) st.doc.words[w.i]?.el.classList.add("hard-word");
}

let marksTimer;
function toggleMark(s) {
  if (st.marks.has(s)) st.marks.delete(s); else st.marks.add(s);
  const diff = st.result.metrics.difficulty || { difficult_sentences: [], difficult_words: [], penalty: 0 };
  applyHardMarks(diff);
  $("#hardSection").replaceWith(hardSection(diff));
  clearTimeout(marksTimer);
  marksTimer = setTimeout(() => {
    fetch(`/api/sessions/${st.result.session_id}/marks`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sentences: [...st.marks] }),
    }).catch(() => toast("표시를 저장하지 못했어요."));
  }, 400);
}

function renderSessionVocab() {
  const items = [...st.glosses.entries()].filter(([, g]) => g).map(([i, g]) => ({
    ...g, example: st.doc.sentences[st.doc.words[i].s].text,
  }));
  $("#sessionVocab").replaceChildren(
    h("div", { class: "section-head" },
      h("div", {}, h("span", { class: "eyebrow" }, "Wortschatz"), h("h2", {}, "이번에 찾아본 단어")),
      h("button", { class: "btn btn-sm", type: "button", onclick: () => openVocabDialog(st.userId) }, "내 단어장 전체 보기"),
    ),
    items.length
      ? vocabTable(items, { showCount: false })
      : h("div", { class: "vocab-empty" }, "이번에는 찾아본 단어가 없어요. 다음에는 읽다가 모르는 단어를 눌러 보세요."),
  );
}

function mrow(label, value) {
  return h("div", {}, h("dt", {}, label), h("dd", {}, value));
}

function annotateParagraphs(paragraphs) {
  el.article.querySelectorAll(".aoi-badge, .aoi-bar").forEach((n) => n.remove());
  for (const p of paragraphs) {
    const node = el.article.querySelector(`[data-aoi="${CSS.escape(p.id)}"]`);
    if (!node) continue;
    const pct = Math.round(p.coverage * 100);
    const cls = p.coverage >= 0.75 ? "good" : p.coverage >= 0.25 ? "accent" : "bad";
    const label = p.coverage < 0.25 ? `건너뜀 · ${(p.dwell_ms / 1000).toFixed(1)}초` : `읽음 ${pct}% · ${(p.dwell_ms / 1000).toFixed(1)}초`;
    node.append(
      h("span", { class: `aoi-badge chip ${cls}` }, label),
      h("span", { class: "aoi-bar", style: { background: coverageColor(p.coverage) } }),
    );
  }
}

function drawLayers() {
  const w = el.article.offsetWidth;
  const hh = el.article.offsetHeight;
  const ctx = sizeCanvas(el.overlay, w, hh);
  const fx = st.result?.metrics.fixation_list || [];
  if ($("#layerHeat").checked) drawHeatmap(ctx, w, hh, fx);
  if ($("#layerPath").checked) drawScanpath(ctx, fx, "#1b6676");
  const showAoi = $("#layerAoi").checked;
  el.article.querySelectorAll(".aoi-badge, .aoi-bar").forEach((n) => { n.hidden = !showAoi; });
  el.article.classList.toggle("hide-hard", !$("#layerHard").checked);
}

for (const id of ["layerHeat", "layerPath", "layerAoi", "layerHard"]) $(`#${id}`).addEventListener("change", drawLayers);
window.addEventListener("resize", () => { if (document.body.dataset.state === "results") drawLayers(); });

function renderReview(feedback) {
  const correct = feedback.filter((f) => f.correct).length;
  if (!feedback.length) { el.review.replaceChildren(); return; }
  el.review.replaceChildren(
    h("div", { class: "section-head" },
      h("div", {}, h("span", { class: "eyebrow" }, "Quiz review"), h("h2", {}, "퀴즈 리뷰")),
      h("span", { class: `chip ${correct === feedback.length ? "good" : "accent"}` }, `${correct} / ${feedback.length} 정답`)),
    ...st.article.quiz.map((q, qi) => {
      const f = feedback.find((x) => x.id === q.id);
      return h("div", { class: "question" },
        h("h3", {}, h("span", { class: "qn" }, `Q${qi + 1}`), h("span", { style: { flex: 1 }, lang: st.article.source ? "de" : null }, q.question),
          h("span", { class: `chip ${f.correct ? "good" : "bad"}` }, f.correct ? "정답" : f.given.length ? "오답" : "미응답")),
        h("div", { class: "options" },
          q.options.map((opt, oi) =>
            h("div", { class: `option ${f.answer.includes(oi) ? "correct" : f.given.includes(oi) ? "wrong" : ""}`, lang: st.article.source ? "de" : null }, opt),
          ),
        ),
      );
    }),
  );
}

$("#heroStart").addEventListener("click", () => el.userId.focus());
$("#ctaStart").addEventListener("click", () => {
  el.form.scrollIntoView({ behavior: "smooth", block: "center" });
  setTimeout(() => el.userId.focus({ preventScroll: true }), 400);
});

$("#restart").addEventListener("click", () => location.reload());

setState("start");
loadLanguages();
loadArticleList();
