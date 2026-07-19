# 臺灣人文藝術資料庫 — 視覺改版規格書（v1，2026-07-19）

> David 拍板的三個參考方向：
> 1. **全站主視覺** = Wix「XIO Gallery」藝廊範本：紙色畫布、大量留白、襯線字、赭金點綴、藝廊卡片。
> 2. **人物頁** = Brunello Cucinelli AI 站：沉浸式深色 hero、巨大襯線人名、氛圍底圖、安靜奢華的間距。
> 3. **臺灣歌曲 tab＋歌曲期頁** = Wix「Tyler Reece 音樂人」範本：暖深褐底、奶油金襯線標題、專輯面板與曲目列。
>
> David 另指示：**人物照片縮小（現版太擠）**；**每位人物都要有一張圖**——無肖像者用 AI 生成「情境圖」（非人像、不出現可辨識正面臉孔）。
> 情境圖檔名規則：`img/scenes/<slug>.jpg`（大圖 1536×1024）＋ `img/scenes/thumbs/<slug>.jpg`（卡片縮圖 480w）。
> 歌曲期封面：`img/scenes/<era-slug>.jpg`（1024×1024）＋ thumbs 同規則。首頁 hero：`img/scenes/site-hero.jpg`。
> 圖片 SSOT 在 Drive（repo 不進任何二進位），本機 build 只引用 URL。

> **David 2026-07-19 拍板：頁面不再標示 AI 生成小字**——原「情境插畫 · AI 生成意象」（首頁 hero `.home-hero-note`、人物 hero `.ph-ai-note`）與「卡片與頁首情境圖為 AI 生成之意象插畫…」（`.cards-note`）三處聲明元素已自 `scripts/build_pages.py` 四個內嵌模板與 `templates/*.html` 參考檔移除（CSS 規則留著無害，class 只加不改）。以下 §4／§5／§6 內文中殘留的小字描述僅為改版當時（v1）的歷史規格記述，**現行 build 已不輸出**。**不變**：無肖像者用 AI 生成情境圖、不畫可辨識正面臉孔（背影／剪影／手部特寫替代）；塑像／作品照代打人物照片時，仍必須以 alt/caption 明示非本人——這兩條紀律持續適用，只是不再用頁面小字做「本圖為 AI 生成」的免責聲明。

## 0. 硬規則（實作者必讀）

- **只加 class 不改名**：既有 JS 依賴的 class/id 一律保留——`.geo-tab`、`.geo-panel`、tab hash-router、`.field-chip` 篩選、地圖 popup 相關全部不准改名。
- **hash-router `<script>` 不動**：`extract_index_script()` 從 `templates/index.html` 抽 script，本次不改其邏輯。
- **地圖 tab 內部不動**：`render_map_svg()`／popup／pin 定位程式全部不碰，只允許它自然繼承新的字體與顏色 token。
- **雙寫同步**:  `scripts/build_pages.py` 四個內嵌模板字串（PERSON_PAGE/INDEX_PAGE/FIELD_PAGE/SONG_ERA_PAGE）與 `templates/*.html` 參考檔要一起改、保持一致。
- **content/*.md 正文一個字不動**（David 定稿紀律）；只允許 build 邏輯層面的變更。
- 響應式必須保住：360px 手機寬度可讀、卡片 grid 自動收欄、hero 文字 clamp。
- build 完 `python3 scripts/build_pages.py` 必須零錯誤跑完。

## 1. 字體（所有頁 `<head>` 加載 Google Fonts）

```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,500;0,600;1,500&family=Noto+Serif+TC:wght@500;700;900&display=swap" rel="stylesheet">
```

- `--serif`: `"Noto Serif TC","Songti TC",...`（沿用，但實際載入 web font 500/700/900）
- `--latin`: `"Cormorant Garamond", Georgia, serif` — **新增 token**。所有西元年份、編號、拉丁字都用它（義大利體為主）。
- `--sans` 沿用（小型 meta 用）。

## 2. 色彩 token（style.css `:root` 全面改寫，舊 token 名保留、值可調）

```css
--paper:#f7f4ec;  --paper-2:#efeadd;  --card:#fdfbf4;
--ink:#26252b;    --ink-soft:#5a5862;
--line:#ddd6c6;   --hair:#e6e0d2;          /* 細髮線 */
--indigo:#45548e; --indigo-deep:#2f3a66;   /* 沿用 */
--plum:#7c5866;   --gold:#caa14a;          /* 沿用 */
--ochre:#a97f2f;  --ochre-soft:#c8a558;    /* XIO 赭金：eyebrow、rule、active tab */
--night:#232227;  --night-ink:#f3efe6; --night-soft:#c9c2b4;  /* 人物 hero */
--taupe:#544e46;  --taupe-2:#45403a;                            /* 歌曲深褐 */
--cream:#efe6cf;  --pale-gold:#e6d49a;                          /* 歌曲奶油金 */
```

## 3. 全站 chrome

**站頭 `.site-head`**（index）：置中 wordmark——「臺灣人文藝術」2.4rem／900／`--ink`；副標「人文與藝術資料庫 · 教師備課用」0.78rem、letter-spacing 0.42em、`--ink-soft`。站頭下方一條**雙髮線**（1px `--line` ＋ 3px 間隔 ＋ 1px `--hair`），藝廊標籤感。

**Tab 列 `.geo-tabs`**：去掉現在的「方塊分頁」感，改 XIO 極簡字標式——置中、0.92rem、letter-spacing 0.16em、`--ink-soft`；hover `--ink`；active `--ochre` 且底下 2px `--ochre` 短底線（寬度只到文字）；整列底部一條 1px `--hair` 貫穿。

## 4. 首頁「總論」tab

1. 頂部加 **hero 圖**：`img/scenes/site-hero.jpg`，寬 100%、`aspect-ratio: 21/9`、`max-height:420px`、`object-fit:cover`、圓角 2px。（2026-07-19 起不再加 AI 生成小字說明，見上方拍板記錄）
2. 現有導言文字：**改左對齊**（現在的 text-align:center 造成參差），max-width 62ch 置中容器，字級 1.02rem、行高 2；第一段 1.12rem。
3. 「或切到人物…」引導句：置中、上下留 40px、前加 24px `--ochre` 短 rule（XIO「See More」樣式：左右細髮線、中央赭金字）。

## 5. 人物 tab（index 內）＋領域頁人物卡 — 卡片全面帶圖

`.person-card` 改為上圖下文的藝廊卡（XIO Upcoming Events 樣式）：

```
<a class="person-card" href="...">
  <figure class="pc-art"><img src="img/scenes/thumbs/<slug>.jpg" alt="" loading="lazy"></figure>
  <div class="pc-body">
    <span class="pc-name">黃土水</span>
    <span class="pc-years">1895 — 1930</span>   ← --latin italic
    <span class="pc-field">美術 · 雕塑</span>
    <p class="pc-tagline">…</p>
  </div>
