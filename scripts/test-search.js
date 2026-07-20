#!/usr/bin/env node
// 站內檢索純函式斷言測試（2026-07-20：search-core.js 雙 repo 收斂案＋型別加權）。
// 用法：node scripts/test-search.js
//
// assets/js/search.js（arts-db adapter，計分核心已抽到 assets/js/search-core.js，
// 兩 repo 同步檔）的 ArtsSearch.rankRecords(records, query, limit) 是可 require
// 的純函式，這支跑四類斷言：
//   0. search-core.js 雙 repo 同步檢查（呼叫 check-search-core-sync.sh）。
//   1. 合成資料單元測試——tie-break 邏輯本身（不依賴真實內容，日後內容改版不會誤報）。
//   2. typeBoosts 驗收：廣泛詞「國語」「台語」「客家」不再被歌曲洗版——
//      top3 不得全是歌曲，且最相關的領域/時代/人物頁要進 top3。
//   3. 具體查詢零回歸：明確指名的歌曲/人物查詢，top1 仍是正確答案
//      （typeBoosts 只解決廣泛詞排序問題，不能誤傷精準查詢）。
"use strict";

const assert = require("assert");
const path = require("path");
const fs = require("fs");
const { spawnSync } = require("child_process");

const ArtsSearch = require(path.join(__dirname, "..", "assets", "js", "search.js"));

let passed = 0;
function check(name, fn) {
  fn();
  passed += 1;
  console.log(`  ok - ${name}`);
}

console.log("== 0. search-core.js 雙 repo 同步檢查 ==");
const syncCheck = spawnSync(
  "bash",
  [path.join(__dirname, "check-search-core-sync.sh")],
  { encoding: "utf-8" }
);
if (syncCheck.stdout) process.stdout.write(syncCheck.stdout.replace(/^/gm, "  "));
if (syncCheck.stderr) process.stderr.write(syncCheck.stderr);
assert.strictEqual(syncCheck.status, 0, "search-core.js 雙 repo 同步檢查失敗，中止測試");
passed += 1;

console.log("== 1. 合成資料單元測試（tie-break 邏輯本身，typeBoosts 不干擾同型別 tie-break）==");

// 兩筆同型別（song，typeBoost +0）、同分（都只靠 body 命中且都頂到 +5 分上限），
// 但原始命中次數不同：rec-low 命中 5 次，rec-high 命中 30 次。
// tie-break 應把命中次數多的 rec-high 排前——驗證 typeBoosts 存在時 tie-break 仍生效。
const synthTie = [
  { id: "song:rec-low", url: "pages/rec-low.html", title: "無關標題A", sub: "", kw: "", body: "關鍵字 ".repeat(5) },
  { id: "song:rec-high", url: "pages/rec-high.html", title: "無關標題B", sub: "", kw: "", body: "關鍵字 ".repeat(30) },
];
check("同分同型別時 body 命中次數多者排前（tie-break 生效）", () => {
  const results = ArtsSearch.rankRecords(synthTie, "關鍵字", 10);
  assert.strictEqual(results.length, 2, "應回傳兩筆合成結果");
  assert.strictEqual(results[0].score, results[1].score, "兩筆合成資料分數應相同（都頂到上限、同型別同 boost）");
  assert.strictEqual(results[0].record.id, "song:rec-high", "命中次數較多的 rec-high 應排第一");
  assert.strictEqual(results[1].record.id, "song:rec-low");
});

check("title 命中分數仍高於 body tie-break（既有排序邏輯不變）", () => {
  const withTitle = [
    { id: "song:rec-title", url: "pages/rec-title.html", title: "關鍵字專頁", sub: "", kw: "", body: "" },
    { id: "song:rec-high", url: "pages/rec-high.html", title: "無關標題", sub: "", kw: "", body: "關鍵字 ".repeat(30) },
  ];
  const results = ArtsSearch.rankRecords(withTitle, "關鍵字", 10);
  assert.strictEqual(results[0].record.id, "song:rec-title", "title 命中（+100）應仍排在純 body 命中之前");
});

