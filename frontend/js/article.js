/** Renders an article as word spans grouped into sentences.
 *
 * The same tokenizer runs on the student page and in the teacher's replay,
 * so word and sentence indices line up with the recorded layout.
 */
import { h } from "./dom.js";

// Words after which a period does not end a sentence.
const ABBR = new Set(["z.", "B.", "z.B.", "bzw.", "ca.", "Dr.", "Prof.", "Nr.", "usw.", "etc.", "vgl.", "u.a.", "d.h.", "evtl.", "ggf.", "Hr.", "Fr.", "St.", "Mio.", "Mrd."]);
const END_RE = /[.!?…]["“”»«’‘)\]]*$/;
const START_RE = /^["„“»«‘(\[]?[A-ZÄÖÜ0-9]/;
const ORDINAL_RE = /^\d+\.$/;

export function tokenize(text) {
  return text.split(/\s+/).filter(Boolean);
}

/** Split a block's words into sentences (arrays of words). */
export function sentences(words) {
  const out = [];
  let cur = [];
  words.forEach((w, k) => {
    cur.push(w);
    const next = words[k + 1];
    const ends = END_RE.test(w) && !ABBR.has(w) && !ORDINAL_RE.test(w) && (!next || START_RE.test(next));
    if (ends) { out.push(cur); cur = []; }
  });
  if (cur.length) out.push(cur);
  return out;
}

/**
 * Fill `root` with the article. Returns
 *   { words: [{el, text, s}], sentences: [{text, aoi, words: [i...]}] }
 */
export function renderArticle(root, a) {
  const words = [];
  const sents = [];
  const blocks = [["h1", "title", a.title]];
  if (a.lead) blocks.push(["p", "lead", a.lead]);
  a.paragraphs.forEach((p, i) => blocks.push(["p", `p${i + 1}`, p]));

  const nodes = blocks.map(([tag, aoi, text]) => {
    const el = h(tag, { "data-aoi": aoi, class: aoi === "lead" ? "lead" : null });
    for (const sw of sentences(tokenize(text))) {
      const s = sents.length;
      const sent = { text: sw.join(" "), aoi, words: [] };
      sents.push(sent);
      const span = h("span", { class: "sent", "data-s": String(s) });
      sw.forEach((w, k) => {
        const i = words.length;
        const wEl = h("span", { class: "w", "data-i": String(i) }, w);
        words.push({ el: wEl, text: w, s });
        sent.words.push(i);
        span.append(wEl);
        if (k < sw.length - 1) span.append(" ");
      });
      if (el.childNodes.length) el.append(" ");
      el.append(span);
    }
    return el;
  });
  root.replaceChildren(...nodes);
  return { words, sentences: sents };
}

/** Word boxes relative to `root`'s border box: [x, y, w, h, sentence]. */
export function wordLayout(root, words) {
  const r0 = root.getBoundingClientRect();
  const round1 = (v) => Math.round(v * 10) / 10;
  return words.map(({ el, s }) => {
    const r = el.getBoundingClientRect();
    return [round1(r.left - r0.left), round1(r.top - r0.top), round1(r.width), round1(r.height), s];
  });
}
