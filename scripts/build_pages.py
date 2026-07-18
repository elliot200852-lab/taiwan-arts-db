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
import json
import math
import re
import sys
from pathlib import Path

import yaml

import check_songs

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "content"
FIELDS = CONTENT / "fields"
SONGS = CONTENT / "songs"
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

# Phase 2B（2026-07-18）：「臺灣藝文地圖」tab。
#
# 底圖＝templates/taiwan-map-base.svg：taiwan.md 官網
# https://taiwan.md/taiwan-shape/ 提供的 taiwan-location-map.svg（David
# 2026-07-18 指定：底圖一律用 taiwan.md 現成 SVG）。實測與 Wikimedia Commons
# 「File:Taiwan location map.svg」逐位元組相同（NordNordWest 製，
# CC BY-SA 3.0 / GFDL 1.2+）；投影是該圖所屬 Wikipedia
# Module:Location_map/data/Taiwan 的官方等距圓柱投影，bounds 見下方常數。
#
# 縣市界線＋pin 落點驗證用的幾何資料＝templates/taiwan-counties.json：
# 同樣源自內政部國土測繪中心「直轄市、縣市界線(TWD97經緯度)」（taiwan.md 自己
# 代管的 topojson 解析度不足以撐 8 點檢核，改用同一份政府資料血緣但解析度高
# 7 倍的 dkaoster/taiwan-atlas 版本——見 scripts/build_map_base.py 檔頭完整說明）。
#
# 投影公式沿用底圖原生 bounds／viewBox 的線性映射，**不**額外疊加 cos(緯度)
# 修正：該底圖本身已用「N/S 拉伸 110%」內建修正過經度失真
# （1/cos(23.5°)≈1.09，與 110% 幾乎一致）；實測若再疊加一次修正，反而會把
# pin 投影錯位。2026-07-18 用台北車站／高雄港／台東市／鵝鑾鼻／富貴角／台中／
# 花蓮市／馬公八個已知點逐一核對，全數落在正確縣市 polygon 內（point-in-
# polygon 程式驗證，非目測）；65 個 map.yaml pin 全量核對亦全過（沿海地點
# 有 2 公里容許誤差，見 MAP_COASTAL_TOLERANCE_KM 說明）。
MAP_YAML = CONTENT / "map.yaml"
MAP_BASE_SVG = TEMPLATES / "taiwan-map-base.svg"
MAP_COUNTIES_JSON = TEMPLATES / "taiwan-counties.json"
MAP_TOP, MAP_BOTTOM = 26.4, 21.7      # 緯度（°N）：上、下邊界
MAP_LEFT, MAP_RIGHT = 118.0, 122.3    # 經度（°E）：左、右邊界
MAP_VIEWBOX_W, MAP_VIEWBOX_H = 1015.733, 1221.247  # 對應底圖 SVG 的 viewBox
MAP_CLUSTER_RADIUS = 20  # viewBox 單位；近於此距離的 pin 合併成一顆聚合圓點
MAP_COASTAL_TOLERANCE_KM = 2.0
# 座標落在宣稱縣市 polygon 外、但距其邊界在此公里數以內時仍算通過——縣市界線
# 資料本身是簡化過的海岸線，港口／海邊廟宇這類緊貼海岸的地標（如朱銘美術館、
# 白沙屯拱天宮、旗津）用嚴格 point-in-polygon 會被簡化掉的海岸線誤判在界外，
# 2026-07-18 65 pin 全量驗證時 3 筆屬此情況、皆確認座標本身正確，非資料錯誤。

# map.yaml 的 `county` 欄位是 taiwan-geo-db 頁 slug（非中文縣市名），
# 需要對照回縣市界線資料裡的縣市名稱才能做 point-in-polygon 驗證。
# 只列 map.yaml 65 筆 pin 實際用到的 19 個＋離島 4 個備用（金門/連江/澎湖/
# 新竹縣目前無 pin 使用，但底圖仍要畫出這些縣市，一併列表方便未來擴充）。
GEO_SLUG_TO_COUNTY = {
    "changhua": "彰化縣", "chiayi-city": "嘉義市", "chiayi-county": "嘉義縣",
    "hsinchu-city": "新竹市", "hsinchu": "新竹縣", "hualien": "花蓮縣",
    "kaohsiung": "高雄市", "keelung": "基隆市", "miaoli": "苗栗縣",
    "nantou": "南投縣", "new-taipei": "新北市", "penghu": "澎湖縣",
    "pingtung": "屏東縣", "taichung": "台中市", "tainan": "台南市",
    "taipei": "台北市", "taitung": "台東縣", "taoyuan": "桃園市",
    "yilan-yilan": "宜蘭縣", "yilan-yuanshan": "宜蘭縣", "yunlin": "雲林縣",
    "kinmen": "金門縣", "lienchiang": "連江縣", "matsu": "連江縣",
}


