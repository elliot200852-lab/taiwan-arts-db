/* 臺灣人文藝術 — 首頁站內檢索（2026-07-19；2026-07-20 收斂為共用核心＋型別加權）
 *
 * client-side 全文檢索：85 頁靜態站＋206 首歌無後端，build 時把人物／領域／
 * 歌曲時代頁／歌曲條目攤平成 search-index.json（290 筆），首頁第一次
 * focus／輸入時才 fetch（lazy-load），瀏覽器內做 NFKC＋lowercase 正規化後
 * 子字串比對＋計分。
 *
 * 計分／排序核心已抽到 search-core.js（taiwan-geo-db／taiwan-arts-db 雙 repo
 * 同步檔，規則見該檔檔頭與 docs/DEPLOY.md「search-core 雙 repo 同步規則」）。
 * 本檔是 arts-db 專屬 adapter，只做兩件事：
 *   1. 把 record 轉成 core 期待的形狀——kw 本站已是字串，直接透傳；另外從
 *      record.id 的 `person:` / `field:` / `era:` / `song:` 前綴推導 type，
 *      供 typeBoosts 用（build_pages.py 產生 id 時就用這個慣例分四類，
 *      詳見該檔 build_search_index()，此處不重新規範）。
 *   2. 帶入本站的 typeBoosts：領域頁／時代頁 +40、人物頁 +25、歌曲 +0——
 *      解決廣泛詞（如「國語」「台語」「客家」）被大量歌曲條目洗版、真正
 *      該排前面的領域/人物頁卻沉下去的排序問題（2026-07-20 拍板）。
 *
 * 檔案分兩段：
 *   1. 純函式模組（ArtsSearch）——不碰 DOM，Node 可直接 require() 測試。
 *   2. 瀏覽器 wiring——只在 `document` 存在時才跑，負責 fetch／debounce／
 *      鍵盤操作／畫面渲染。UI 行為（高亮、snippet、渲染、鍵盤、IME）維持
 *      2026-07-19 原樣，本次收斂不動。
 *
 * 不動的東西：hash-router <script>（templates/index.html 抽出）一個字元
 * 不碰；本檔完全獨立，不依賴、也不修改該 script 的任何行為。
 *
 * 瀏覽器需先載入 assets/js/search-core.js（見 scripts/build_pages.py 產生
 * 的 <script> 順序，search-core.js 排在 search.js 之前，兩者皆 defer，
 * 執行序不受影響）。
 */
