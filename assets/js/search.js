/* 臺灣人文藝術 — 首頁站內檢索（2026-07-19）
 *
 * client-side 全文檢索：85 頁靜態站＋206 首歌無後端，build 時把人物／領域／
 * 歌曲時代頁／歌曲條目攤平成 search-index.json（290 筆），首頁第一次
 * focus／輸入時才 fetch（lazy-load），瀏覽器內做 NFKC＋lowercase 正規化後
 * 子字串比對＋計分。
 *
 * 檔案分兩段：
 *   1. 純函式模組（ArtsSearch）——不碰 DOM，Node 可直接 require() 測試。
 *   2. 瀏覽器 wiring——只在 `document` 存在時才跑，負責 fetch／debounce／
 *      鍵盤操作／畫面渲染。
 *
 * 不動的東西：hash-router <script>（templates/index.html 抽出）一個字元
 * 不碰；本檔完全獨立，不依賴、也不修改該 script 的任何行為。
 */
(function (root, factory) {
  if (typeof module !== "undefined" && module.exports) {
    module.exports = factory();
  } else {
    root.ArtsSearch = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  /** NFKC＋lowercase＋空白正規化（全形/半形、大小寫統一比對用）。 */
  function normalize(s) {
    return (s || "")
      .normalize("NFKC")
      .toLowerCase()
      .replace(/\s+/g, " ")
      .trim();
  }

  /** 查詢字串依空白切成多詞（AND 比對），已正規化。 */
  function tokenize(query) {
    var n = normalize(query);
    return n ? n.split(" ").filter(Boolean) : [];
  }

  /** needle 在 haystack 中出現的次數（不重疊）。 */
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

  /** 單筆 record 對 tokens 的計分；任一詞完全沒命中（title/sub/kw/body
   * 皆無）→ 回傳 null（AND 語意，整筆不算）。計分：title +100（前綴再
   * +50）、kw +40、sub +20、body 每次出現 +1（每詞上限 +5）。 */
  function scoreRecord(record, tokens) {
    var title = normalize(record.title);
    var sub = normalize(record.sub);
    var kw = normalize(record.kw);
    var body = normalize(record.body);
    var total = 0;

    for (var i = 0; i < tokens.length; i++) {
      var t = tokens[i];
      var inTitle = title.indexOf(t) > -1;
      var inSub = sub.indexOf(t) > -1;
      var inKw = kw.indexOf(t) > -1;
      var bodyHits = countOccurrences(body, t);
      if (!inTitle && !inSub && !inKw && bodyHits === 0) {
        return null;
      }
      var s = 0;
      if (inTitle) {
        s += 100;
        if (title.indexOf(t) === 0) s += 50;
      }
      if (inKw) s += 40;
      if (inSub) s += 20;
      s += Math.min(bodyHits, 5);
      total += s;
    }
    return total;
  }

  /** body 未封頂的原始命中總數（跨全部 tokens 加總）。只當次要排序鍵用
   * （2026-07-20 A-list #5）：scoreRecord() 的計分公式本身不動（body 貢獻
   * 上限維持 +5／詞），這裡另外算一份不封頂版本，只在同分時決定何者在前
   * ——不解決「廣泛詞被歌曲洗版」的排序型別問題，那是另案待拍板的 UX
   * 決策，這裡只是同分 tie-break，不做型別加權。 */
  function rawBodyHitTotal(record, tokens) {
    var body = normalize(record.body);
    var total = 0;
    for (var i = 0; i < tokens.length; i++) {
      total += countOccurrences(body, tokens[i]);
    }
    return total;
  }

  /** 對整批 records 排序取前 limit 筆（預設 20）。查詢為空回傳 []。
   * 排序：score 高者在前；同 score 時，body 未封頂原始命中數高者在前
   * （tie-break，2026-07-20）。 */
  function rankRecords(records, query, limit) {
    limit = limit || 20;
    var tokens = tokenize(query);
    if (!tokens.length) return [];
    var scored = [];
    for (var i = 0; i < records.length; i++) {
      var s = scoreRecord(records[i], tokens);
      if (s !== null && s > 0) {
        scored.push({
          record: records[i],
          score: s,
          rawBody: rawBodyHitTotal(records[i], tokens),
          tokens: tokens,
        });
      }
    }
    scored.sort(function (a, b) {
      if (b.score !== a.score) return b.score - a.score;
      return b.rawBody - a.rawBody;
    });
    return scored.slice(0, limit);
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
    scoreRecord: scoreRecord,
    rawBodyHitTotal: rawBodyHitTotal,
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