def project_latlng(lat: float, lng: float) -> tuple[float, float]:
    """等距圓柱投影：lat/lng → 底圖 SVG 座標（viewBox 單位）。不額外疊加
    cos(緯度) 修正，理由見上方模組註解——底圖自身的 110% N/S 拉伸已內建。"""
    x = (lng - MAP_LEFT) / (MAP_RIGHT - MAP_LEFT) * MAP_VIEWBOX_W
    y = (MAP_TOP - lat) / (MAP_TOP - MAP_BOTTOM) * MAP_VIEWBOX_H
    return x, y


def extract_map_base_svg() -> str:
    """從 templates/taiwan-map-base.svg 抽出 `<svg ...>` 標籤內的子元素
    （不含外層 svg 標籤本身，因為輸出時要用自己的 viewBox／class 重新包一層，
    才能疊加 pin 圖層）；單一來源，不複製貼上底圖內容。"""
    svg = MAP_BASE_SVG.read_text(encoding="utf-8")
    m = re.search(r"<svg\b[^>]*>(.*)</svg>\s*\Z", svg, re.S)
    if not m:
        die("templates/taiwan-map-base.svg 找不到 <svg> 內容")
    return m.group(1).strip()


_counties_data_cache: dict | None = None


def load_counties_data() -> dict | None:
    """讀 templates/taiwan-counties.json（縣市界線幾何，見 build_map_base.py）。
    回傳 None＝檔案不存在（該檔是 build 輸入而非內容，理論上應恆存在於 repo；
    容錯只是避免地圖 tab 因這份輔助資料缺席就整頁 die，縣市線與 pin 驗證則
    會分別優雅降級：不畫線、不驗證縣市但仍畫 pin）。"""
    global _counties_data_cache
    if _counties_data_cache is not None:
        return _counties_data_cache
    if not MAP_COUNTIES_JSON.is_file():
        return None
    _counties_data_cache = json.loads(MAP_COUNTIES_JSON.read_text(encoding="utf-8"))
    return _counties_data_cache


def _point_in_ring(lng: float, lat: float, ring: list[list[float]]) -> bool:
    """Ray casting：點是否在單一環內（環座標為 [lng,lat] pair 列表，首尾閉合）。"""
    inside = False
    n = len(ring)
    for i in range(n - 1):
        x1, y1 = ring[i]
        x2, y2 = ring[i + 1]
        if (y1 > lat) != (y2 > lat):
            x_at = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
            if lng < x_at:
                inside = not inside
    return inside


def _nearest_ring_dist_km(lng: float, lat: float, rings: list[list[list[float]]]) -> float:
    best = float("inf")
    for ring in rings:
        for i in range(len(ring) - 1):
            x1, y1 = ring[i]
            x2, y2 = ring[i + 1]
            dx, dy = x2 - x1, y2 - y1
            if dx == 0 and dy == 0:
                d = math.hypot(lng - x1, lat - y1)
            else:
                t = max(0.0, min(1.0, ((lng - x1) * dx + (lat - y1) * dy) / (dx * dx + dy * dy)))
                d = math.hypot(lng - (x1 + t * dx), lat - (y1 + t * dy))
            best = min(best, d)
    return best * 111.32  # 度→公里，臺灣緯度尺度下的粗略換算已足夠這裡的用途


def point_in_county(lng: float, lat: float, county_name: str, counties: dict) -> tuple[bool, str | None]:
    """回傳 (是否算通過, 若失敗則落在哪個縣市或 None)。嚴格 point-in-polygon
    先判；沒過但落在 MAP_COASTAL_TOLERANCE_KM 內也算通過（見上方常數說明）。"""
    all_groups = {**counties.get("main", {}), **counties.get("kinmen", {}), **counties.get("matsu", {})}
    target_rings = all_groups.get(county_name)
    if target_rings is None:
        return False, None
    if any(_point_in_ring(lng, lat, r) for r in target_rings):
        return True, None
    if _nearest_ring_dist_km(lng, lat, target_rings) <= MAP_COASTAL_TOLERANCE_KM:
        return True, None
    actual = None
    for name, rings in all_groups.items():
        if any(_point_in_ring(lng, lat, r) for r in rings):
            actual = name
            break
    return False, actual


