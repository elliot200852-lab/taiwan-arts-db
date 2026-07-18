#!/usr/bin/env python3
"""content/*.md → _build/ HTML 生成器（MD→HTML build）。

架構（2026-07-18 David 拍板）：
  文章內容的創作 SSOT ＝ repo `content/`（Markdown，本機可用 Obsidian 編輯）；
  HTML 成品與圖片才上 Google Drive（html 夾／img 夾），CI 部署時由
  pull_content.py 從 Drive 拉取。本腳本負責 content/ → `_build/`，
  `_build/` 鏡射 Drive html 夾結構（index.html＋pages/*.html），
  產物用 gws 上傳 Drive，不進 repo（.gitignore 擋 `_build/`）。

用法：
  python3 scripts/build_pages.py            # 輸出 _build/index.html＋_build/pages/*.html

Markdown 子集（受限轉換，刻意不吃完整 Markdown）：
  - 段落、`- ` 清單、`1. ` 有序清單、`> ` 引用、`[label](url)` 連結、
    行內 `**粗體**` 只用於「作品與聽看入口」清單的作品名（→ wk-title）。
  - `[^N]` → 腳註上標（<sup class="fn-ref">…#fnN）；「出處」有序清單第 N 項
    → <li id="fnN">。
  - `<!-- portrait -->` 獨立一行 → 插入 frontmatter `portrait` 的 figure。
  - 生平段落內的 `- 年份｜事件` 清單 → timeline。
  - 作品清單 `- **作品名**（年代）｜說明` → works-list（｜與 timeline 的
    ｜同為結構分隔符，不會出現在輸出文字裡）。

領域標籤（2026-07-18 David 拍板，Phase 1 雛形）：
  frontmatter `tags`（第一個為主要領域）→ 人物頁 hero 顯示可點 chips，
  連回 `../index.html#field-<tag>`；首頁人物 tab 上方同步生成「全部＋
  出現過的領域」篩選列（見 index.md `people[].tags`）。首頁 hash 路由
  擴充支援 `#field-<tag>` 深連結／返回鍵，與既有 `#general`/`#people`
  共存。Phase 2 分領域獨立頁上線後，同一批標籤與 URL 設計原樣承接。

頁面骨架照 templates/index.html、templates/person.html 的定案結構
（class 名、區塊順序）；首頁 tab 的 hash-router <script> 直接從
templates/index.html 抽出，單一來源。
"""

from __future__ import annotations

import html
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "content"
FIELDS = CONTENT / "fields"
BUILD = ROOT / "_build"
TEMPLATES = ROOT / "templates"

PERSON_SECTIONS = ["who", "bio", "works", "teaching", "storyteller", "footnotes"]
WHO_HEADINGS = ("他是誰", "她是誰")
CORE_NOTE = "本資料庫的核心：探究問題、跨科連結，與可直接帶進課堂的素材。"

# 領域中文名稱 → 分領域頁 slug（2026-07-18 拍板，6 個固定集合；見 handoff）。
# 人物頁 hero 的 tag chips 與 content/fields/*.md 的 `tag` 欄都對照這張表。
FIELD_SLUG_BY_TAG = {
    "美術": "field-art",
    "音樂": "field-music",
    "文學": "field-literature",
    "戲曲偶戲": "field-opera",
    "舞蹈": "field-dance",
    "電影": "field-film",
    # Phase 2（2026-07-18）：無人物的純議題領域——查無人物頁會用到這些 tag，
    # 但 field 頁本身仍以 content/fields/*.md 的 frontmatter 產生，兩者獨立。
    "工藝": "field-craft",
    "建築": "field-architecture",
    "節慶信仰": "field-festival",
}


def die(msg: str) -> None:
    print(f"[build_pages] 錯誤：{msg}", file=sys.stderr)
    sys.exit(1)


def esc(text: str) -> str:
    """跳脫文字節點／屬性中的 & < >（不動引號——內容沒有 ASCII 引號需求）。"""
    return html.escape(text, quote=False)


