/* search-core.js — 站內檢索計分核心（雙 repo 同步檔，2026-07-20）
 *
 * ⚠️ 本檔在 taiwan-geo-db（site/js/search-core.js）與 taiwan-arts-db
 * （assets/js/search-core.js）各放一份，兩份必須 byte-identical。
 * 改這支＝兩邊都要改，改完跑 `node scripts/check-search-core-sync.sh`
 * （或直接跑各自的 `node scripts/test-search.js`，已接好同步檢查）確認一致
 * 再 commit。背景：兩站原本各自獨立寫了一份幾乎相同的計分/排序邏輯，
 * 2026-07-20 同一個 tie-break bug 修了兩遍才拍板收斂成這支共用核心。
 *
 * UMD、純函式、完全不碰 DOM／window／document——Node `require()`、瀏覽器
 * `<script>` 皆可用。各站資料形狀差異（geo 的 kw 是陣列、arts 是字串；
 * arts 另有 type 型別欄位）由各自 adapter（site/js/search.js／
 * assets/js/search.js）在呼叫本檔前正規化，不進本檔。UI 行為（高亮、
 * snippet、渲染、鍵盤、IME）也全部留在各站 adapter，不進本檔。
 *
 * ---- API ----
 *   normalize(s) -> string
 *     NFKC + 小寫 + 空白正規化。
 *   splitQuery(query) -> string[]
 *     查詢字串正規化後依空白切成多詞，空字串過濾掉。
 *   findAllIndexes(haystack, needle) -> number[]
 *     haystack 中 needle 的所有出現起始位置（不重疊往前找）。
 *   rankRecords(records, query, config, limit) -> Array<{record, score, snippetPos, rawBodyHits}>
 *     records：呼叫前已由 adapter 正規化成 { title, sub, kw(string), body, type? } 的物件
 *       （其餘欄位如 id/url 原樣透傳，回傳的 record 就是傳入的同一物件參照）。
 *     query：原始查詢字串（本函式內部會呼叫 splitQuery）。
 *     config：{ weights?: {...}, typeBoosts?: {...} }（見下）。
 *     limit：回傳前幾筆，預設 20。
 *     多詞為 AND 語意——任一詞在 title/kw/sub/body 都沒命中，整筆排除。
 *     排序：主鍵＝score；次鍵（tie-break）＝body 未封頂的原始命中總數，高者
 *       排前；兩鍵都相同則維持 records 傳入序（Array#sort 為 stable sort）。
 *
 * ---- config.weights（未提供的欄位吃預設值，兩站現行數值皆為預設值）----
 *   title       title 命中，預設 100
 *   titlePrefix title 命中且在開頭再加，預設 50
 *   kw          kw 命中，預設 40
 *   sub         sub 命中，預設 20
 *   bodyHit     body 每次出現的單位分，預設 1
 *   bodyCap     body 貢獻上限（每詞），預設 5
 *
 * ---- config.typeBoosts（選配，預設不啟用）----
 *   { [record.type]: number }——record.type 命中即整筆加一次固定分（不分詞、
 *   不隨命中次數變動），用來解決「廣泛詞被特定型別記錄洗版」的排序問題
 *   （目前僅 taiwan-arts-db 啟用；taiwan-geo-db 的 config 不帶這個欄位，
 *   等同關閉，排序行為與收斂前完全一致）。
 *
 * version: 1.0.0（2026-07-20 首版，從 geo-db／arts-db 兩份獨立實作收斂而來）
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.SearchCore = factory();
  }
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var DEFAULT_WEIGHTS = {
    title: 100,
    titlePrefix: 50,
    kw: 40,
    sub: 20,
    bodyHit: 1,
    bodyCap: 5
  };

  // NFKC 正規化 + 小寫 + 空白正規化。中文不分詞，之後一律做子字串比對。
  function normalize(s) {
    if (s === null || s === undefined) return '';
    var t = String(s);
    if (typeof t.normalize === 'function') t = t.normalize('NFKC');
    t = t.toLowerCase();
    t = t.replace(/\s+/g, ' ').trim();
    return t;
  }

  // 查詢字串以空白切成多詞（正規化後切），空字串過濾掉。
  function splitQuery(query) {
    var n = normalize(query);
    if (!n) return [];
    var parts = n.split(' ');
    var out = [];
    for (var i = 0; i < parts.length; i++) {
      if (parts[i]) out.push(parts[i]);
    }
    return out;
  }

  // haystack 中 needle 的所有出現起始位置（子字串，不重疊往前找）。
  function findAllIndexes(haystack, needle) {
    var idxs = [];
    if (!needle) return idxs;
    var start = 0;
    while (true) {
      var i = haystack.indexOf(needle, start);
      if (i === -1) break;
      idxs.push(i);
      start = i + needle.length;
    }
    return idxs;
  }

  function mergeWeights(weights) {
    var w = {};
    var k;
    for (k in DEFAULT_WEIGHTS) {
      if (DEFAULT_WEIGHTS.hasOwnProperty(k)) w[k] = DEFAULT_WEIGHTS[k];
    }
    if (weights) {
      for (k in weights) {
        if (weights.hasOwnProperty(k) && weights[k] !== undefined) w[k] = weights[k];
      }
    }
    return w;
  }

  // 單一 query 對 records 做比對計分排序，回傳前 limit 名（依分數高到低）。
  // records 元素形狀見檔頭說明；回傳的 record 就是傳入元素本身（不複製）。
  function rankRecords(records, query, config, limit) {
    config = config || {};
    var w = mergeWeights(config.weights);
    var typeBoosts = config.typeBoosts || null;
    limit = limit || 20;

    var terms = splitQuery(query);
    if (!terms.length || !records || !records.length) return [];

    var results = [];
    for (var ri = 0; ri < records.length; ri++) {
      var rec = records[ri];
      var titleN = normalize(rec.title);
      var subN = normalize(rec.sub);
      var kwN = normalize(rec.kw);
      var bodyN = normalize(rec.body);

      var totalScore = 0;
      var matchedAll = true;
      var bodyFirstPositions = [];
      var rawBodyHits = 0;   // tie-break 用：body 命中次數加總，不封頂、不計分

      for (var ti = 0; ti < terms.length; ti++) {
        var term = terms[ti];
        var termScore = 0;
        var termHit = false;

        var titleIdx = titleN.indexOf(term);
        if (titleIdx !== -1) {
          termScore += w.title;
          if (titleIdx === 0) termScore += w.titlePrefix;
          termHit = true;
        }

        if (kwN.indexOf(term) !== -1) { termScore += w.kw; termHit = true; }

        if (subN.indexOf(term) !== -1) { termScore += w.sub; termHit = true; }

        var bodyIdxs = findAllIndexes(bodyN, term);
        if (bodyIdxs.length) {
          termScore += Math.min(bodyIdxs.length, w.bodyCap);
          termHit = true;
          bodyFirstPositions.push(bodyIdxs[0]);
          rawBodyHits += bodyIdxs.length;
        }

        if (!termHit) { matchedAll = false; break; }
        totalScore += termScore;
      }

      if (!matchedAll) continue;

      // 型別加權（選配）：整筆加一次固定分，不分詞、不隨命中次數變動。
      if (typeBoosts && rec.type && typeof typeBoosts[rec.type] === 'number') {
        totalScore += typeBoosts[rec.type];
      }

      var snippetPos = -1;
      if (bodyFirstPositions.length) {
        snippetPos = bodyFirstPositions[0];
        for (var pi = 1; pi < bodyFirstPositions.length; pi++) {
          if (bodyFirstPositions[pi] < snippetPos) snippetPos = bodyFirstPositions[pi];
        }
      }

      results.push({ record: rec, score: totalScore, snippetPos: snippetPos, rawBodyHits: rawBodyHits });
    }

    results.sort(function (a, b) {
      if (b.score !== a.score) return b.score - a.score;
      return b.rawBodyHits - a.rawBodyHits;
    });
    return results.slice(0, limit);
  }

  return {
    normalize: normalize,
    splitQuery: splitQuery,
    findAllIndexes: findAllIndexes,
    rankRecords: rankRecords
  };
});