def load_map_pins() -> list[dict] | None:
    """讀 content/map.yaml 的 pins 清單並驗證。回傳 None＝檔案不存在
    （過渡期：資料 agent 尚未交檔，tab 顯示占位、不 crash）。
    兩層資料品質保險：(1) 座標超出臺灣範圍（MAP_TOP/BOTTOM/LEFT/RIGHT）；
    (2) 座標與宣稱的 county 不符（point-in-polygon，見 point_in_county()）。
    兩者皆直接 die——寧可擋下明顯的經緯度或縣市打錯，也不要讓 pin 掉錯地方。"""
    if not MAP_YAML.is_file():
        return None
    data = yaml.safe_load(MAP_YAML.read_text(encoding="utf-8")) or {}
    pins = data.get("pins")
    if pins is None:
        die("content/map.yaml：缺 `pins` 清單")
    counties = load_counties_data()
    validated = []
    for i, pin in enumerate(pins):
        label = pin.get("name", f"第 {i+1} 筆")
        for key in ("type", "name", "link", "county", "lat", "lng", "hook"):
            if key not in pin:
                die(f"content/map.yaml：{label} 缺 `{key}`")
        if pin["type"] not in ("person", "place"):
            die(f"content/map.yaml：{label} 的 type 須為 person 或 place，實得 {pin['type']!r}")
        if pin["type"] == "person" and "slug" not in pin:
            die(f"content/map.yaml：{label} 是 person 卻缺 `slug`")
        lat, lng = pin["lat"], pin["lng"]
        if not (MAP_BOTTOM <= lat <= MAP_TOP and MAP_LEFT <= lng <= MAP_RIGHT):
            die(
                f"content/map.yaml：{label} 座標超出臺灣範圍"
                f"（lat={lat}, lng={lng}；容許範圍 lat {MAP_BOTTOM}–{MAP_TOP}、"
                f"lng {MAP_LEFT}–{MAP_RIGHT}）"
            )
        if counties is not None:
            county_name = GEO_SLUG_TO_COUNTY.get(pin["county"])
            if county_name is None:
                die(
                    f"content/map.yaml：{label} 的 county `{pin['county']}` 不在"
                    f"GEO_SLUG_TO_COUNTY 對照表內（build_pages.py）"
                )
            ok, actual = point_in_county(lng, lat, county_name, counties)
            if not ok:
                where = f"實際落在「{actual}」" if actual else "不在任何已知縣市內"
                die(
                    f"content/map.yaml：{label} 座標（lat={lat}, lng={lng}）不在宣稱的"
                    f"「{county_name}」（county: {pin['county']}）範圍內（含 "
                    f"{MAP_COASTAL_TOLERANCE_KM}km 沿海容許誤差），{where}——"
                    "請確認是座標打錯還是 county 打錯"
                )
        validated.append(pin)
    return validated


def cluster_pins(pins: list[dict]) -> list[dict]:
    """把投影後距離在 MAP_CLUSTER_RADIUS 內的 pin 合併成一組（同縣市密集點，
    如臺北、臺南，避免疊在一起點不到）。單一 pin 的組直接視為普通 pin；
    多 pin 的組渲染成數字聚合圓點，點擊展開清單（見 render_map_svg）。
    回傳 [{x, y, items: [pin,...]}, ...]，貪婪最近鄰演算法（樣本數小，
    不追求最佳分群，求視覺上不重疊）。"""
    clusters: list[dict] = []
    for pin in pins:
        x, y = project_latlng(pin["lat"], pin["lng"])
        placed = False
        for c in clusters:
            if ((c["x"] - x) ** 2 + (c["y"] - y) ** 2) ** 0.5 <= MAP_CLUSTER_RADIUS:
                n = len(c["items"])
                c["x"] = (c["x"] * n + x) / (n + 1)
                c["y"] = (c["y"] * n + y) / (n + 1)
                c["items"].append(pin)
                placed = True
                break
        if not placed:
            clusters.append({"x": x, "y": y, "items": [pin]})
    return clusters


def _ring_path_d(ring: list[list[float]], project) -> str:
    pts = [project(lat, lng) for lng, lat in ring]
    d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    return d + " Z"


def render_county_lines(counties: dict | None) -> str:
    """本島（含澎湖等，"main" 群組）縣市界線，細淡線疊在底圖上——只當視覺輔助
    與 pin 落點的人眼對照，實際驗證邏輯在 point_in_county()，不依賴這條線畫得
    多準。counties 缺席（taiwan-counties.json 還沒 build_map_base.py 產生過）
    就跳過，不影響底圖與 pin 正常顯示。"""
    if not counties or "main" not in counties:
        return ""
    parts = [
        # fill="none" 內建在元素上（不只靠 CSS）：CSS 若失效，縣市 polygon 會以
        # 預設黑色填滿蓋掉整張底圖——2026-07-18 無樣式截圖實測過這個災難畫面。
        f'<path class="county-line" fill="none" d="{_ring_path_d(ring, project_latlng)}"/>'
        for rings in counties["main"].values()
        for ring in rings
    ]
    return f'<g class="county-lines">{"".join(parts)}</g>'