def inline(text: str) -> str:
    """受限行內轉換：先跳脫，再套 [^N] 上標與 [label](url) 連結。

    URL 允許一層括號配對（2026-07-18 修：原本 `(\\S+?)` 非貪婪比對，遇到 URL
    本身含 `)` 時會在第一個 `)` 處誤判為連結結尾截斷 href，例如 Wikimedia
    Commons 檔名帶 `(cropped)`／維基百科條目帶 `(1906年)` 這類括號消歧義寫法，
    造成連結 404＋截斷後的文字露出在 `</a>` 外。改用「非括號字元，或一層完整
    平衡括號」的字元類重複比對，涵蓋目前 content/ 內所有實際出現的括號寫法
    （單層、不巢狀），且對不含括號的一般 URL 行為不變。"""
    out = esc(text)
    out = re.sub(
        r"\[\^(\d+)\]",
        r'<sup class="fn-ref"><a href="#fn\1">[\1]</a></sup>',
        out,
    )

    def link(m: re.Match) -> str:
        label, url = m.group(1), m.group(2)
        if url.startswith("http://") or url.startswith("https://"):
            return f'<a href="{url}" target="_blank" rel="noopener">{label}</a>'
        return f'<a href="{url}">{label}</a>'

    return re.sub(r"\[([^\]]+)\]\(((?:[^()\s]|\([^()]*\))*)\)", link, out)


def split_frontmatter(path: Path) -> tuple[dict, str]:
    raw = path.read_text(encoding="utf-8")
    m = re.match(r"\A---\n(.*?)\n---\n(.*)\Z", raw, re.S)
    if not m:
        die(f"{path.name}：找不到 YAML frontmatter")
    return yaml.safe_load(m.group(1)), m.group(2)


def split_sections(body: str, path: Path) -> list[tuple[str, list[str]]]:
    """依 `## ` 切段，回傳 [(標題, blocks)]；block＝空行分隔的原始文字塊。"""
    sections: list[tuple[str, list[str]]] = []
    current: list[str] | None = None
    buf: list[str] = []

    def flush_block() -> None:
        if buf:
            current.append("\n".join(buf))
            buf.clear()

    for line in body.splitlines():
        if line.startswith("## "):
            if current is not None:
                flush_block()
            current = []
            sections.append((line[3:].strip(), current))
        elif current is None:
            if line.strip():
                die(f"{path.name}：`## ` 區段之外出現內容：{line!r}")
        elif not line.strip():
            flush_block()
        else:
            buf.append(line)
    if current is not None:
        flush_block()
    return sections


def split_paragraphs(body: str) -> list[str]:
    """純段落切分（無 `## ` 區段結構）：空行分隔的文字塊；供 field 頁導言使用
    （field 頁 frontmatter 後直接接導言文字，不像人物頁需要 `## ` 區段）。"""
    blocks: list[str] = []
    buf: list[str] = []
    for line in body.splitlines():
        if line.strip():
            buf.append(line)
        elif buf:
            blocks.append("\n".join(buf))
            buf = []
    if buf:
        blocks.append("\n".join(buf))
    return blocks


def parse_list_items(block: str) -> tuple[str, list[str]] | None:
    """辨識清單塊：回傳 ('ul'|'ol', items)；非清單回 None。"""
    lines = block.splitlines()
    if all(re.match(r"^- ", l) for l in lines):
        return "ul", [l[2:] for l in lines]
    if all(re.match(r"^\d+\. ", l) for l in lines):
        return "ol", [re.sub(r"^\d+\. ", "", l) for l in lines]
    return None


def render_figure(p: dict) -> str:
    width = f' width="{p["width"]}"' if "width" in p else ""
    return (
        '      <figure class="geo-fig">\n'
        f'        <img src="../img/{esc(p["file"])}" alt="{esc(p["alt"])}"{width} loading="lazy">\n'
        f'        <figcaption><span class="cap-title">{esc(p["caption_title"])}</span>'
        f'<br>{esc(p["caption_credit"])}</figcaption>\n'
        "      </figure>"
    )