check("typeBoosts 生效：同分不同型別時，領域頁靠 boost 排到歌曲之前", () => {
  // 兩筆都只靠 kw 命中（+40），無 title/sub/body：field 有 +40 boost、song 沒有，
  // boost 前同分，boost 後 field 應排前。
  const mixed = [
    { id: "song:rec-song", url: "pages/rec-song.html", title: "無關標題", sub: "", kw: "測試詞", body: "" },
    { id: "field:rec-field", url: "pages/rec-field.html", title: "無關標題", sub: "", kw: "測試詞", body: "" },
  ];
  const results = ArtsSearch.rankRecords(mixed, "測試詞", 10);
  assert.strictEqual(results[0].record.id, "field:rec-field", "field 型別靠 +40 boost 應排在同分的 song 之前");
  assert.strictEqual(results[0].score - results[1].score, 40, "兩筆 boost 前同分，差距應正好等於 field 的 +40 boost");
});

console.log("== 2. typeBoosts 驗收：廣泛詞不再被歌曲洗版（真實資料 _build/search-index.json）==");

const indexPath = path.join(__dirname, "..", "_build", "search-index.json");
if (!fs.existsSync(indexPath)) {
  console.error(
    `✗ 找不到 ${indexPath}——本測試需要先跑一次 \`python3 scripts/build_pages.py\` 產生 _build/search-index.json。`
  );
  process.exit(1);
}
const records = JSON.parse(fs.readFileSync(indexPath, "utf-8"));
assert.ok(Array.isArray(records) && records.length > 0, "search-index.json 應含非空 records 陣列");

function typeOf(id) {
  const i = String(id).indexOf(":");
  return i === -1 ? "" : id.slice(0, i);
}

const broadCases = [
  { q: "國語", mustHaveTypeInTop3: ["field", "era"] },
  { q: "台語", mustHaveTypeInTop3: ["field", "era", "person"] },
  { q: "客家", mustHaveTypeInTop3: ["field", "era", "person"] },
];
for (const c of broadCases) {
  check(`廣泛詞「${c.q}」：top3 不全是歌曲，且領域/時代/人物頁進 top3`, () => {
    const ranked = ArtsSearch.rankRecords(records, c.q, 10);
    assert.ok(ranked.length >= 3, `「${c.q}」應至少有 3 筆結果`);
    const top3Types = ranked.slice(0, 3).map((r) => typeOf(r.record.id));
    assert.ok(
      top3Types.some((t) => t !== "song"),
      `「${c.q}」top3 型別為 ${JSON.stringify(top3Types)}，不得全是歌曲`
    );
    assert.ok(
      top3Types.some((t) => c.mustHaveTypeInTop3.includes(t)),
      `「${c.q}」top3 型別為 ${JSON.stringify(top3Types)}，應含領域/時代/人物頁`
    );
  });
}

console.log("== 3. 具體查詢零回歸：typeBoosts 不誤傷精準查詢 ==");

const exactCases = [
  { q: "雨夜花", wantId: "song:u-ia-hoe", label: "歌曲〈雨夜花〉" },
  { q: "望春風", wantId: "song:bang-chhun-hong", label: "歌曲〈望春風〉" },
  { q: "洪一峰", wantId: "person:hung-i-feng", label: "人物洪一峰" },
  { q: "江文也", wantId: "person:chiang-wen-yeh", label: "人物江文也" },
  { q: "陳澄波", wantId: "person:chen-cheng-po", label: "人物陳澄波" },
];
for (const c of exactCases) {
  check(`「${c.q}」：${c.label} 仍排第一`, () => {
    const ranked = ArtsSearch.rankRecords(records, c.q, 5);
    assert.ok(ranked.length > 0, `「${c.q}」應有搜尋結果`);
    assert.strictEqual(
      ranked[0].record.id,
      c.wantId,
      `第一名應為 ${c.wantId}，實得 ${ranked[0].record.id}`
    );
  });
}

console.log(`\n全部通過（${passed} 項斷言區塊）。`);