def render_map_inset(counties: dict | None) -> str:
    """金門／馬祖 inset 小圖：兩者離本島遠，在主圖真實位置比例下小到看不清楚
    （2026-07-18 實測：底圖點陣化後，兩地落點窗口內幾乎全是海面反鋸齒色，
    陸地色塊佔比趨近於零），改用獨立小座標系統、較大相對比例畫出，放主圖
    左下角，附文字標籤——維持「臺澎金馬全覆蓋、不裁切」但仍看得清楚。
    澎湖不在此列：離本島近，主圖真實位置已看得見，照 David 指示畫在真實位置。"""
    if not counties or "kinmen" not in counties or "matsu" not in counties:
        return ""
    groups = {**counties["kinmen"], **counties["matsu"]}
    lngs = [lng for rings in groups.values() for ring in rings for lng, lat in ring]
    lats = [lat for rings in groups.values() for ring in rings for lng, lat in ring]
    lng_min, lng_max = min(lngs), max(lngs)
    lat_min, lat_max = min(lats), max(lats)
    pad = 0.05
    lng_min, lng_max = lng_min - pad, lng_max + pad
    lat_min, lat_max = lat_min - pad, lat_max + pad
    inset_w, inset_h = 150.0, 150.0

    def inset_project(lat, lng):
        x = (lng - lng_min) / (lng_max - lng_min) * inset_w
        y = (lat_max - lat) / (lat_max - lat_min) * inset_h
        return x, y

    shapes = [
        f'<path class="county-line inset-shape" d="{_ring_path_d(ring, inset_project)}"/>'
        for rings in groups.values()
        for ring in rings
    ]
    # 金門在南、馬祖在北——用各自群組的緯度平均值判斷標籤擺放，不寫死座標。
    kinmen_y = inset_project(
        sum(lat for r in counties["kinmen"]["金門縣"] for _, lat in r) / sum(len(r) for r in counties["kinmen"]["金門縣"]),
        118.3,
    )[1]
    matsu_y = inset_project(
        sum(lat for r in counties["matsu"]["連江縣"] for _, lat in r) / sum(len(r) for r in counties["matsu"]["連江縣"]),
        119.9,
    )[1]
    labels = (
        f'<text x="4" y="{max(10, min(inset_h - 4, kinmen_y - 6)):.0f}" class="inset-label">金門</text>'
        f'<text x="4" y="{max(10, min(inset_h - 4, matsu_y - 6)):.0f}" class="inset-label">馬祖</text>'
    )
    return (
        f'<g class="map-inset" transform="translate(14,{MAP_VIEWBOX_H - inset_h - 14})">'
        f'<rect class="inset-frame" x="-4" y="-4" width="{inset_w + 8}" height="{inset_h + 8}"/>'
        f'<text x="4" y="12" class="inset-title">金門・馬祖（不同比例尺）</text>'
        f'<g transform="translate(0,16)">{"".join(shapes)}{labels}</g>'
        "</g>"
    )


def render_map_svg(pins: list[dict] | None) -> str:
    """組出完整 map tab 的 <svg>（底圖＋縣市界線＋pin 圖層＋離島 inset）。
    pins 為 None 時只出底圖＋縣市線（地圖仍看得見，只是還沒有點——資料 agent
    交檔前的過渡態）。"""
    base_body = extract_map_base_svg()
    counties = load_counties_data()
    county_layer = render_county_lines(counties)
    inset_layer = render_map_inset(counties)
    pin_layer = ""
    if pins:
        clusters = cluster_pins(pins)
        parts = []
        for c in clusters:
            x, y = round(c["x"], 1), round(c["y"], 1)
            if len(c["items"]) == 1:
                p = c["items"][0]
                cls = "pin-person" if p["type"] == "person" else "pin-place"
                shape = (
                    f'<circle class="pin-shape" cx="{x}" cy="{y}" r="6"/>'
                    if p["type"] == "person"
                    else f'<rect class="pin-shape" x="{x-6}" y="{y-6}" width="12" height="12"/>'
                )
                href = esc(p["link"])
                external = p["link"].startswith(("http://", "https://"))
                target = ' target="_blank" rel="noopener"' if external else ""
                title = f'{esc(p["name"])} — {esc(p["hook"])}'
                parts.append(
                    f'<a class="pin {cls}" href="{href}"{target} '
                    f'aria-label="{esc(p["name"])}，{esc(p["county"])}">'
                    f"{shape}<title>{title}</title></a>"
                )
            else:
                items_attr = esc(
                    "|".join(f'{p["type"]}::{p["name"]}::{p["hook"]}::{p["link"]}' for p in c["items"])
                )
                parts.append(
                    f'<g class="pin-cluster" tabindex="0" role="button" '
                    f'aria-label="{len(c["items"])} 筆，點擊展開" data-items="{items_attr}">'
                    f'<circle class="pin-shape" cx="{x}" cy="{y}" r="10"/>'
                    f'<text x="{x}" y="{y}" class="pin-cluster-n">{len(c["items"])}</text>'
                    "</g>"
                )
        pin_layer = f'<g class="map-pins">{"".join(parts)}</g>'
    return (
        f'<svg class="tw-map" viewBox="0 0 {MAP_VIEWBOX_W} {MAP_VIEWBOX_H}" '
        f'role="img" aria-label="臺灣藝文地圖">{base_body}{county_layer}{pin_layer}{inset_layer}</svg>'
    )


