#!/usr/bin/env bash
# scripts/check-search-core-sync.sh — 防止 search-core.js 兩 repo 再度分岔（arts-db 版）。
#
# 背景：assets/js/search-core.js（首頁站內檢索計分核心）刻意在 taiwan-arts-db
# 與 taiwan-geo-db 兩個 repo 各放一份 byte-identical 拷貝（2026-07-20 收斂案，
# 見 docs/DEPLOY.md「search-core 雙 repo 同步規則」）。本機若找得到 sibling
# repo（taiwan-geo-db），就 diff 兩份 search-core.js，不一致就 fail——擋下
# 「只改一邊忘了另一邊」再度發生（同一個 tie-break bug 才剛修兩遍）。
#
# CI（GitHub Actions runner）通常只 checkout 單一 repo、找不到 sibling，
# 這種情況印提示後直接視為通過（exit 0），不擋 CI。
# scripts/test-search.js 會呼叫本腳本，失敗即整個測試失敗。
set -euo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OWN_CORE="$SELF_DIR/assets/js/search-core.js"
SIBLING_REPO="$(cd "$SELF_DIR/.." 2>/dev/null && pwd)/taiwan-geo-db"
SIBLING_CORE="$SIBLING_REPO/site/js/search-core.js"

if [[ ! -f "$OWN_CORE" ]]; then
  echo "✗ 找不到 $OWN_CORE，check-search-core-sync 中止。" >&2
  exit 1
fi

if [[ ! -d "$SIBLING_REPO" || ! -f "$SIBLING_CORE" ]]; then
  echo "⚠ 本機找不到 sibling repo taiwan-geo-db（$SIBLING_REPO）或其 search-core.js，略過同步檢查（CI 環境正常現象）。"
  exit 0
fi

if diff -q "$OWN_CORE" "$SIBLING_CORE" >/dev/null 2>&1; then
  echo "✓ search-core.js 與 taiwan-geo-db 一致。"
  exit 0
else
  echo "✗ search-core.js 與 taiwan-geo-db（$SIBLING_CORE）版本不一致！修改必須同步兩邊：" >&2
  diff "$OWN_CORE" "$SIBLING_CORE" >&2 || true
  exit 1
fi
