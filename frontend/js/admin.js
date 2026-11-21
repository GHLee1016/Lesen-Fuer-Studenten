import { h, $, icon, toast, fmtDuration } from "./dom.js";

const TOKEN_KEY = "lesen.adminToken";
const LEVELS = ["high", "medium", "low"];
const AOI_LABEL = (id) => (id === "title" ? "제목" : id === "lead" ? "리드" : `${id.slice(1)}문단`);
const FLAG_TEXT = {
  high_calibration_error: "보정 오차가 커서 문단 판정이 부정확할 수 있어요.",
  low_sample_rate: "시선 데이터 수집 빈도가 낮았어요.",
  gaze_mostly_off_text: "시선이 대부분 본문 밖에서 측정됐어요.",
};

let token = "";
let overview = null;
let ws = null;
let pingTimer = null;
let reconnectTimer = null;
let openUser = null;

// ------------------------------------------------------------- auth ----

function storeToken(t) {
  try { t ? sessionStorage.setItem(TOKEN_KEY, t) : sessionStorage.removeItem(TOKEN_KEY); } catch { /* blocked */ }
}

async function api(path, opts = {}) {
  const res = await fetch(path, { ...opts, headers: { ...(opts.headers || {}), Authorization: `Bearer ${token}` } });
  if (res.status === 401) {
    logout("토큰이 올바르지 않아요.");
    throw new Error("unauthorized");
  }
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res;
}

async function login(t) {
  token = t;
  try {
    const res = await fetch("/api/admin/overview", { headers: { Authorization: `Bearer ${t}` } });
    if (res.status === 401) throw new Error("토큰이 올바르지 않아요.");
    if (!res.ok) throw new Error(`서버 오류 (HTTP ${res.status})`);
    overview = await res.json();
  } catch (err) {
    token = "";
    storeToken("");
    showLoginError(err.message === "Failed to fetch" ? "서버에 연결할 수 없어요." : err.message);
    return;
  }
  storeToken(t);
  $("#loginView").hidden = true;
  $("#dashView").hidden = false;
  render();
  connectWs();
}

function logout(msg) {
  token = "";
  storeToken("");
  clearInterval(pingTimer);
  clearTimeout(reconnectTimer);
  if (ws) { ws.onclose = null; ws.close(); ws = null; }
  closeDrawer();
  $("#dashView").hidden = true;
  $("#loginView").hidden = false;
  $("#token").value = "";
  showLoginError(msg || "");
}

function showLoginError(msg) {
  const e = $("#loginError");
  e.textContent = msg;
  e.hidden = !msg;
}

$("#loginForm").addEventListener("submit", (e) => {
  e.preventDefault();
  const t = $("#token").value.trim();
  if (t) login(t);
});
$("#logout").addEventListener("click", () => logout());

// -------------------------------------------------------- live data ----

function setLive(state) {
  const chip = $("#liveChip");
  const map = { on: ["good", "실시간"], connecting: ["", "연결 중"], off: ["warn", "재연결 중"] };
  const [cls, text] = map[state];
  chip.className = `chip ${cls}`;
  chip.replaceChildren(h("span", { class: "dot" }), text);
}

