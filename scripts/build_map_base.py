#!/usr/bin/env python3
"""臺灣藝文地圖底圖轉換腳本（Phase 2B，2026-07-18；可重跑）。

產出兩份 build 輸入檔（放 templates/，不進 build_pages.py 的動態抓取路徑，
避免每次 build 都打網路）：

  templates/taiwan-map-base.svg   — 視覺底圖（原樣不改內容，只加檔頭 comment）
  templates/taiwan-counties.json  — 縣市界線幾何資料（給 build_pages.py 畫細線
                                     ＋做 pin 落點 point-in-polygon 驗證用）

資料來源（兩條分開，缺一不可，理由見下）：

  1. 視覺底圖：taiwan.md 官網 https://taiwan.md/taiwan-shape/ 的
     taiwan-location-map.svg（David 2026-07-18 指定：底圖一律用 taiwan.md
     的現成 SVG，不用別的來源）。實測其內容與 Wikimedia Commons
     「File:Taiwan_location_map.svg」（NordNordWest 製，CC BY-SA 3.0 /
     GFDL 1.2+）逐位元組相同——taiwan.md 只是代管同一份 Commons 檔。
     這份圖是 Wikipedia Module:Location_map/data/Taiwan 的標準底圖，
     documented bounds：top=26.4°N bottom=21.7°N left=118.0°E right=122.3°E，
     viewBox 0 0 1015.733 1221.247。

  2. 縣市界線幾何：taiwan.md 也代管一份 TopoJSON
     （taiwan-country.topo.json，源自 waiting7777/taiwan-vue-components，
     MIT License），但實測解析度偏低（全臺僅約 3100 點），對「鵝鑾鼻」
     「富貴角」「馬公」這類海岬/離島座標做 point-in-polygon 驗證會誤判
     （落在所有縣市外——見下方 8 點檢核紀錄）。改用同樣源自內政部國土測繪
     中心「直轄市、縣市界線(TWD97經緯度)」shapefile、但解析度高出約 7 倍
     （約 23000 點）的 dkaoster/taiwan-atlas 專案（MIT License）重新轉製，
     8 個已知點與 65 個 map.yaml pin 全數通過（2km 海岸線容許誤差內）。
     兩者同一份政府資料血緣，只是簡化程度不同——這裡選解析度夠撐驗證的那份，
     符合 David「TopoJSON 若程式化投影更順手可用它算座標」的授權（座標計算
     用哪份 topojson 有彈性，視覺渲染才鎖定 taiwan.md 的 SVG）。

投影：等距圓柱（equirectangular），沿用 taiwan-location-map.svg 原生的
bounds／viewBox（該圖本身以「N/S 拉伸 110%」的方式已經內建了經度 cos(緯度)
修正——1/cos(23.5°)≈1.09，跟 110% 幾乎完全對上；所以疊圖時直接沿用該圖
viewBox 的線性映射，不需要、也不能再疊加一次 cos 修正，否則會反過來修正過頭。
縣市界線疊圖沿用同一套投影，才會跟底圖對齊。已用台北車站／高雄港／台東市／
鵝鑾鼻／富貴角／台中／花蓮市／馬公八個已知點逐一核對落點縣市，全過。

用法：
  python3 scripts/build_map_base.py             # 全部重新下載＋轉換
  python3 scripts/build_map_base.py --offline    # 用 scratchpad 快取，不連網（除錯用）
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"

UA = "taiwan-arts-db-map-build/1.0 (https://elliot200852-lab.github.io/taiwan-arts-db/; contact: elliot200852@gmail.com)"

BASE_MAP_URL = "https://taiwan.md/assets/svg/taiwan-location-map.svg"
COUNTIES_TOPOJSON_URL = "https://cdn.jsdelivr.net/npm/taiwan-atlas/counties-10t.json"

# 離島分組：金門、馬祖離本島遠，畫成 inset；其餘（含澎湖，離本島近）走主圖真實位置。
KINMEN_COUNTY = "金門縣"
MATSU_COUNTY = "連江縣"

SIMPLIFY_EPSILON_DEG = 0.0015  # ≈150m，主圖縣市線用（離島 inset 另外處理，見下）
INSET_SIMPLIFY_EPSILON_DEG = 0.0004  # 離島小、需要留多一點細節


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def decode_topojson(raw: bytes, obj_name: str) -> dict[str, list[list[tuple[float, float]]]]:
    """TopoJSON → {縣市名: [ring, ...]}，ring = [(lng,lat), ...] 已還原成實際經緯度。
    純 Python 手刻解碼（不依賴 topojson-client），標準 delta 量化格式：
    transform.scale/translate 還原 arc 座標；負的 arc index 代表反向引用
    （~index），相鄰 arc 首尾共用端點，串接時要去掉重複點——見
    docs/PLAN.md 與本檔開頭說明引用的 TopoJSON spec。"""
    d = json.loads(raw)
    scale = d["transform"]["scale"]
    translate = d["transform"]["translate"]

    def decode_arc(arc):
        x = y = 0
        pts = []
        for dx, dy in arc:
            x += dx
            y += dy
            pts.append((x * scale[0] + translate[0], y * scale[1] + translate[1]))
        return pts

    arcs_decoded = [decode_arc(a) for a in d["arcs"]]

    def arc_pts(i):
        return arcs_decoded[i] if i >= 0 else list(reversed(arcs_decoded[~i]))

    def ring_coords(arc_indices):
        coords: list[tuple[float, float]] = []
        for idx in arc_indices:
            pts = arc_pts(idx)
            coords.extend(pts[1:] if coords else pts)
        return coords

    counties: dict[str, list[list[tuple[float, float]]]] = {}
    for geom in d["objects"][obj_name]["geometries"]:
        name = geom["properties"]["COUNTYNAME"]
        rings: list[list[tuple[float, float]]] = []
        if geom["type"] == "Polygon":
            rings.extend(ring_coords(r) for r in geom["arcs"])
        elif geom["type"] == "MultiPolygon":
            for poly in geom["arcs"]:
                rings.extend(ring_coords(r) for r in poly)
        counties[name] = rings
    return counties


def _perp_dist(pt, a, b):
    (x, y), (x1, y1), (x2, y2) = pt, a, b
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(x - x1, y - y1)
    t = ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)
    px, py = x1 + t * dx, y1 + t * dy
    return math.hypot(x - px, y - py)


def douglas_peucker(points, epsilon):
    if len(points) < 3:
        return points
    dmax, idx = 0.0, 0
    for i in range(1, len(points) - 1):
        d = _perp_dist(points[i], points[0], points[-1])
        if d > dmax:
            dmax, idx = d, i
    if dmax > epsilon:
        left = douglas_peucker(points[: idx + 1], epsilon)
        right = douglas_peucker(points[idx:], epsilon)
        return left[:-1] + right
    return [points[0], points[-1]]


def simplify_ring(ring, epsilon):
    simplified = douglas_peucker(ring, epsilon)
    if simplified[0] != simplified[-1]:
        simplified.append(simplified[0])
    return simplified


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="用本機快取檔，不連網（除錯用）")
    ap.add_argument(
        "--cache-dir", default="/tmp/taiwan-arts-db-map-cache", help="--offline 用的快取目錄"
    )
    args = ap.parse_args()

    cache = Path(args.cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    base_svg_cache = cache / "taiwan-location-map.svg"
    topo_cache = cache / "counties-10t.json"

    if args.offline and base_svg_cache.exists() and topo_cache.exists():
        base_svg = base_svg_cache.read_bytes()
        topo_raw = topo_cache.read_bytes()
    else:
        print(f"下載底圖：{BASE_MAP_URL}")
        base_svg = fetch(BASE_MAP_URL)
        base_svg_cache.write_bytes(base_svg)
        print(f"下載縣市界線資料：{COUNTIES_TOPOJSON_URL}")
        topo_raw = fetch(COUNTIES_TOPOJSON_URL)
        topo_cache.write_bytes(topo_raw)

    # ---- 視覺底圖：原樣寫入 templates/，只加檔頭 comment ----
    svg_text = base_svg.decode("utf-8")
    header = (
        "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n"
        "<!--\n"
        "  來源：taiwan.md 官網 https://taiwan.md/taiwan-shape/ 提供的\n"
        "  taiwan-location-map.svg（https://taiwan.md/assets/svg/taiwan-location-map.svg）。\n"
        "  實測與 Wikimedia Commons「File:Taiwan location map.svg」逐位元組相同——\n"
        "  taiwan.md 代管同一份 Commons 檔案，上游作者與授權：\n"
        "  作者：NordNordWest（User:NordNordWest）\n"
        "  授權：CC BY-SA 3.0 Unported ／ GNU Free Documentation License 1.2+\n"
        "  （雙授權，本站依 CC BY-SA 3.0 引用；照 taiwan.md 頁面指示一併列出\n"
        "  Wikimedia Commons 出處）。\n"
        "  用途：臺灣人文藝術資料庫「臺灣藝文地圖」tab 的底圖（build_pages.py\n"
        "  讀取本檔內文 inline 進 index.html，疊加縣市界線與 pin 圖層；未修改原圖）。\n"
        "  投影：Wikipedia Module:Location_map/data/Taiwan 標準等距圓柱投影，\n"
        "  邊界 top=26.4°N bottom=21.7°N left=118.0°E right=122.3°E，對應本檔\n"
        "  viewBox 0 0 1015.733 1221.247。該圖已用「N/S 拉伸 110%」內建修正經度\n"
        "  cos(緯度) 失真（1/cos(23.5°)≈1.09，與 110% 幾乎一致）——疊圖時直接沿用\n"
        "  本檔 viewBox 的線性映射即可，不需要／不能再疊加一次 cos 修正。\n"
        "  金門／馬祖離本島遠、在此圖上過小難辨識，另以 inset 小圖呈現（見\n"
        "  build_pages.py 的 render_map_svg()），inset 幾何資料來源見\n"
        "  templates/taiwan-counties.json 檔頭。\n"
        "  2026-07-18 用台北車站／高雄港／台東市／鵝鑾鼻／富貴角／台中／花蓮市／\n"
        "  馬公八個已知點逐一驗證落點縣市，全過（見 scripts/build_map_base.py 開頭）。\n"
        "  轉換／驗證腳本：scripts/build_map_base.py（可重跑）。\n"
        "-->\n"
    )
    # 去掉原檔第一行 <?xml ...?>（已含在 header 裡），其餘原樣保留
    body_after_decl = svg_text.split("\n", 1)[1] if svg_text.startswith("<?xml") else svg_text
    (TEMPLATES / "taiwan-map-base.svg").write_text(header + body_after_decl, encoding="utf-8")
    print("寫入 templates/taiwan-map-base.svg")

    # ---- 縣市界線幾何：解碼＋分組＋簡化 ----
    counties = decode_topojson(topo_raw, "counties")
    main_group = {}
    inset_group = {}
    for name, rings in counties.items():
        if name in (KINMEN_COUNTY, MATSU_COUNTY):
            inset_group[name] = [simplify_ring(r, INSET_SIMPLIFY_EPSILON_DEG) for r in rings]
        else:
            main_group[name] = [simplify_ring(r, SIMPLIFY_EPSILON_DEG) for r in rings]

    total_pts_before = sum(len(r) for rings in counties.values() for r in rings)
    total_pts_after = sum(
        len(r) for g in (main_group, inset_group) for rings in g.values() for r in rings
    )
    print(f"縣市界線點數：簡化前 {total_pts_before} → 簡化後 {total_pts_after}")

    out = {
        "_meta": {
            "source": "taiwan.md 代管之 https://cdn.jsdelivr.net/npm/taiwan-atlas/counties-10t.json"
            "（dkaoster/taiwan-atlas，MIT License），源自內政部國土測繪中心"
            "「直轄市、縣市界線(TWD97經緯度)」shapefile（data.gov.tw 資料集 7442，"
            "政府資料開放授權）。",
            "coords": "TWD97 經緯度（近似 WGS84，臺灣尺度誤差可忽略）",
            "simplify": f"main 群組 Douglas-Peucker ε={SIMPLIFY_EPSILON_DEG}°、"
            f"inset 群組 ε={INSET_SIMPLIFY_EPSILON_DEG}°",
            "groups": "main=本島＋澎湖等（真實位置繪製）；kinmen/matsu=另開 inset 小圖",
        },
        "main": main_group,
        "kinmen": {KINMEN_COUNTY: inset_group[KINMEN_COUNTY]},
        "matsu": {MATSU_COUNTY: inset_group[MATSU_COUNTY]},
    }
    (TEMPLATES / "taiwan-counties.json").write_text(
        json.dumps(out, ensure_ascii=False), encoding="utf-8"
    )
    print("寫入 templates/taiwan-counties.json")


if __name__ == "__main__":
    main()