MAP_POPUP_SCRIPT = """  <script>
    // 地圖聚合圓點的展開面板（純 vanilla JS，無外部庫）。點擊 .pin-cluster
    // 讀 data-items（"type::name::hook::link" 以 | 分隔多筆）列成清單，
    // 用 getScreenCTM() 把 SVG 內部座標換算成畫面座標來定位面板
    // （viewBox 縮放/RWD 下仍準確）。
    (function () {
      var svg = document.querySelector('svg.tw-map');
      if (!svg) return;
      var popup = document.getElementById('map-popup');
      function closePopup() { popup.hidden = true; popup.innerHTML = ''; }
      svg.querySelectorAll('.pin-cluster').forEach(function (g) {
        function open() {
          var items = g.getAttribute('data-items').split('|').map(function (s) {
            var parts = s.split('::');
            return { type: parts[0], name: parts[1], hook: parts[2], link: parts[3] };
          });
          var html = '<button type="button" class="map-popup-close" aria-label="關閉">×</button><ul>';
          items.forEach(function (it) {
            var ext = /^https?:\\/\\//.test(it.link);
            html += '<li><a href="' + it.link + '"' + (ext ? ' target="_blank" rel="noopener"' : '') +
              '><span class="mp-name">' + it.name + '</span><span class="mp-hook">' + it.hook + '</span></a></li>';
          });
          html += '</ul>';
          popup.innerHTML = html;
          var pt = svg.createSVGPoint();
          var cx = parseFloat(g.querySelector('circle').getAttribute('cx'));
          var cy = parseFloat(g.querySelector('circle').getAttribute('cy'));
          pt.x = cx; pt.y = cy;
          var screenPt = pt.matrixTransform(svg.getScreenCTM());
          popup.style.left = (screenPt.x + window.scrollX + 12) + 'px';
          popup.style.top = (screenPt.y + window.scrollY - 10) + 'px';
          popup.hidden = false;
          popup.querySelector('.map-popup-close').addEventListener('click', closePopup);
        }
        g.addEventListener('click', open);
        g.addEventListener('keydown', function (e) {
          if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); }
        });
      });
      document.addEventListener('click', function (e) {
        if (!popup.hidden && !popup.contains(e.target) && !e.target.closest('.pin-cluster')) closePopup();
      });
    })();
  </script>"""


def build_map_tab() -> tuple[str, str]:
    """首頁第 4 個 tab「臺灣藝文地圖」。回傳 (tab-panel HTML, 互動用 <script>)——
    分開回傳是為了讓 <script> 放在 </main> 之後、與既有 hash-router script
    同一層級，而不是嵌在 <main> 裡面（風格與其他 script 放置一致）。
    content/map.yaml 缺席時顯示占位文字、不 crash（資料 agent 交檔前的過渡態，
    2026-07-18）；有資料則出底圖＋pin＋圖例＋聚合展開面板。"""
    pins = load_map_pins()
    if pins is None:
        body = '      <p class="map-empty">臺灣藝文地圖資料尚未上架。</p>'
        script = ""
    else:
        body = (
            '      <div class="map-legend">\n'
            '        <span class="legend-item"><span class="legend-swatch legend-person"></span>人物</span>\n'
            '        <span class="legend-item"><span class="legend-swatch legend-place"></span>地標</span>\n'
            "      </div>\n"
            '      <div class="map-svg-wrap">\n'
            f"        {render_map_svg(pins)}\n"
            "      </div>\n"
            '      <div id="map-popup" class="map-popup" hidden></div>'
        )
        script = MAP_POPUP_SCRIPT
    panel = (
        '    <section class="tab-panel" data-panel="map" role="tabpanel">\n'
        f"{body}\n"
        "    </section>"
    )
    return panel, script


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
    <button type="button" class="geo-tab" data-tab="map" role="tab" aria-selected="false">臺灣藝文地圖</button>
    <button type="button" class="geo-tab" data-tab="songs" role="tab" aria-selected="false">臺灣歌曲</button>
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

    <!-- 臺灣藝文地圖（Phase 2B，2026-07-18）：底圖＋pin 自 content/map.yaml 產生 -->
{map_panel}

    <!-- 臺灣歌曲（S1 基建，2026-07-18）：時代卡自 content/songs/era-*.md 產生 -->
{songs_panel}
  </main>

{script}
{map_script}
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


