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
   （鏡射 Drive html 夾結構）。
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