def render_bio(blocks: list[str], portrait: dict | None, path: Path) -> str:
    """portrait 選配（2026-07-18）：frontmatter 無 `portrait` 時，`<!-- portrait -->`
    標記行直接跳過、不插 figure（肖像授權不明的人物走此路——見 PLAN.md 代換規則）。"""
    parts: list[str] = []
    for block in blocks:
        if block.strip() == "<!-- portrait -->":
            if portrait:
                parts.append(render_figure(portrait))
            continue
        listing = parse_list_items(block)
        if listing:
            kind, items = listing
            if kind != "ul":
                die(f"{path.name}：生平的 timeline 應為 `- 年份｜事件` 清單")
            lis = []
            for item in items:
                year, _, text = item.partition("｜")
                if not text:
                    die(f"{path.name}：timeline 項缺「｜」分隔：{item!r}")
                lis.append(
                    f'        <li><span class="tl-year">{esc(year)}</span>{inline(text)}</li>'
                )
            parts.append('      <ul class="timeline">\n' + "\n".join(lis) + "\n      </ul>")
            continue
        parts.append(f"      <p>{inline(block)}</p>")
    return "\n".join(parts)


def render_works(blocks: list[str], path: Path) -> str:
    items: list[str] = []
    for block in blocks:
        listing = parse_list_items(block)
        if not listing or listing[0] != "ul":
            die(f"{path.name}：作品區只能是 `- **作品名**（年代）｜說明` 清單")
        for item in listing[1]:
            m = re.match(r"^\*\*(.+?)\*\*(.*?)｜(.+)$", item)
            if not m:
                die(f"{path.name}：作品項格式不對：{item!r}")
            title, extra, note = m.groups()
            items.append(
                "        <li>\n"
                f'          <span class="wk-title">{esc(title)}</span>{esc(extra)}\n'
                f'          <span class="wk-note">{inline(note)}</span>\n'
                "        </li>"
            )
    return "\n".join(items)


def render_teaching(body_lines: list[str], path: Path) -> str:
    """教學素材：總說段落＋`### 小節` 各接一個清單。"""
    parts: list[str] = []
    i = 0
    blocks = body_lines
    while i < len(blocks) and not blocks[i].startswith("### "):
        parts.append(f"      <p>{inline(blocks[i])}</p>")
        i += 1
    while i < len(blocks):
        heading = blocks[i]
        if not heading.startswith("### "):
            die(f"{path.name}：教學素材小節結構不對：{heading!r}")
        title = heading[4:].strip()
        i += 1
        if i >= len(blocks):
            die(f"{path.name}：教學素材小節「{title}」缺清單")
        listing = parse_list_items(blocks[i])
        if not listing:
            die(f"{path.name}：教學素材小節「{title}」下不是清單")
        kind, items = listing
        lis = "\n".join(f"        <li>{inline(item)}</li>" for item in items)
        parts.append(f"      <h3>{esc(title)}</h3>")
        parts.append(f"      <{kind}>\n{lis}\n      </{kind}>")
        i += 1
    return "\n".join(parts)


def render_storyteller(blocks: list[str], path: Path) -> str:
    quotes: list[str] = []
    for block in blocks:
        lines = block.splitlines()
        if not all(l.startswith("> ") for l in lines):
            die(f"{path.name}：說書稿區只能是 `> ` 引用塊：{block!r}")
        text = " ".join(l[2:] for l in lines)
        quotes.append(f"      <blockquote>{inline(text)}</blockquote>")
    return "\n".join(quotes)


def render_tag_chips(tags: list[str], path: Path) -> str:
    """人物頁 hero 的領域標籤 chips；連到對應分領域頁 `../pages/field-<slug>.html`
    （2026-07-18 分領域頁上線，取代先前連回首頁 `#field-<tag>` 篩選列的雛形；
    首頁「人物」tab 上方的 `#field-<tag>` 篩選列本身不受影響，原樣保留）。"""
    chips = []
    for t in tags:
        slug = FIELD_SLUG_BY_TAG.get(t)
        if slug is None:
            die(f"{path.name}：tag `{t}` 不在領域對照表 FIELD_SLUG_BY_TAG 內（見 build_pages.py）")
        chips.append(f'        <a class="tag-chip" href="../pages/{slug}.html">{esc(t)}</a>')
    return "\n".join(chips)


