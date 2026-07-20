# 部署與內容來源

`main` 有 push（或手動 `gh workflow run deploy-pages.yml`）就跑
`.github/workflows/deploy-pages.yml`，產物送 GitHub Pages：
<https://elliot200852-lab.github.io/taiwan-arts-db/>

```
repo content/*.md ──build_pages.py──> _build/ ──gws 上傳──> Drive「html 夾」
Drive「html 夾」(index.html＋pages/*.html) ──┐
                                             ├─pull_content.py──> site/ ──> Pages artifact
Drive「img 夾」({person-id}/{NN}.webp)  ─────┘                      ↑
                                                    repo assets/ ──copy──> site/assets/
```

## 內容分兩層：文章 MD 在 repo、HTML 成品與圖在 Drive

2026-07-18 David 拍板：**文章內容的創作 SSOT＝`content/` 的 Markdown，放在 repo**
（本機可直接用 Obsidian 開 repo 編輯）；**HTML 成品與圖片才上 Google Drive**，
部署來源不變（`~/.claude/CLAUDE.md`「網路部署架構鐵則」：成品與二進位走 Drive、
repo 管怎麼組裝上架）。repo 有內容源（`content/`）、腳本（`scripts/`）、
參考模板（`templates/`）與程式資產（`assets/css/`）。
`.gitignore` 擋掉 `site/`（CI 組裝產物）與 `_build/`（本機 MD→HTML 產物）。

| 項目 | 值 |
|---|---|
| Drive html 夾 | `1Mqmci41UvdbN9qxlF6_gQ-N2Y9jm6em-`（＝`drive-manifest.yaml` 的 `files.site`）；夾內＝`index.html`（file ID `14ClkDjA3xEy7vVQuF92sshanWTpWebrY`）＋`pages/` 子夾（`1LE9I0Mp2BVNQxhHaVwlvJ8WoU1sSyU9s`，內含 `pages/*.html`） |
| Drive img 夾 | `165Zia_oiWLqVgOOVPmwT5iwxumhygUpT`（＝`drive-manifest.yaml` 的 `files.site/img`）；夾內鏡射 `{person-id}/{NN}.webp` |
| 讀取者 | `channel-deployer@waldorfcreatorhubdatabase.iam.gserviceaccount.com` |
| repo secret | `DRIVE_SA_KEY`（SA key JSON；key 本身不留硬碟，要新的就重產） |
| 對照表 | `drive-manifest.yaml`（folder ID 唯一 SSOT，改動即以此檔為準） |

`templates/` 是給內容組的**參考模板**（首頁 tab 結構、人物頁區塊順序、class 名），
不會被部署；實際頁面生成後上傳 Drive，那裡才是 SSOT。

## 內容更新流程

1. 編輯 `content/index.md` 或 `content/people/<slug>.md`（文章文字的 SSOT；
   MD 格式規約見 `scripts/build_pages.py` 檔頭說明）。
2. `python3 scripts/build_pages.py` → 產出 `_build/index.html`＋`_build/pages/*.html`
   （鏡射 Drive html 夾結構）＋`_build/search-index.json`（首頁站內檢索索引）＋
   `_build/sitemap.xml`（首頁＋全部頁面絕對 URL，供搜尋引擎；2026-07-20 加）。
   後三者與 `index.html` 同放 Drive html 夾**根目錄**（`pages/` 子夾只放
   `pages/*.html`），`pull_content.py` 遞迴鏡射整夾即涵蓋，不需要另外改它；
   `deploy.sh` 的 `sync_one` 也已把三者一起換版，見該腳本 STEP 2。
3. 用 gws 上傳 `_build/` 產物到 Drive html 夾（圖片另傳 img 夾；
   新增子夾記得確認 SA 讀得到）。
4. push main 或手動觸發：`gh workflow run deploy-pages.yml`。
5. 部署綠了之後**一定要跑**：

```bash
python scripts/verify_live.py        # 全綠才算部署成功
```

**或直接跑 `scripts/deploy.sh`**——把上面 2–5 步串成一條：build → 只換版 Drive
上有變動／新增的檔（md5Checksum 比對，不用一律全上傳）→ 觸發／等 CI → 驗 live，
失敗即停。細節與 Drive folder ID 出處見腳本檔頭註解。

## 為什麼一定要驗 live

內容不在 repo，**CI 綠只代表「當下從 Drive 拉到了東西」**，不代表站是完整的：
某個人物頁沒上傳、某張圖漏放、SA 對新子夾沒權限——CI 都可能照樣綠。

擋這件事的只有兩個地方，都別拿掉：

1. `pull_content.py` 兩道 fail-fast——
   （a）在 CI 上卻沒有 `DRIVE_SA_KEY` 憑證 → 非零退出中止建置；
   （b）拉完 `site/` 底下 0 個 HTML → 非零退出中止建置。
   另外 manifest 還是 `FILL_ME` 占位字串時也會直接中止。
   （img 夾拉到 0 檔只警告不中止——試點初期圖夾可能還是空的；圖的完整性
   由 verify_live.py 把關。）