function connectWs() {
  clearTimeout(reconnectTimer);
  clearInterval(pingTimer);
  setLive("connecting");
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/api/admin/ws`);
  ws.onopen = () => {
    ws.send(JSON.stringify({ type: "auth", token }));
    pingTimer = setInterval(() => ws?.readyState === 1 && ws.send("ping"), 20000);
  };
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "overview") {
      overview = msg.data;
      setLive("on");
      render();
      if (openUser) refreshDrawer();
    }
  };
  ws.onclose = (ev) => {
    clearInterval(pingTimer);
    if (ev.code === 4401) return logout("토큰이 올바르지 않아요.");
    setLive("off");
    reconnectTimer = setTimeout(connectWs, 2500);
  };
}

// ------------------------------------------------------------ render ----

const rtf = new Intl.RelativeTimeFormat("ko", { numeric: "auto" });
function ago(ms) {
  const diff = (ms - Date.now()) / 1000;
  const units = [["day", 86400], ["hour", 3600], ["minute", 60]];
  for (const [u, s] of units) if (Math.abs(diff) >= s) return rtf.format(Math.round(diff / s), u);
  return "방금";
}

const pct = (v) => `${Math.round(v * 100)}%`;
const warnIcon = () => h("span", { class: "warn-icon", title: "데이터 품질 경고" }, icon("i-alert"));
const initial = (id) => [...id][0]?.toUpperCase() || "?";
const scoreColor = (v) => (v >= overview.levels.high ? "var(--green)" : v < overview.levels.low ? "#ef4444" : "#f59e0b");

function render() {
  if (!overview) return;
  const { levels, groups } = overview;
  $("#hi").value = levels.high;
  $("#lo").value = levels.low;

  const all = LEVELS.flatMap((lv) => groups[lv]);
  const avg = (f) => (all.length ? all.reduce((a, u) => a + f(u), 0) / all.length : 0);
  const flagged = all.filter((u) => u.flags.length).length;
  $("#kpis").replaceChildren(
    kpi("i-users", "학생 수", all.length, "명"),
    kpi("i-award", "평균 최종 점수", all.length ? Math.round(avg((u) => u.final_score)) : "–", all.length ? "점" : ""),
    kpi("i-layers", "평균 문단 커버리지", all.length ? Math.round(avg((u) => u.reading_coverage) * 100) : "–", all.length ? "%" : ""),
    kpi("i-alert", "품질 경고", flagged, "명", flagged > 0),
  );

  for (const lv of LEVELS) {
    const sec = document.querySelector(`.group[data-level="${lv}"]`);
    sec.querySelector("[data-count]").textContent = `${groups[lv].length}명`;
    const list = sec.querySelector(".list");
    list.replaceChildren(
      ...(groups[lv].length
        ? groups[lv].map((u) =>
            h("button", { class: "student", type: "button", dataset: { user: u.user_id }, onclick: () => openDrawer(u.user_id) },
              h("span", { class: "avatar" }, initial(u.user_id)),
              h("div", { class: "name" }, h("span", {}, u.user_id), u.flags.length ? warnIcon() : null),
              h("div", { class: "score", style: { color: scoreColor(u.final_score) } }, u.final_score),
              h("div", { class: "meta" },
                h("div", { class: "bar" }, h("i", { style: { width: `${u.final_score}%`, background: scoreColor(u.final_score) } })),
                h("span", {}, `시선 ${u.gaze_score} · 퀴즈 ${u.quiz_score} · ${ago(u.created_at)}`)),
            ),
          )
        : [h("div", { class: "empty" }, "아직 이 그룹에 학생이 없어요")]),
    );
  }
}

function kpi(iconName, label, value, unit, alert = false) {
  return h("div", { class: `kpi ${alert ? "alert" : ""}` },
    h("span", { class: "tile" }, icon(iconName)),
    h("div", {}, h("dt", {}, label), h("dd", {}, value, unit ? h("small", {}, unit) : null)),
  );
}

// ----------------------------------------------------------- levels ----

$("#levelsForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const high = Number($("#hi").value);
  const low = Number($("#lo").value);
  if (!(low < high)) return toast("하위 기준은 상위 기준보다 작아야 해요.");
  try {
    await api("/api/admin/levels", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ high, low }) });
    toast("기준을 적용했어요.");
  } catch (err) {
    if (err.message !== "unauthorized") toast("기준을 저장하지 못했어요.");
  }
});

// ----------------------------------------------------------- export ----

async function download(path, filename) {
  try {
    const blob = await (await api(path)).blob();
    const url = URL.createObjectURL(blob);
    const a = h("a", { href: url, download: filename });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (err) {
    if (err.message !== "unauthorized") toast("내보내기에 실패했어요.");
  }
}
$("#exportCsv").addEventListener("click", () => download("/api/admin/export.csv", "sessions.csv"));
$("#exportJson").addEventListener("click", () => download("/api/admin/export.json", "sessions.json"));

// ----------------------------------------------------------- drawer ----

let drawerData = null;
let selected = 0;

async function openDrawer(userId) {
  openUser = userId;
  selected = 0;
  $("#scrim").hidden = false;
  $("#drawer").hidden = false;
  $("#drawer").replaceChildren(
    h("div", { class: "drawer-head dark" }, h("span", { class: "avatar" }, initial(userId)), h("h2", {}, userId)),
    h("div", { class: "drawer-body" }, h("p", { class: "muted" }, "불러오는 중…")),
  );
  await refreshDrawer();
}

async function refreshDrawer() {
  try {
    drawerData = await (await api(`/api/admin/users/${encodeURIComponent(openUser)}`)).json();
  } catch (err) {
    if (err.message === "HTTP 404") closeDrawer();
    return;
  }
  selected = Math.min(selected, drawerData.sessions.length - 1);
  renderDrawer();
}

function closeDrawer() {
  openUser = null;
  $("#scrim").hidden = true;
  $("#drawer").hidden = true;
}
$("#scrim").addEventListener("click", closeDrawer);
document.addEventListener("keydown", (e) => e.key === "Escape" && closeDrawer());

function renderDrawer() {
  const { user_id, sessions } = drawerData;
  const sess = sessions[selected];
  const m = sess.metrics;
  const fmtDate = (ms) => new Date(ms).toLocaleString("ko-KR", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });

  $("#drawer").replaceChildren(
    h("div", { class: "drawer-head dark" },
      h("span", { class: "avatar" }, initial(user_id)),
      h("div", {}, h("h2", {}, user_id), h("p", {}, `세션 ${sessions.length}개 · 최근 ${fmtDate(sessions[0].created_at)}`)),
      h("button", { class: "btn btn-sm btn-glass", type: "button", onclick: closeDrawer, "aria-label": "닫기" }, icon("i-x")),
    ),
    h("div", { class: "drawer-body" },
      h("section", {},
        h("h3", {}, "세션 기록"),
        h("div", { class: "sessions", role: "tablist" },
          sessions.map((s, i) =>
            h("button", { class: "session-tab", type: "button", role: "tab", "aria-selected": String(i === selected), onclick: () => { selected = i; renderDrawer(); } },
              h("b", {}, s.final_score), h("span", {}, fmtDate(s.created_at)),
            ),
          ),
        ),
      ),
      h("section", {},
        h("dl", { class: "scoreline" },
          h("div", {}, h("dt", {}, "최종 점수"), h("dd", {}, sess.final_score)),
          h("div", {}, h("dt", {}, "시선 ×0.6"), h("dd", {}, sess.gaze_score)),
          h("div", {}, h("dt", {}, "퀴즈 ×0.4"), h("dd", {}, sess.quiz_score)),
        ),
      ),
      sess.flags.length || sess.mode === "mouse"
        ? h("section", { class: "flags" },
            sess.mode === "mouse" ? h("div", { class: "flag" }, "마우스 데모 세션이에요.") : null,
            sess.flags.map((f) => h("div", { class: "flag" }, FLAG_TEXT[f] || f)),
          )
        : null,
      h("section", {},
        h("h3", {}, "문단별 읽음 정도"),
        h("div", { class: "panel para-bars" },
          m.paragraphs.map((p) => {
            const color = p.coverage >= 0.75 ? "var(--green)" : p.coverage >= 0.25 ? "#f59e0b" : "#ef4444";
            return h("div", { class: "para" },
              h("span", {}, AOI_LABEL(p.id)),
              h("div", { class: "bar" }, h("i", { style: { width: `${Math.max(2, p.coverage * 100)}%`, background: color } })),
              h("span", { class: "v" }, `${pct(p.coverage)} · ${(p.dwell_ms / 1000).toFixed(1)}초`),
            );
          }),
        ),
      ),
      h("section", {},
        h("h3", {}, "읽기 지표"),
        h("dl", { class: "panel mini-metrics" },
          mm("읽기 시간", fmtDuration(m.duration_ms)),
          mm("읽기 속도", `${m.wpm} WPM`),
          mm("문단 커버리지", pct(m.reading_coverage)),
          mm("본문 응시 비율", pct(m.on_text_ratio)),
          mm("고정 횟수", `${m.fixations}회 · ${m.mean_fixation_ms}ms`),
          mm("되돌아 읽기", `${m.regressions_within_line + m.regressions_between_paragraphs}회 · ${pct(m.regression_rate)}`),
          mm("보정 오차", sess.calib_error_px != null ? `${Math.round(sess.calib_error_px)}px` : "–"),
          mm("수집 빈도", `${m.sample_rate_hz} Hz`),
        ),
      ),
      h("div", { class: "danger-zone" },
        h("span", { class: "hint" }, "이 학생의 모든 세션과 시선 데이터를 삭제해요."),
        h("button", { class: "btn btn-sm btn-danger", type: "button", onclick: () => deleteUser(user_id) }, "학생 데이터 삭제"),
      ),
    ),
  );
}

function mm(label, value) {
  return h("div", {}, h("dt", {}, label), h("dd", {}, value));
}

async function deleteUser(userId) {
  if (!confirm(`${userId} 학생의 데이터를 모두 삭제할까요? 되돌릴 수 없어요.`)) return;
  try {
    await api(`/api/admin/users/${encodeURIComponent(userId)}`, { method: "DELETE" });
    closeDrawer();
    toast("삭제했어요.");
  } catch (err) {
    if (err.message !== "unauthorized") toast("삭제하지 못했어요.");
  }
}

// ------------------------------------------------------------- boot ----

let saved = "";
try { saved = sessionStorage.getItem(TOKEN_KEY) || ""; } catch { /* blocked */ }
if (saved) login(saved);