def render_footnotes(blocks: list[str], path: Path) -> str:
    items: list[str] = []
    for block in blocks:
        listing = parse_list_items(block)
        if not listing or listing[0] != "ol":
            die(f"{path.name}：出處區只能是 `1. ` 有序清單")
        items.extend(listing[1])
    lis = [
        f'        <li id="fn{n}">{inline(item)}</li>'
        for n, item in enumerate(items, start=1)
    ]
    return "\n".join(lis)


PERSON_PAGE = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{name} — 臺灣人文藝術</title>
  <link rel="stylesheet" href="../assets/css/style.css">
</head>
<body>
  <nav class="crumbs"><a href="../index.html#people">← 人物</a> · <a href="../index.html#general">回首頁</a></nav>

  <div class="page-wrap">
    <header class="page-header person-hero">
      <div class="eyebrow">{field}</div>
      <h1>{name}</h1>
      <p class="ph-years">{years}</p>
      <p class="ph-field">{tagline}</p>
      <div class="tag-chips">
{tag_chips}
      </div>
      <div class="lede"><p>{lede}</p></div>
    </header>

    <section class="person-sec who">
      <h2>{who_heading}</h2>
      <p>{who}</p>
    </section>
{story}
    <section class="person-sec bio">
      <h2>生平與時代</h2>
{bio}
    </section>

    <section class="person-sec works">
      <h2>作品與聽看入口</h2>
      <ul class="works-list">
{works}
      </ul>
    </section>

    <section class="person-sec">
      <h2>地理錨點</h2>
      <p>{geo_text}</p>
      <p><a href="{geo_url}" target="_blank" rel="noopener">{geo_place} ↗</a></p>
    </section>

    <section class="teaching">
      <h2>教學素材</h2>
      <p class="core-note">{core_note}</p>
{teaching}
    </section>

    <section class="storyteller">
      <h2>說書稿切分提示</h2>
{storyteller}
    </section>

    <section class="footnotes">
      <h2>出處</h2>
      <ol>
{footnotes}
      </ol>
    </section>

    <footer class="page-foot">
      <div class="license">
        {license_line}
      </div>
      <div class="credit">
        {credit}
      </div>
    </footer>
  </div>