def build_index(eras: list[dict]) -> str:
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

    map_panel, map_script = build_map_tab()
    return INDEX_PAGE.format(
        title=esc(fm["title"]),
        description=esc(fm["description"]),
        site_title=esc(fm["site_title"]),
        site_sub=esc(fm["site_sub"]),
        intro="\n".join(intro_parts),
        filters=build_field_filters(fm["people"]),
        cards="\n".join(cards),
        field_cards=build_field_cards(),
        map_panel=map_panel,
        map_script=map_script,
        songs_panel=build_songs_tab(eras),
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


# ---------- 臺灣歌曲線（S1 基建，2026-07-18；SSOT＝docs/SONGS-SPEC.md） ----------
#
# 資料模型：content/songs/era-<slug>.md（八個時代頁敘事）＋ era-<slug>.yaml
# （該期歌曲登記簿分片，見 SONGS-SPEC §2）。schema／孤兒歌名驗證交給
# scripts/check_songs.py（本檔頂部 `import check_songs`），main() 開頭以
# --no-net 模式呼叫、fail-fast；完整連結驗證是部署前另跑的一關，不在這裡做
# （分工說明見 check_songs.py 檔頭）。
#
# 零內容過渡態（content/songs/ 不存在或全空）：load_era_pages() 回傳 []，
# 首頁 tab 與歌曲頁生成都優雅跳過，不 crash（同地圖 tab 前例）。部分內容
# （只有幾期）：只出已有的時代卡／時代頁，不強求 8 期齊全——check_songs.py
# 已保證「有 MD 就有分片、有分片就有 MD」，這裡不重複驗證。

_SONG_TITLE_RE = re.compile(r"《([^》]+)》")
_EXISTING_LINK_RE = re.compile(r"\[([^\]]+)\]\(((?:[^()\s]|\([^()]*\))*)\)")


def load_era_pages() -> list[dict]:
    """讀 content/songs/era-*.md（含對應 era-*.yaml 分片），依 frontmatter
    `order` 排序回傳。每項 {"md_path", "fm", "body", "songs"}，songs 為該期
    歌曲（依 year 升冪排序）。缺 content/songs/ 或無檔案 → 回傳 []。"""
    if not SONGS.is_dir():
        return []
    era_md = sorted(SONGS.glob("era-*.md"))
    if not era_md:
        return []
    eras: list[dict] = []
    for md_path in era_md:
        fm, body = split_frontmatter(md_path)
        for key in ("title", "slug", "period", "order", "axis"):
            if key not in fm:
                die(f"{md_path.name}：frontmatter 缺 `{key}`")
        if fm["slug"] != md_path.stem:
            die(f"{md_path.name}：frontmatter slug（{fm['slug']}）與檔名不一致")
        yaml_path = SONGS / f"{fm['slug']}.yaml"
        if not yaml_path.is_file():
            die(f"{md_path.name}：缺對應登記簿 {yaml_path.name}（check_songs.py 應已攔下，這裡是保險）")
        shard = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        songs = shard.get("songs") or []
        if not songs:
            die(f"{yaml_path.name}：登記簿至少要有 1 首歌")
        eras.append({
            "md_path": md_path,
            "fm": fm,
            "body": body,
            "songs": sorted(songs, key=lambda s: s.get("year", 0)),
        })
    eras.sort(key=lambda e: e["fm"]["order"])
    return eras


def build_songs_by_title(eras: list[dict]) -> dict[str, dict]:
    """全部分片 title → 歌曲物件的對照表，供正文《歌名》自動掛鏈與歌單區使用。
    同名異曲（理論上不應發生，SONGS-SPEC §6：靠登記簿 note 標注、正文人工
    指定，不硬猜）以先出現者為準，不 die——check_songs.py 不視同名為 schema
    錯誤，這裡也不擋。"""
    by_title: dict[str, dict] = {}
    for era in eras:
        for song in era["songs"]:
            by_title.setdefault(song["title"], song)
    return by_title


def _autolink_plain(segment: str, songs_by_title: dict[str, dict]) -> str:
    """在一段「保證不在既有 markdown 連結內」的原始文字裡，把《歌名》包成
    `[《歌名》](listen[0] url)`（markdown 連結語法，交給 inline() 走既有的
    esc()／連結轉換管線，不在這裡直接產 HTML）。未命中登記簿的歌名原樣保留
    純文字——孤兒偵測是 check_songs.py 的事，這裡不猜、不報錯。"""
    def repl(m: re.Match) -> str:
        title = m.group(1)
        song = songs_by_title.get(title)
        if song is None:
            return m.group(0)
        url = song["listen"][0]["url"]
        return f"[《{title}》]({url})"
    return _SONG_TITLE_RE.sub(repl, segment)


def autolink_song_titles(text: str, songs_by_title: dict[str, dict]) -> str:
    """時代頁正文《歌名》自動掛鏈（SONGS-SPEC §2.1／§6）：掃描與登記簿 title
    精確比對命中者，包成連結；不動已經在 `[label](url)` markdown 連結內的
    文字（先切出既有連結區段原樣保留，只對連結以外的區段做《…》替換）。"""
    out: list[str] = []
    last = 0
    for m in _EXISTING_LINK_RE.finditer(text):
        out.append(_autolink_plain(text[last:m.start()], songs_by_title))
        out.append(m.group(0))
        last = m.end()
    out.append(_autolink_plain(text[last:], songs_by_title))
    return "".join(out)


def render_era_prose(blocks: list[str], songs_by_title: dict[str, dict], path: Path) -> str:
    """時代頁正文受限子集（SONGS-SPEC §2.1）：段落／`- `／`1. ` 清單／`> `
    引用／`[label](url)` 連結／`[^N]` 腳註。每個文字塊先做《歌名》自動掛鏈
    再進 inline()。不重用人物頁 render_bio()——那支服務 timeline／portrait，
    時代頁不需要。"""
    parts: list[str] = []
    for block in blocks:
        lines = block.splitlines()
        if lines and all(l.startswith("> ") for l in lines):
            text = " ".join(l[2:] for l in lines)
            parts.append(f"      <blockquote>{inline(autolink_song_titles(text, songs_by_title))}</blockquote>")
            continue
        listing = parse_list_items(block)
        if listing:
            kind, items = listing
            lis = "\n".join(
                f"        <li>{inline(autolink_song_titles(item, songs_by_title))}</li>" for item in items
            )
            parts.append(f"      <{kind}>\n{lis}\n      </{kind}>")
            continue
        parts.append(f"      <p>{inline(autolink_song_titles(block, songs_by_title))}</p>")
    return "\n".join(parts)


def render_era_body(body: str, songs_by_title: dict[str, dict], path: Path) -> tuple[str, str]:
    """回傳 (content_html, footnotes_html)。格式同 field 頁新格式（可選導言＋
    `## ` 區段序列，最後一區固定「出處」）；時代頁不像 field 頁把出處設為選配
    ——SONGS-SPEC §2.1 規定每頁至少 4 條出處，這裡直接要求「出處」為必要的
    最後一個區段。"""
    m = re.search(r"^## ", body, re.M)
    if m is None:
        die(f"{path.name}：時代頁正文須以 `## ` 區段組織（SONGS-SPEC §2.1）")
    intro_paras = split_paragraphs(body[: m.start()])
    intro_html = "\n".join(
        f"      <p>{inline(autolink_song_titles(p, songs_by_title))}</p>" for p in intro_paras
    )
    sections = split_sections(body[m.start():], path)
    if not sections:
        die(f"{path.name}：時代頁內容為空")
    if sections[-1][0] != "出處":
        die(f"{path.name}：時代頁最後一個 `## ` 區段須為「出處」（實得「{sections[-1][0]}」）")
    footnotes_html = render_footnotes(sections[-1][1], path)
    content_sections = sections[:-1]
    if not content_sections and not intro_html:
        die(f"{path.name}：時代頁除「出處」外沒有任何內容區段")

    parts: list[str] = []
    if intro_html:
        parts.append('    <section class="person-sec">\n' + intro_html + "\n    </section>")
    for heading, blocks in content_sections:
        parts.append(
            '    <section class="person-sec">\n'
            f"      <h2>{esc(heading)}</h2>\n"
            f"{render_era_prose(blocks, songs_by_title, path)}\n"
            "    </section>"
        )
    return "\n".join(parts), footnotes_html


# credits 欄位 → 中文標籤；未列在此表的欄位仍會照樣輸出（key 本身當標籤），
# 避免登記簿以後新增欄位卻在頁面上悄悄消失（SONGS-SPEC §2.2 credits 是開放欄位）。
CREDIT_LABELS = {"lyricist": "詞", "composer": "曲", "original_singer": "唱"}


def render_song_item(song: dict) -> str:
    """歌單區單一 `<li>`：歌名本身＝listen[0] 主連結（target="_blank"
    rel="noopener"）＋年份／語言／詞曲唱＋hook＋禁歌標記（有 banned 才出）＋
    listen[1..] 以「另聽：label」小字列出（SONGS-SPEC §6）。"""
    listen = song["listen"]
    main_url = esc(listen[0]["url"])
    title_link = (
        f'<a class="song-title" href="{main_url}" target="_blank" rel="noopener">'
        f'{esc(song["title"])}</a>'
    )
    credits = song.get("credits") or {}
    credit_bits = [
        f"{label}：{esc(credits[key])}" for key, label in CREDIT_LABELS.items() if key in credits
    ]
    credit_bits += [
        f"{esc(key)}：{esc(value)}" for key, value in credits.items() if key not in CREDIT_LABELS
    ]
    meta_bits = [esc(str(song["year"])), esc(song["language"])] + credit_bits
    meta_line = " · ".join(meta_bits)

    lines = [
        '        <li class="song-item">',
        f'          <p class="song-head">{title_link}</p>',
        f'          <p class="song-meta">{meta_line}</p>',
        f'          <p class="song-hook">{esc(song["hook"])}</p>',
    ]
    if song.get("banned"):
        lines.append(f'          <p class="song-banned">禁歌：{esc(song["banned"])}</p>')
    if len(listen) > 1:
        also = "、".join(
            f'<a href="{esc(l["url"])}" target="_blank" rel="noopener">{esc(l["label"])}</a>'
            for l in listen[1:]
        )
        lines.append(f'          <p class="song-also">另聽：{also}</p>')
    lines.append("        </li>")
    return "\n".join(lines)


def render_era_nav(eras: list[dict], idx: int) -> str:
    """頁尾上一期／下一期導覽（依 order，即 eras 清單順序）；同 pages/ 目錄下
    的相對連結（`<slug>.html`，與 field 頁人物卡連結同款寫法）。"""
    parts: list[str] = []
    if idx > 0:
        prev_slug = eras[idx - 1]["fm"]["slug"]
        prev_title = eras[idx - 1]["fm"]["title"]
        parts.append(f'      <a class="era-prev" href="song-{esc(prev_slug)}.html">← 上一期：{esc(prev_title)}</a>')
    else:
        parts.append('      <span class="era-prev era-nav-empty"></span>')
    if idx < len(eras) - 1:
        next_slug = eras[idx + 1]["fm"]["slug"]
        next_title = eras[idx + 1]["fm"]["title"]
        parts.append(f'      <a class="era-next" href="song-{esc(next_slug)}.html">下一期：{esc(next_title)} →</a>')
    else:
        parts.append('      <span class="era-next era-nav-empty"></span>')
    return "\n".join(parts)


def build_songs_tab(eras: list[dict]) -> str:
    """首頁第 5 個 tab「臺灣歌曲」：時代卡（期名＋年代＋axis 主軸句），依
    order 排，連到 pages/song-<slug>.html。eras 為 [] 時顯示占位文字、不
    crash（零內容過渡態，SONGS-SPEC §6）；部分內容時只出已有的時代卡。"""
    if not eras:
        return (
            '    <section class="tab-panel" data-panel="songs" role="tabpanel">\n'
            '      <p class="songs-empty">臺灣歌曲時代頁尚未上架。</p>\n'
            "    </section>"
        )
    cards = []
    for era in eras:
        fm = era["fm"]
        cards.append(
            f'        <a class="person-card era-card" href="pages/song-{esc(fm["slug"])}.html">\n'
            f'          <span class="pc-name">{esc(fm["title"])}</span>'
            f'<span class="pc-years">{esc(fm["period"])}</span>\n'
            f'          <span class="pc-field">{esc(fm["axis"])}</span>\n'
            "        </a>"
        )
    return (
        '    <section class="tab-panel" data-panel="songs" role="tabpanel">\n'
        '      <div class="person-cards" id="era-cards">\n'
        + "\n".join(cards) + "\n"
        "      </div>\n"
        "    </section>"
    )


SONG_ERA_PAGE = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} — 臺灣歌曲 — 臺灣人文藝術</title>
  <link rel="stylesheet" href="../assets/css/style.css">