</a>
```

- `.pc-art`：`aspect-ratio:3/2`、`overflow:hidden`；img `object-fit:cover`、transition transform .5s；卡 hover 時 `scale(1.04)`。
- 卡片：bg `--card`、1px `--line` 邊、radius 4px；hover：`translateY(-2px)`＋`box-shadow 0 12px 32px rgba(38,37,43,.10)`。
- `.pc-name` 1.12rem/700 `--indigo-deep`；`.pc-years` `--latin` italic 0.95rem `--ochre`；`.pc-field` 0.78rem letter-spacing .12em `--ink-soft`；tagline 0.88rem、`-webkit-line-clamp:2`。
- Grid：`minmax(232px,1fr)`、gap 28px。
- 篩選 chips `.field-chip`：髮線藥丸（1px `--line`、透明底、`--ink-soft`）；active＝`--ink` 底、`--paper` 字。**class 名與 data 屬性不動**（篩選 JS 依賴）。
- ~~人物 grid 下方加一行小字（index 人物 tab 與領域頁皆加）~~ 2026-07-19 拍板移除（見上方拍板記錄），CSS `.cards-note` 留著無害但 build 已不輸出此段。

首頁人物卡的圖：`img/scenes/thumbs/{slug}.jpg`；領域頁（在 pages/ 下）用 `../img/scenes/thumbs/{slug}.jpg`。**路徑由 build 依 slug 推導，不需 frontmatter 新欄位**；67 位全部有圖（本次一併生成）。

## 6. 人物頁 — Cucinelli 沉浸式 hero

結構（PERSON_PAGE 模板重排）：

```
crumbs（紙底上、hero 之上，樣式沿用但縮小 0.82rem）
<header class="person-hero immersive" style="background-image:url('../img/scenes/<slug>.jpg')">
  ── CSS 疊加：linear-gradient(180deg, rgba(28,27,32,.42), rgba(28,27,32,.80)) ＋ background center/cover
  <p class="ph-eyebrow">音樂 · 流行歌曲</p>        ← 0.82rem、letter-spacing .3em、--ochre-soft
  <h1>鄧麗君</h1>                                  ← clamp(2.6rem,5vw,3.8rem)、900、--night-ink
  <p class="ph-years">1953 — 1995</p>              ← --latin italic 1.3rem、--night-soft
  <p class="ph-tagline">用歌聲飛過邊界的歌手…</p>   ← 1.02rem、行高1.9、#e8e2d2、max-width 640px
  <div class="tag-chips">…</div>                   ← 外框改 rgba(255,255,255,.4)、字 #efe9da
  <!-- .ph-ai-note 小字已於 2026-07-19 拍板移除，見上方拍板記錄 -->