</body>
</html>
"""


def build_person(md_path: Path) -> tuple[str, str]:
    fm, body = split_frontmatter(md_path)
    for key in ("slug", "name", "years", "field", "tagline", "lede", "geo", "credit", "tags"):
        if key not in fm:
            die(f"{md_path.name}：frontmatter 缺 `{key}`")
    portrait = fm.get("portrait")  # 選配（2026-07-18）：無明確授權肖像的人物省略此欄。
    # source 選配：有 → 改作自 Taiwan.md 專文（代換規則 1）；無 → 本庫原創編寫（代換規則 2）。
    src = fm.get("source")
    if src:
        if "title" not in src or "url" not in src:
            die(f"{md_path.name}：source 需含 title 與 url")
        license_line = (
            f'本頁內容改作自 <a href="{esc(src["url"])}" target="_blank" rel="noopener">'
            f"{esc(src['title'])}</a>（CC BY-SA 4.0），依同條款釋出改作內容。"
        )
    else:
        license_line = "本頁由本資料庫依公開來源原創編寫（逐條見「出處」），以 CC BY-SA 4.0 釋出。"
    sections = split_sections(body, md_path)
    titles = [t for t, _ in sections]
    expected_tail = ["生平與時代", "作品與聽看入口", "教學素材", "說書稿切分提示", "出處"]
    # 「一個小故事」為選配區段（有出處才寫、絕不編造——David 2026-07-18），
    # 位置固定在 他是誰 之後。
    story_html = ""
    if len(titles) >= 2 and titles[1] == "一個小故事":
        story_paras = "\n".join(f"      <p>{inline(b)}</p>" for b in sections[1][1])
        story_html = (
            '\n    <section class="person-sec story">\n'
            "      <h2>一個小故事</h2>\n"
            f"{story_paras}\n"
            "    </section>\n"
        )
        sections = [sections[0]] + sections[2:]
        titles = [t for t, _ in sections]
    if len(titles) != 6 or titles[0] not in WHO_HEADINGS or titles[1:] != expected_tail:
        die(f"{md_path.name}：`## ` 區段應依序為 他是誰/她是誰、（選配）一個小故事、{'、'.join(expected_tail)}；實得 {titles}")

    who_blocks = sections[0][1]
    if len(who_blocks) != 1:
        die(f"{md_path.name}：「{titles[0]}」應為單一段落")

    html_out = PERSON_PAGE.format(
        name=esc(fm["name"]),
        field=esc(fm["field"]),
        years=esc(fm["years"]),
        tagline=esc(fm["tagline"]),
        tag_chips=render_tag_chips(fm["tags"], md_path),
        lede=inline(fm["lede"]),
        who_heading=esc(titles[0]),
        who=inline(who_blocks[0]),
        story=story_html,
        bio=render_bio(sections[1][1], portrait, md_path),
        works=render_works(sections[2][1], md_path),
        geo_text=inline(fm["geo"]["text"]),
        geo_url=esc(fm["geo"]["url"]),
        geo_place=esc(fm["geo"]["place"]),
        core_note=CORE_NOTE,
        teaching=render_teaching(sections[3][1], md_path),
        storyteller=render_storyteller(sections[4][1], md_path),
        footnotes=render_footnotes(sections[5][1], md_path),
        license_line=license_line,
        credit=inline(fm["credit"]),
    )
    return fm["slug"], html_out


INDEX_PAGE = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <meta name="description" content="{description}">
  <link rel="stylesheet" href="assets/css/style.css">
</head>
<body>
  <header class="site-head">
    <h1>{site_title}</h1>
    <p>{site_sub}</p>
  </header>

  <nav class="geo-tabs" role="tablist" aria-label="首頁導覽">
    <button type="button" class="geo-tab" data-tab="general" role="tab" aria-selected="false">總論</button>
    <button type="button" class="geo-tab" data-tab="people" role="tab" aria-selected="false">人物</button>
    <button type="button" class="geo-tab" data-tab="fields" role="tab" aria-selected="false">分領域</button>
    <a class="geo-tab" href="https://taiwan.md/" target="_blank" rel="noopener" title="臺灣.md — AI 原生的台灣開源知識庫（外部網站）">臺灣.md ↗</a>
  </nav>

  <main>
    <!-- 總論 -->
    <section class="tab-panel" data-panel="general" role="tabpanel">
      <div class="general-intro">
{intro}
      </div>
    </section>

    <!-- 人物 -->
    <section class="tab-panel" data-panel="people" role="tabpanel">
      <div class="field-filters" role="group" aria-label="依領域篩選人物">
{filters}
      </div>
      <div class="person-cards" id="person-cards">
{cards}
      </div>
    </section>

    <!-- 分領域（Phase 2，2026-07-18 上線）：卡片自 content/fields/*.md 產生 -->
    <section class="tab-panel" data-panel="fields" role="tabpanel">
      <div class="person-cards" id="field-cards">
{field_cards}
      </div>
    </section>
  </main>

{script}
</body>
</html>
"""


def extract_index_script() -> str:
    """從 templates/index.html 抽 hash-router <script>（單一來源，不複製貼上）。"""
    tpl = (TEMPLATES / "index.html").read_text(encoding="utf-8")
    m = re.search(r"^(  <script>\n.*?^  </script>)$", tpl, re.S | re.M)
    if not m:
        die("templates/index.html 找不到 <script> 區塊")
    return m.group(1)