2. `scripts/verify_live.py`——抓 live 首頁、解析所有內部連結頁與 `<img>`，
   逐一驗 HTTP 200（頁面驗 content-type 含 text/html、圖驗大小 > 0）。

## Secret 設定（首次或 key 重產時）

```bash
# SA key 由 channel-deployer SA 重產（不留硬碟），直接餵給 repo secret：
gh secret set DRIVE_SA_KEY --repo elliot200852-lab/taiwan-arts-db < key.json
rm key.json
```

Drive 夾建好後：把兩個 folder ID 回填 `drive-manifest.yaml`，
並確認兩個夾都共用給上表的 SA（檢視者權限即可），否則 CI 403。

## 每月連結驗證（`.github/workflows/link-check.yml`，2026-07-20 加）

`check_songs.py`／`check_works.py` 的完整連結驗證（YouTube oEmbed＋非 YouTube
HTTP 200）耗時且依賴網路，原本純手動跑。現排程化：

- 觸發：每月 1 日 01:00 UTC（台北 09:00）自動跑，或隨時手動
  `gh workflow run link-check.yml` / Actions 頁面 workflow_dispatch。
- 跑什麼：`python3 scripts/check_songs.py`＋`python3 scripts/check_works.py`
  完整模式（**不加** `--no-net`，即含網路連結驗證）。
- 判定：任一腳本回非零 exit（有死鏈）→ job 失敗（紅燈）；GitHub 對 repo owner
  的預設通知會寄失敗信。死鏈清單另外寫進該次 run 的 Step Summary，不必翻整份
  log 就能一眼看到。
- 不需要 Drive 憑證：本 workflow 只打 `content/songs/*.yaml`／`content/works.yaml`
  裡記的外部連結，不觸碰 Drive 上的站台內容，因此沒有掛 `DRIVE_SA_KEY`。
- 節流：兩支腳本共用 `check_songs.py` 的 `_check_links()`
  （`ThreadPoolExecutor(max_workers=6)`＋YouTube oEmbed 失敗重試 3 次）；
  本機實測 206 首歌＋85 筆作品共約 300 條連結全綠、耗時約 10–20 秒，未見
  429/限流，故未再另加 sleep delay。
- 已知盲點：YouTube oEmbed 對「影片存在但已被上傳者設限（如僅頻道會員可看）」
  驗不出來——望春風 `famous_cover` 曾踩過（oEmbed 回 200 但實際播不了，靠瀏覽器
  實測抓出）。這是 oEmbed 方法本身的限制，非本 workflow 要解的問題，維持現有
  判定即可。

## Drive 孤兒檔報告（`scripts/report_orphans.py`，report-only，2026-07-20 加）

`deploy.sh` 的 `sync_one` 換版邏輯只增改、**從不刪除** Drive html 夾裡的舊檔。
人物頁改名或整篇下架後，舊的 `pages/<old-slug>.html` 不會再被任何本機 build
產出提及，卻仍留在 Drive 裡變成孤兒——可能還被舊連結或搜尋引擎索引到。

`scripts/report_orphans.py` 只做這一件事：

1. 用 gws 遞迴列出 Drive html 夾（`drive-manifest.yaml` 的 `files.site`，含
   `pages/` 子夾）全部檔案的相對路徑。
2. 讀 `_build/`（`build_pages.py` 的產出，鏡射 Drive html 夾結構）當作
   canonical 清單；獨立執行時若 `_build/` 缺 `index.html` 會自動先跑一次
   `build_pages.py` 補齊（離線，不需要 Drive 憑證）。
3. 「Drive 有、build 沒有」的檔案列為孤兒，印出相對路徑＋Drive file id。
4. **只印清單，絕不呼叫任何刪除 API**——確認要不要清、要不要先備份，由人決定。

`ALLOWLIST`（腳本內常數）留給「合法但非 `build_pages.py` 產物」的例外檔案；
目前查無此類檔案——`index.html`／`search-index.json`／`sitemap.xml` 皆已是
`build_pages.py` 的正式產物，見上一節。

用法：

```bash
python3 scripts/report_orphans.py              # 獨立跑：_build/ 沒有就自動先 build
python3 scripts/report_orphans.py --rebuild    # 強制重跑 build_pages.py 再比對
python3 scripts/report_orphans.py --no-rebuild # 信任呼叫端剛 build 過，不重跑
```

`deploy.sh` 收尾已加一步 `python3 scripts/report_orphans.py --no-rebuild`
（重用 STEP 1 剛產出的 `_build/`，不重複 build）——**失敗或發現孤兒都不擋
本次部署**，只印警告，是否清理另外處理。
