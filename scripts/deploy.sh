#!/usr/bin/env bash
# scripts/deploy.sh — 一鍵部署鏈：build → 換版 Drive → 觸發 CI → 驗 live
#
# 為什麼要有這支：本站內容 HTML 成品 SSOT 在 Google Drive、repo 只留腳本
# （~/.claude/CLAUDE.md「網路部署架構鐵則」）；日常更新內容要串起四個各自獨立的
# 步驟才算真正上架，過去全靠手動一步步下指令。本腳本把它們串成一條：
#
#   1. python3 scripts/build_pages.py
#        content/*.md → _build/index.html ＋ _build/pages/*.html（鏡射 Drive html 夾結構）
#   2. 換版 Drive：比對 _build/ 與 Drive 現版（md5Checksum），只換有變動的檔；
#        Drive 沒有的新檔用 gws ... create --json（帶 metadata）建立，
#        **不可用 --params 建檔**（那是給 update 用的 query 參數，用在 create 上
#        只會生出沒有 name 的 Untitled 檔）。
#   3. 觸發 .github/workflows/deploy-pages.yml：
#        - 若本地 main 領先 origin → 換版 Drive 不會自動觸發 CI，需要 push；
#          本腳本只提醒、不代為 push（push 前應由人／呼叫端自行 commit＋確認）。
#        - 若本地已與 origin 同步（純 Drive 內容更新、沒有新 commit）→
#          直接手動 `gh workflow run` 觸發，並 `gh run watch` 等結果。
#   4. python3 scripts/verify_live.py
#        CI 綠不代表內容真的在 live 站上（Drive-pull 架構，見腳本檔頭說明）——
#        逐頁逐圖打 live 站才是唯一的驗收閘。
#
# Drive folder ID 出處：drive-manifest.yaml（html 夾＝files.site、img 夾＝files.site/img）；
# pages 子夾 ID 未寫進 manifest（pull_content.py 靠遞迴自動找到），本腳本在 STEP 2
# 用 gws 列 html 夾內容動態找出 pages 子夾，避免硬編碼 ID 過期漂移。
#
# 執行需求：gws（已登入）、gh（已 gh auth login）、jq、python3（含 requirements.txt）。
# 換版幾十頁檔案要跑一陣子，呼叫端（含 agent harness）記得給長 timeout（> 2 分鐘）。
set -euo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> 1/4 build_pages.py：content/*.md → _build/"
python3 scripts/build_pages.py

echo
echo "==> 2/4 換版 Drive：比對 _build/ 與 Drive 現版，只換有變動／新增的檔"

HTML_FOLDER_ID=$(python3 -c "import yaml; print(yaml.safe_load(open('drive-manifest.yaml'))['files']['site'])")
if [[ -z "$HTML_FOLDER_ID" || "$HTML_FOLDER_ID" == FILL_ME* ]]; then
  echo "✗ drive-manifest.yaml 的 files.site 還沒填實際 folder ID，中止。" >&2
  exit 1
fi

local_md5() {
  if command -v md5 >/dev/null 2>&1; then
    md5 -q "$1"
  else
    md5sum "$1" | awk '{print $1}'
  fi
}

# 列 html 夾內容，動態找出 pages 子夾 ID（不硬編碼，避免夾被重建後 ID 漂移）
html_children=$(gws drive files list --params "{\"q\":\"'${HTML_FOLDER_ID}' in parents and trashed = false\",\"fields\":\"files(id,name,mimeType,md5Checksum)\",\"pageSize\":100}")
PAGES_FOLDER_ID=$(echo "$html_children" | jq -r '[.files[] | select(.name=="pages" and .mimeType=="application/vnd.google-apps.folder")][0].id // empty')
if [[ -z "$PAGES_FOLDER_ID" ]]; then
  echo "✗ 在 html 夾（$HTML_FOLDER_ID）底下找不到 pages 子夾，中止。" >&2
  exit 1
fi
pages_children=$(gws drive files list --params "{\"q\":\"'${PAGES_FOLDER_ID}' in parents and trashed = false\",\"fields\":\"files(id,name,mimeType,md5Checksum)\",\"pageSize\":200}")

created=0
updated=0
unchanged=0

# 換版或新建單一檔案：$1=本機路徑 $2=該檔所屬 Drive 夾的 files.list JSON $3=該夾 folder ID
sync_one() {
  local path="$1" drive_json="$2" folder_id="$3"
  local name entry drive_md5 file_id my_md5
  name=$(basename "$path")
  my_md5=$(local_md5 "$path")
  entry=$(echo "$drive_json" | jq -c --arg n "$name" '[.files[] | select(.name==$n)][0] // empty')

  if [[ -z "$entry" ]]; then
    echo "  + 新增 $name"
    gws drive files create --json "{\"name\":\"${name}\",\"parents\":[\"${folder_id}\"]}" --upload "$path" >/dev/null
    created=$((created + 1))
    return
  fi

  drive_md5=$(echo "$entry" | jq -r '.md5Checksum // empty')
  if [[ "$my_md5" == "$drive_md5" ]]; then
    unchanged=$((unchanged + 1))
    return
  fi

  file_id=$(echo "$entry" | jq -r '.id')
  echo "  ↻ 換版 $name"
  gws drive files update --params "{\"fileId\":\"${file_id}\"}" --upload "$path" >/dev/null
  updated=$((updated + 1))
}

sync_one "_build/index.html" "$html_children" "$HTML_FOLDER_ID"
for f in _build/pages/*.html; do
  sync_one "$f" "$pages_children" "$PAGES_FOLDER_ID"
done

echo "  Drive 換版完成：新增 ${created}、換版 ${updated}、無變動 ${unchanged}"

echo
echo "==> 3/4 觸發 CI（.github/workflows/deploy-pages.yml）"

LOCAL_SHA=$(git rev-parse HEAD)
git fetch origin main --quiet || true
REMOTE_SHA=$(git rev-parse origin/main 2>/dev/null || echo "")

if [[ "$LOCAL_SHA" != "$REMOTE_SHA" ]]; then
  echo "  本地 main（${LOCAL_SHA}）領先 origin/main（${REMOTE_SHA:-無})。"
  echo "  請先 commit＋push："
  echo "    git push origin main"
  echo "  push 會自動觸發 deploy-pages.yml；push 完重跑本腳本繼續 3/4、4/4，"
  echo "  或手動 gh run watch ＋ python3 scripts/verify_live.py。"
  exit 0
fi

echo "  main 已與 origin 同步（無待 push 的 commit），手動觸發一次 workflow_dispatch："
gh workflow run deploy-pages.yml
echo "  已送出，等待新 run 出現…"
sleep 8
RUN_ID=$(gh run list --workflow=deploy-pages.yml --limit 1 --json databaseId --jq '.[0].databaseId')
echo "  → run id: ${RUN_ID}"
gh run watch "$RUN_ID" --exit-status

echo
echo "==> 4/4 verify_live.py：驗證 live 站（CI 綠不代表內容真的在，見腳本檔頭）"
python3 scripts/verify_live.py