def build_field_filters(people: list[dict]) -> str:
    """首頁人物 tab 上方的領域篩選列：「全部」＋出現過的領域各一顆
    （依人物出現順序去重）；分領域完整頁面上線前的雛形，chip 對應
    `#field-<tag>` hash（見 templates/index.html 的 hash-router）。"""
    seen: list[str] = []
    for p in people:
        for t in p["tags"]:
            if t not in seen:
                seen.append(t)
    chips = [
        '        <button type="button" class="field-chip active" '
        'data-field="all" aria-pressed="true">全部</button>'
    ]
    for t in seen:
        chips.append(
            f'        <button type="button" class="field-chip" '
            f'data-field="{esc(t)}" aria-pressed="false">{esc(t)}</button>'
        )
    return "\n".join(chips)


def build_field_cards() -> str:
    """首頁「分領域」tab 的 6 張領域卡：讀 content/fields/*.md frontmatter 的
    title/slug 各生成一張，卡片樣式沿用人物卡（.person-card）。容錯：
    content/fields/ 不存在或為空時回傳提示文字，不 die——寫手與工程平行、
    field 頁可能晚於首頁完成（2026-07-18）。"""
    if not FIELDS.is_dir() or not any(FIELDS.glob("*.md")):
        return '        <p class="field-empty">分領域內容尚未上架。</p>'
    cards: list[str] = []
    for md_path in sorted(FIELDS.glob("*.md")):
        fm, _ = split_frontmatter(md_path)
        for key in ("title", "slug"):
            if key not in fm:
                die(f"{md_path.name}：frontmatter 缺 `{key}`")
        cards.append(
            f'        <a class="person-card" href="pages/{esc(fm["slug"])}.html">\n'
            f'          <span class="pc-name">{esc(fm["title"])}</span>\n'
            "        </a>"
        )
    return "\n".join(cards)


def build_index() -> str:
    fm, body = split_frontmatter(CONTENT / "index.md")
    sections = split_sections(body, CONTENT / "index.md")
    if [t for t, _ in sections] != ["總論"]:
        die("index.md 應只有 `## 總論` 一個區段")
    intro_parts: list[str] = []
    prev_was_cta = False
    for block in sections[0][1]:
        solo_link = re.fullmatch(r"\[([^\]]+)\]\((\S+?)\)", block.strip())
        if solo_link:
            label, url = solo_link.groups()
            intro_parts.append(f'        <a class="intro-cta" href="{esc(url)}">{esc(label)}</a>')
            prev_was_cta = True
        elif prev_was_cta:
            intro_parts.append(f'        <p class="general-hint">{inline(block)}</p>')
            prev_was_cta = False
        else:
            intro_parts.append(f"        <p>{inline(block)}</p>")

    cards: list[str] = []
    for p in fm["people"]:
        tags_attr = esc(" ".join(p["tags"]))
        cards.append(
            f'        <a class="person-card" href="pages/{esc(p["slug"])}.html" data-tags="{tags_attr}">\n'
            f'          <span class="pc-name">{esc(p["name"])}</span>'
            f'<span class="pc-years">{esc(p["years"])}</span>\n'
            f'          <span class="pc-field">{esc(p["field"])}</span>\n'
            f'          <span class="pc-field">{esc(p["tagline"])}</span>\n'
            "        </a>"
        )

    return INDEX_PAGE.format(
        title=esc(fm["title"]),
        description=esc(fm["description"]),
        site_title=esc(fm["site_title"]),
        site_sub=esc(fm["site_sub"]),
        intro="\n".join(intro_parts),
        filters=build_field_filters(fm["people"]),
        cards="\n".join(cards),
        field_cards=build_field_cards(),
        script=extract_index_script(),
    )


FIELD_PAGE = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} — 臺灣人文藝術</title>
  <link rel="stylesheet" href="../assets/css/style.css">
</head>
<body>
  <nav class="crumbs"><a href="../index.html#fields">← 分領域</a> · <a href="../index.html#general">回首頁</a></nav>

  <div class="page-wrap">
    <header class="page-header">
      <div class="eyebrow">分領域</div>
      <h1>{title}</h1>
    </header>

{content}
{cards_section}
{footnotes_section}
    <footer class="page-foot">
      <div class="license">
        {license_line}
      </div>
      <div class="credit">
        {credit}
      </div>
    </footer>
  </div>