</head>
<body>
  <nav class="crumbs"><a href="../index.html#songs">← 臺灣歌曲</a> · <a href="../index.html#general">回首頁</a></nav>

  <div class="page-wrap">
    <header class="page-header era-hero">
      <div class="eyebrow">臺灣歌曲 · {period}</div>
      <h1>{title}</h1>
    </header>

{content}

    <section class="person-sec songs-of-era">
      <h2>這個時代的歌</h2>
      <ul class="song-list">
{song_items}
      </ul>
    </section>

    <section class="footnotes">
      <h2>出處</h2>
      <ol>
{footnotes}
      </ol>
    </section>

    <nav class="era-nav">
{era_nav}
    </nav>

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


def build_song_pages(eras: list[dict]) -> int:
    """content/songs/era-*.{md,yaml} → _build/pages/song-<slug>.html。eras 為
    [] 時（零內容過渡態）跳過、不 die。回傳實際產出頁數。"""
    if not eras:
        print("[build_pages] content/songs/ 尚無時代頁，跳過歌曲線頁面生成")
        return 0
    songs_by_title = build_songs_by_title(eras)
    count = 0
    for idx, era in enumerate(eras):
        md_path, fm = era["md_path"], era["fm"]
        content_html, footnotes_html = render_era_body(era["body"], songs_by_title, md_path)
        song_items = "\n".join(render_song_item(s) for s in era["songs"])
        page_html = SONG_ERA_PAGE.format(
            title=esc(fm["title"]),
            period=esc(fm["period"]),
            content=content_html,
            song_items=song_items,
            footnotes=footnotes_html,
            era_nav=render_era_nav(eras, idx),
            license_line="本頁由本資料庫依公開來源原創編寫（逐條見「出處」），以 CC BY-SA 4.0 釋出。",
            credit=FIELD_CREDIT,
        )
        slug = fm["slug"]
        (BUILD / "pages" / f"song-{slug}.html").write_text(page_html, encoding="utf-8")
        print(f"[build_pages] pages/song-{slug}.html ✓（歌曲線）")
        count += 1
    return count


