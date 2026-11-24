import { h, $, toast } from "./dom.js";

/** Table of vocabulary entries. `onDelete(item)` adds a delete button per row. */
export function vocabTable(items, { onDelete, showCount = true } = {}) {
  return h("table", { class: "vocab-table" },
    h("thead", {}, h("tr", {},
      h("th", {}, "독일어"), h("th", {}, "뜻"), h("th", {}, "예문"),
      showCount ? h("th", {}, "조회") : null, onDelete ? h("th", {}, "") : null,
    )),
    h("tbody", {},
      items.map((v) =>
        h("tr", {},
          h("td", { class: "de", lang: "de" }, v.article ? h("small", {}, v.article) : null, v.lemma),
          h("td", {}, v.translation, v.pos ? h("span", { class: "muted" }, `, ${v.pos}`) : null,
            v.dw ? h("span", { class: "dwdef", lang: "de" }, `DW: ${v.dw.definition}`) : null),
          h("td", { class: "ex", lang: "de" }, v.example || ""),
          showCount ? h("td", { class: "num" }, v.lookups ?? 1) : null,
          onDelete ? h("td", {}, h("button", { class: "btn btn-sm btn-ghost", type: "button", onclick: () => onDelete(v) }, "삭제")) : null,
        ),
      ),
    ),
  );
}

/** Opens the student's full vocabulary list in a dialog. */
export async function openVocabDialog(userId) {
  const dlg = $("#vocabDialog");
  const list = $("#vocabList");
  $("#vocabCsv").href = `/api/vocab.csv?user_id=${encodeURIComponent(userId)}`;
  const load = async () => {
    try {
      const res = await fetch(`/api/vocab?user_id=${encodeURIComponent(userId)}`);
      const { items } = await res.json();
      list.replaceChildren(
        items.length
          ? vocabTable(items, {
              onDelete: async (v) => {
                await fetch(`/api/vocab/${v.id}?user_id=${encodeURIComponent(userId)}`, { method: "DELETE" });
                load();
              },
            })
          : h("div", { class: "vocab-empty" }, "아직 모은 단어가 없어요. 읽는 중에 단어를 눌러 보세요."),
      );
    } catch {
      toast("단어장을 불러오지 못했어요.");
    }
  };
  list.replaceChildren(h("p", { class: "muted" }, "불러오는 중…"));
  dlg.showModal();
  await load();
}

$("#vocabClose")?.addEventListener("click", () => $("#vocabDialog").close());