</body>
</html>
"""

FIELD_CREDIT = "本頁為教師備課資料庫。"


def load_people_meta(people_md: list[Path]) -> list[dict]:
    """讀所有人物 .md 的 frontmatter（不含生平內文），供 field 頁比對 tags 用。
    是各人物檔自己的 frontmatter（非 content/index.md 的 `people:` 清單）——
    field 頁「收錄該領域 tags 含該 tag 的所有人物」直接以此為準，
    不依賴 index.md 卡片是否已同步（寫手可能還沒補上首頁卡）。"""
    meta: list[dict] = []
    for md_path in people_md:
        fm, _ = split_frontmatter(md_path)
        for key in ("slug", "name", "years", "field", "tagline", "tags"):
            if key not in fm:
                die(f"{md_path.name}：frontmatter 缺 `{key}`")
        meta.append({
            "slug": fm["slug"],
            "name": fm["name"],
            "years": fm["years"],
            "field": fm["field"],
            "tagline": fm["tagline"],
            "tags": fm["tags"],
        })
    return meta


def render_field_body(body: str, path: Path) -> tuple[str, str]:
    """回傳 (content_html, footnotes_html)。直接重用人物頁的渲染管線
    （split_sections/render_bio/render_footnotes/inline()），不另寫一套
    （2026-07-18 Phase 2：field 頁從純導言升級成完整議題頁文章）。

    新格式：body 可選以一段不帶標題的「導言」開場（實際寫手慣例——先一段
    領域總覽，再進入 `## ` 子標題的正文），之後接規範的 `## ` 區段序列
    （同人物頁 split_sections 規則）。若最後一個區段標題為「出處」，獨立
    渲染為編號腳註；其餘各區段（含導言）各自成一個帶 <h2>（導言無 h2）的
    <section>，段落／時間軸內容重用 render_bio()（field 頁無肖像，恆傳
    portrait=None，遇到 `<!-- portrait -->` 標記也只會跳過不會出錯）。
    偵測用 `^## ` 找body中第一次出現處，不是只看開頭是否為 `## `——
    2026-07-18 上線當天發現：field-art.md 等 6 檔開頭其實是一段導言，
    真正的 `## ` 標題在後面才出現，原本「只看開頭」的判斷會把整份新格式
    內容誤判成舊格式，`## ` 與腳註清單原封不動當純文字印出（已修正）。

    舊格式（過渡期相容，2026-07-18 Phase 1 遺留）：body 完全沒有任何
    `## ` 標題，視為純段落導言，不產生出處區——尚未升級的 field 檔仍可
    正常 build 不 crash。"""
    m = re.search(r"^## ", body, re.M)
    if m is None:
        paras = split_paragraphs(body)
        if not paras:
            die(f"{path.name}：領域內容為空")
        content_html = "\n".join(f"      <p>{inline(p)}</p>" for p in paras)
        return content_html, ""

    intro_paras = split_paragraphs(body[: m.start()])
    intro_html = "\n".join(f"      <p>{inline(p)}</p>" for p in intro_paras)

    sections = split_sections(body[m.start():], path)
    if not sections:
        die(f"{path.name}：領域內容為空")
    if sections[-1][0] == "出處":
        footnotes_html = render_footnotes(sections[-1][1], path)
        content_sections = sections[:-1]
    else:
        footnotes_html = ""
        content_sections = sections
    if not content_sections and not intro_html:
        die(f"{path.name}：field 頁除「出處」外沒有任何內容區段")

    parts = []
    if intro_html:
        parts.append(
            '    <section class="person-sec">\n'
            f"{intro_html}\n"
            "    </section>"
        )
    for heading, blocks in content_sections:
        parts.append(
            '    <section class="person-sec">\n'
            f"      <h2>{esc(heading)}</h2>\n"
            f"{render_bio(blocks, None, path)}\n"
            "    </section>"
        )
    return "\n".join(parts), footnotes_html


def build_field(md_path: Path, people_meta: list[dict]) -> tuple[str, str]:
    fm, body = split_frontmatter(md_path)
    for key in ("title", "slug", "tag"):
        if key not in fm:
            die(f"{md_path.name}：frontmatter 缺 `{key}`")

    content_html, footnotes_html = render_field_body(body, md_path)

    tag = fm["tag"]
    matched = [p for p in people_meta if tag in p["tags"]]
    if matched:
        cards_html = "\n".join(
            f'        <a class="person-card" href="{esc(p["slug"])}.html" '
            f'data-tags="{esc(" ".join(p["tags"]))}">\n'
            f'          <span class="pc-name">{esc(p["name"])}</span>'
            f'<span class="pc-years">{esc(p["years"])}</span>\n'
            f'          <span class="pc-field">{esc(p["field"])}</span>\n'
            f'          <span class="pc-field">{esc(p["tagline"])}</span>\n'
            "        </a>"
            for p in matched
        )
        cards_section = (
            '    <section class="person-sec">\n'
            "      <h2>這個領域的人物</h2>\n"
            '      <div class="person-cards">\n'
            f"{cards_html}\n"
            "      </div>\n"
            "    </section>"
        )
    else:
        # 2026-07-18 Phase 2：純議題頁（工藝／建築／節慶信仰等查無人物的領域）
        # 整個人物卡區省略，不放「尚無收錄人物」占位——占位在純議題頁上很怪。
        cards_section = ""

    if footnotes_html:
        footnotes_section = (
            '    <section class="footnotes">\n'
            "      <h2>出處</h2>\n"
            "      <ol>\n"
            f"{footnotes_html}\n"
            "      </ol>\n"
            "    </section>"
        )
        license_line = "本頁由本資料庫依公開來源原創編寫（逐條見「出處」），以 CC BY-SA 4.0 釋出。"
    else:
        footnotes_section = ""
        license_line = "本頁由本資料庫依公開來源原創編寫，以 CC BY-SA 4.0 釋出。"

    html_out = FIELD_PAGE.format(
        title=esc(fm["title"]),
        content=content_html,
        cards_section=cards_section,
        footnotes_section=footnotes_section,
        license_line=license_line,
        credit=FIELD_CREDIT,
    )
    return fm["slug"], html_out


def build_fields(people_meta: list[dict]) -> int:
    """content/fields/*.md → _build/pages/field-<x>.html。容錯（2026-07-18）：
    目錄不存在或為空都只印訊息跳過、不 die——寫手可能晚於工程完成。
    回傳實際產出頁數，供 main() 的完成摘要計數。"""
    if not FIELDS.is_dir():
        print("[build_pages] content/fields/ 不存在，跳過分領域頁生成")
        return 0
    field_md = sorted(FIELDS.glob("*.md"))
    if not field_md:
        print("[build_pages] content/fields/ 沒有任何 .md，跳過分領域頁生成")
        return 0
    count = 0
    for md_path in field_md:
        slug, page_html = build_field(md_path, people_meta)
        if slug != md_path.stem:
            die(f"{md_path.name}：frontmatter slug（{slug}）與檔名不一致")
        (BUILD / "pages" / f"{slug}.html").write_text(page_html, encoding="utf-8")
        print(f"[build_pages] pages/{slug}.html ✓（分領域）")
        count += 1
    return count


def main() -> None:
    if not CONTENT.is_dir():
        die("content/ 不存在")
    people_md = sorted((CONTENT / "people").glob("*.md"))
    if not people_md:
        die("content/people/ 底下沒有任何 .md")

    (BUILD / "pages").mkdir(parents=True, exist_ok=True)

    people_meta = load_people_meta(people_md)

    index_html = build_index()
    (BUILD / "index.html").write_text(index_html, encoding="utf-8")
    print(f"[build_pages] index.html ✓")

    for md_path in people_md:
        slug, page_html = build_person(md_path)
        if slug != md_path.stem:
            die(f"{md_path.name}：frontmatter slug（{slug}）與檔名不一致")
        (BUILD / "pages" / f"{slug}.html").write_text(page_html, encoding="utf-8")
        print(f"[build_pages] pages/{slug}.html ✓")

    field_count = build_fields(people_meta)

    print(f"[build_pages] 完成：{len(people_md) + 1 + field_count} 頁 → {BUILD.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