def main() -> None:
    if not CONTENT.is_dir():
        die("content/ 不存在")
    people_md = sorted((CONTENT / "people").glob("*.md"))
    if not people_md:
        die("content/people/ 底下沒有任何 .md")

    # 歌曲線 schema／孤兒歌名 fail-fast（--no-net：不打連結，完整連結驗證是
    # 部署前另跑的一關，見 check_songs.py 檔頭分工說明）。
    songs_errors = check_songs.validate(no_net=True)
    if songs_errors:
        die(
            "check_songs 驗證未通過（--no-net）：\n"
            + "\n".join(f"  - {e}" for e in songs_errors)
        )

    (BUILD / "pages").mkdir(parents=True, exist_ok=True)

    people_meta = load_people_meta(people_md)
    eras = load_era_pages()

    index_html = build_index(eras)
    (BUILD / "index.html").write_text(index_html, encoding="utf-8")
    print(f"[build_pages] index.html ✓")

    for md_path in people_md:
        slug, page_html = build_person(md_path)
        if slug != md_path.stem:
            die(f"{md_path.name}：frontmatter slug（{slug}）與檔名不一致")
        (BUILD / "pages" / f"{slug}.html").write_text(page_html, encoding="utf-8")
        print(f"[build_pages] pages/{slug}.html ✓")

    field_count = build_fields(people_meta)
    song_count = build_song_pages(eras)

    print(
        f"[build_pages] 完成：{len(people_md) + 1 + field_count + song_count} 頁 → "
        f"{BUILD.relative_to(ROOT)}/"
    )


if __name__ == "__main__":
    main()