</header>
```

- hero 滿版出血（突破 `.page-wrap`：`margin-inline: calc(50% - 50vw)` 技法），`min-height:400px`、flex 對齊底部、padding `64px 8vw 56px`。
- 無 scene 圖 fallback：純 `--night` 底（樣式相同）。
- **lede**：從 hero 下方開始的內文首段（原 blockquote）改成置中優雅導言——去左邊條，max-width 680px、1.12rem、行高 2，上方 24px `--ochre` 短 rule。
- 內文欄 `.page-wrap` 收窄到 **max-width 800px**；body 1rem/1.95。
- h2：1.45rem＋下方 32px×2px `--ochre` rule（取代現行 indigo 底線）。
- **肖像保留＋畫框設計（David 2026-07-19 明示）**：已授權的本人肖像照一律保留，且做成「小畫框」：
  - `figure.geo-fig` max-width **340px**（縮小）；≥900px 視窗 float:right＋margin `4px 0 20px 32px` 讓文字環繞；手機置中。
  - 畫框三層：外框 1px `--ochre` 細線 → 白色 passe-partout 襯紙（`background:#fffdf8; padding:10px`）→ 相片本體外再一圈 1px `--hair` 內髮線；整體 `box-shadow: 0 6px 18px rgba(38,37,43,.12)`（掛在牆上的畫框感）。
  - caption 改「藝廊標籤牌」樣式：置中、0.76rem；`caption_title` 用 serif、`caption_credit` 用 `--sans` letter-spacing .06em `--ink-soft`；caption 與框之間留 10px。
- `.teaching`／說書稿區：`--paper-2` 面板、radius 6、padding 24–28px。
- 腳註：頂部 1px `--hair`、0.85rem。

## 7. 臺灣歌曲 tab（index 內）— Tyler Reece 深色面板

整個 tab 內容包進 `.songs-shell`：bg `--taupe`、radius 8px、padding 56px 48px（手機 32px 20px）、文字反白。

- 標題「臺灣歌曲」：2rem／900／`--pale-gold`；副說明 0.95rem `--cream` 80%。
- 期卡 `.era-card` 改深色專輯卡：
  - 上方封面 `img/scenes/thumbs/{era-slug}.jpg`、`aspect-ratio:1/1`、cover；
  - 左上角疊期號 `01`–`08`（`--latin`、1rem、`--pale-gold`、半透明黑底小方塊）；
  - 卡底 `rgba(255,255,255,.045)`、1px `rgba(255,255,255,.14)` 邊、radius 6；hover 邊 `rgba(255,255,255,.32)`、底 `.08`；
  - 期名 1.08rem `--cream` serif 700；時期 `--latin` italic `--pale-gold` 0.9rem；axis 摘句 0.85rem `#cfc7b6`、clamp 3 行。
- grid `minmax(240px,1fr)`、gap 24px。

## 8. 歌曲期頁 — 深色曲目頁

- `<body class="theme-songs">`：整頁 bg `--taupe-2`；預設文字 `#d8d0bf`；crumbs 反白 70%。
- hero `.era-hero.immersive`：同人物頁出血技法，背景 `../img/scenes/{era-slug}.jpg`＋`linear-gradient(180deg, rgba(58,53,46,.55), rgba(69,64,58,.92))`；期號大字（`--latin` 2.6rem `--pale-gold` 40% 透明）→ 期名 clamp(2.2rem,4vw,3rem)／900／`--pale-gold` → 時期 `--latin` italic `--cream` → axis 段落 max-width 640px。
- **曲目列（核心）** `.song-item`：模仿 Wix 播放器 track list——
  - row grid：`[曲序 2.6rem] [主欄 1fr] [年份]`；曲序 `--latin` 1.05rem `--pale-gold`；
  - 歌名 1.06rem `--cream` 700；創作者行 0.82rem `#b8b0a0`；hook 0.9rem `#d8d0bf`；
  - 聆聽連結：藥丸（1px `rgba(255,255,255,.25)` 邊、radius 999、0.78rem `--cream`、前綴 `♪ `）；hover 底 `rgba(255,255,255,.10)`；
  - 列間 1px `rgba(255,255,255,.12)` 髮線；hover 整列底 `rgba(255,255,255,.045)`；
  - note／出處小字 `#a89f8e`。
- 回鏈（人物頁連結等）在深色底上用 `--pale-gold` 底線連結。

## 9. 領域頁

淺色紙底維持：hero 沿用純文字（h1＋`--ochre` rule＋導言），人物卡自動吃第 5 節新卡（含縮圖）。純議題頁（工藝/建築/節慶信仰）無人物 grid，只吃新的 h2/正文/腳註樣式。

## 10. 不改的東西（再列一次）

地圖 tab 內部、hash-router script、check_songs 驗證邏輯、content/*.md 正文、`docs/` 其他文件、CI workflow、pull_content.py。