(function (root, factory) {
  if (typeof module !== "undefined" && module.exports) {
    module.exports = factory();
  } else {
    root.ArtsSearch = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var SearchCore = (typeof module !== "undefined" && module.exports)
    ? require("./search-core.js")
    : (typeof self !== "undefined" ? self.SearchCore : this.SearchCore);

  var normalize = SearchCore.normalize;
  var tokenize = SearchCore.splitQuery;

  // arts-db 計分權重與收斂前完全一致；typeBoosts 是本站專屬啟用項——
  // 領域／時代頁 +40、人物頁 +25、歌曲 +0（2026-07-20 拍板，數值見
  // scripts/test-search.js 的驗收斷言）。
  var ARTS_CONFIG = {
    weights: { title: 100, titlePrefix: 50, kw: 40, sub: 20, bodyHit: 1, bodyCap: 5 },
    typeBoosts: { field: 40, era: 40, person: 25, song: 0 }
  };

  /** 從 record.id 的 `person:`/`field:`/`era:`/`song:` 前綴推導型別，供
   * typeBoosts 查表用（build_pages.py 產生 id 時的既有慣例，非新規範）。 */
  function recordType(record) {
    var id = record && record.id;
    if (!id) return "";
    var i = String(id).indexOf(":");
    return i === -1 ? "" : id.slice(0, i);
  }

  /** needle 在 haystack 中出現的次數（不重疊）；保留原名供既有呼叫端/測試沿用。 */
  function countOccurrences(haystack, needle) {
    if (!needle) return 0;
    var count = 0;
    var pos = 0;
    while (true) {
      var idx = haystack.indexOf(needle, pos);
      if (idx === -1) break;
      count += 1;
      pos = idx + needle.length;
    }
    return count;
  }

  /** HTML escape（顯示用；<mark> 由呼叫端另外包）。 */
  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  /** 對整批 records 排序取前 limit 筆（預設 20）。查詢為空回傳 []。
   * 計分／多詞 AND／排序（含 tie-break、typeBoosts）規則全部在
   * search-core.js；本函式只做 arts 的資料形狀轉換：kw 本站已是字串直接
   * 透傳，另外從 id 前綴推導 type 供 typeBoosts 查表。回傳的 record 是
   * 保留原欄位（url/title/sub/body）的淺拷貝物件，只多了 type 欄位，
   * UI 端存取的欄位不受影響。 */
  function rankRecords(records, query, limit) {
    if (!records || !records.length) return [];
    var canon = new Array(records.length);
    for (var i = 0; i < records.length; i++) {
      var rec = records[i];
      canon[i] = {
        id: rec.id,
        url: rec.url,
        title: rec.title,
        sub: rec.sub,
        body: rec.body,
        kw: rec.kw || "",
        type: recordType(rec)
      };
    }
    return SearchCore.rankRecords(canon, query, ARTS_CONFIG, limit || 20);
  }

  /** 把 normalizedText 裡所有 tokens 命中的區間，對應到 originalText 同一
   * 段落並包成 <mark>（先跳脫非命中片段的 HTML）。normalize() 只做大小寫
   * 與空白正規化、不改變字串長度，故 originalText／normalizedText 索引
   * 可直接對應（中文字元 NFKC 幾乎不變動，這是已知的簡化假設）。 */
  function markText(originalText, normalizedText, tokens) {
    var ranges = [];
    for (var i = 0; i < tokens.length; i++) {
      var t = tokens[i];
      if (!t) continue;
      var pos = 0;
      while (true) {
        var idx = normalizedText.indexOf(t, pos);
        if (idx === -1) break;
        ranges.push([idx, idx + t.length]);
        pos = idx + t.length;
      }
    }
    if (!ranges.length) return escapeHtml(originalText);
    ranges.sort(function (a, b) { return a[0] - b[0]; });
    var merged = [ranges[0].slice()];
    for (var j = 1; j < ranges.length; j++) {
      var last = merged[merged.length - 1];
      if (ranges[j][0] <= last[1]) {
        last[1] = Math.max(last[1], ranges[j][1]);
      } else {
        merged.push(ranges[j].slice());
      }
    }
    var out = "";
    var cursor = 0;
    for (var k = 0; k < merged.length; k++) {
      var r = merged[k];
      out += escapeHtml(originalText.slice(cursor, r[0]));
      out += "<mark>" + escapeHtml(originalText.slice(r[0], r[1])) + "</mark>";
      cursor = r[1];
    }
    out += escapeHtml(originalText.slice(cursor));
    return out;
  }

  /** body 首個命中前後約 radius 字（預設 30）的 snippet，命中處包 <mark>；
   * 查無命中（理論上不會發生，因為 body 命中才會有分數貢獻，但保底）回傳
   * body 開頭一小段。 */
  function buildSnippet(record, tokens, radius) {
    radius = radius || 30;
    var body = record.body || "";
    var normBody = normalize(body);
    var bestIdx = -1;
    for (var i = 0; i < tokens.length; i++) {
      var idx = normBody.indexOf(tokens[i]);
      if (idx !== -1 && (bestIdx === -1 || idx < bestIdx)) {
        bestIdx = idx;
      }
    }
    if (bestIdx === -1) {
      var head = body.slice(0, radius * 2);
      return escapeHtml(head) + (body.length > radius * 2 ? "…" : "");
    }
    var start = Math.max(0, bestIdx - radius);
    var end = Math.min(body.length, bestIdx + radius);
    var prefix = start > 0 ? "…" : "";
    var suffix = end < body.length ? "…" : "";
    var slice = body.slice(start, end);
    var normSlice = normalize(slice);
    return prefix + markText(slice, normSlice, tokens) + suffix;
  }

  return {
    normalize: normalize,
    tokenize: tokenize,
    countOccurrences: countOccurrences,
    escapeHtml: escapeHtml,
    rankRecords: rankRecords,
    markText: markText,
    buildSnippet: buildSnippet,
  };
});

/* ---------- 瀏覽器 wiring（只在有 document 時跑） ---------- */
if (typeof document !== "undefined") {
  (function () {
    var ArtsSearch = (typeof module !== "undefined" && module.exports)
      ? module.exports
      : window.ArtsSearch;

    var root = document.querySelector(".arts-search");
    if (!root) return;

    var input = root.querySelector(".arts-search-input");
    var status = root.querySelector(".arts-search-status");
    var resultsEl = root.querySelector(".arts-search-results");
    if (!input || !resultsEl) return;

    var indexData = null; // null=未載入, []/[...]=已載入
    var loading = false;
    var isComposing = false;
    var debounceTimer = null;
    var activeIndex = -1;
    var currentItems = []; // 目前畫面上的 <li> 對應的 record

    function setStatus(text) {
      if (status) status.textContent = text || "";
    }

    function clearResults() {
      resultsEl.innerHTML = "";
      resultsEl.hidden = true;
      currentItems = [];
      activeIndex = -1;
      input.setAttribute("aria-expanded", "false");
    }

    function ensureIndexLoaded(cb) {
      if (indexData) { cb(indexData); return; }
      if (loading) return;
      loading = true;
      setStatus("載入檢索資料中…");
      fetch("search-index.json", { credentials: "omit" })
        .then(function (r) {
          if (!r.ok) throw new Error("HTTP " + r.status);
          return r.json();
        })
        .then(function (data) {
          indexData = data;
          loading = false;
          setStatus("");
          cb(indexData);
        })
        .catch(function () {
          loading = false;
          setStatus("檢索資料載入失敗，請稍後再試。");
        });
    }

    function render(query, tokens, ranked) {
      if (!ranked.length) {
        resultsEl.innerHTML = '<li class="arts-search-empty">找不到符合的內容</li>';
        resultsEl.hidden = false;
        currentItems = [];
        activeIndex = -1;
        setStatus("找不到符合的內容");
        input.setAttribute("aria-expanded", "true");
        return;
      }
      var html = "";
      currentItems = [];
      for (var i = 0; i < ranked.length; i++) {
        var record = ranked[i].record;
        var normTitle = ArtsSearch.normalize(record.title);
        var titleMarked = ArtsSearch.markText(record.title, normTitle, tokens);
        var snippet = ArtsSearch.buildSnippet(record, tokens);
        html +=
          '<li class="arts-search-result" role="option" id="arts-search-opt-' + i + '" data-idx="' + i + '">' +
          '<a href="' + ArtsSearch.escapeHtml(record.url) + '">' +
          '<span class="asr-title">' + titleMarked + "</span>" +
          '<span class="asr-sub">' + ArtsSearch.escapeHtml(record.sub || "") + "</span>" +
          '<span class="asr-snippet">' + snippet + "</span>" +
          "</a></li>";
        currentItems.push(record);
      }
      resultsEl.innerHTML = html;
      resultsEl.hidden = false;
      activeIndex = -1;
      input.setAttribute("aria-expanded", "true");
      setStatus("共找到 " + ranked.length + " 筆結果");
    }

    function runSearch() {
      var query = input.value;
      if (!query.trim()) {
        clearResults();
        setStatus("");
        return;
      }
      ensureIndexLoaded(function (data) {
        var tokens = ArtsSearch.tokenize(query);
        var ranked = ArtsSearch.rankRecords(data, query, 20);
        render(query, tokens, ranked);
      });
    }

    function scheduleSearch() {
      if (isComposing) return;
      window.clearTimeout(debounceTimer);
      debounceTimer = window.setTimeout(runSearch, 120);
    }

    function setActive(idx) {
      var items = resultsEl.querySelectorAll(".arts-search-result");
      items.forEach(function (li, i) {
        li.classList.toggle("active", i === idx);
      });
      activeIndex = idx;
      if (idx >= 0 && items[idx]) {
        input.setAttribute("aria-activedescendant", items[idx].id);
      } else {
        input.removeAttribute("aria-activedescendant");
      }
    }

    input.addEventListener("focus", function () { ensureIndexLoaded(function () {}); });
    input.addEventListener("input", scheduleSearch);
    input.addEventListener("compositionstart", function () { isComposing = true; });
    input.addEventListener("compositionend", function () {
      isComposing = false;
      scheduleSearch();
    });

    input.addEventListener("keydown", function (e) {
      var items = resultsEl.querySelectorAll(".arts-search-result");
      if (e.key === "ArrowDown") {
        if (!items.length) return;
        e.preventDefault();
        setActive(Math.min(activeIndex + 1, items.length - 1));
      } else if (e.key === "ArrowUp") {
        if (!items.length) return;
        e.preventDefault();
        setActive(Math.max(activeIndex - 1, 0));
      } else if (e.key === "Enter") {
        if (activeIndex > -1 && currentItems[activeIndex]) {
          e.preventDefault();
          window.location.href = currentItems[activeIndex].url;
        }
      } else if (e.key === "Escape") {
        clearResults();
        input.blur();
      }
    });

    document.addEventListener("click", function (e) {
      if (!root.contains(e.target)) clearResults();
    });
  })();
}
