# 部署與內容來源

`main` 有 push（或手動 `gh workflow run deploy-pages.yml`）就跑
`.github/workflows/deploy-pages.yml`，產物送 GitHub Pages：
<https://elliot200852-lab.github.io/taiwan-arts-db/>

```
Drive「html 夾」(index.html＋pages/*.html) ──┐
                                             ├─pull_content.py──> site/ ──> Pages artifact
Drive「img 夾」({person-id}/{NN}.webp)  ─────┘                      ↑
                                                    repo assets/ ──copy──> site/assets/
```

## 內容完全不在 repo 裡

本 repo 比 taiwan-geo-db 更徹底：**不只圖片，連內容 HTML 都在 Google Drive**
（`~/.claude/CLAUDE.md`「網路部署架構鐵則」：內容走 Drive、repo 管怎麼組裝上架）。
repo 只有腳本（`scripts/`）、參考模板（`templates/`）與程式資產（`assets/css/`）。
`.gitignore` 擋掉整個 `site/`，CI 每次部署前從 Drive 拉。

| 項目 | 值 |
|---|---|
| Drive html 夾 | `FILL_ME_HTML_FOLDER_ID`（建好 Drive 夾後回填 `drive-manifest.yaml`）；夾內＝`index.html`＋`pages/*.html` |
| Drive img 夾 | `FILL_ME_IMG_FOLDER_ID`（同上）；夾內鏡射 `{person-id}/{NN}.webp` |
| 讀取者 | `channel-deployer@waldorfcreatorhubdatabase.iam.gserviceaccount.com` |
| repo secret | `DRIVE_SA_KEY`（SA key JSON；key 本身不留硬碟，要新的就重產） |
| 對照表 | `drive-manifest.yaml` |

`templates/` 是給內容組的**參考模板**（首頁 tab 結構、人物頁區塊順序、class 名），
不會被部署；實際頁面生成後上傳 Drive，那裡才是 SSOT。

## 內容更新流程

1. 本機生成／修改 HTML（照 `templates/` 的結構與 class）。
2. 用 gws 上傳到 Drive 對應夾（html 夾或 img 夾；新增子夾記得確認 SA 讀得到）。
3. push main 或手動觸發：`gh workflow run deploy-pages.yml`。
4. 部署綠了之後**一定要跑**：

```bash
python scripts/verify_live.py        # 全綠才算部署成功
```

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
