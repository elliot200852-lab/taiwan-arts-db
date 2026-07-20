#!/usr/bin/env python3
"""部署後逐檔驗證 live 站（Drive-pull 架構的驗收閘）。

為什麼需要這支：內容完全不在 repo，CI 綠只代表「當下從 Drive 拉到了東西」，
不代表使用者看到的站是完整的——唯一能證明部署成功的是對 live 站逐檔抓。
每次內容更新部署後都要跑，全綠才算完成。

做法：抓 live 首頁 → 解析所有內部連結頁（*.html）→ 逐頁抓回，再從每頁
收集 <img src> → 逐一 HTTP 檢查：
  頁面：HTTP 200 ＋ Content-Type 含 text/html
  圖片：HTTP 200 ＋ body 大小 > 0（GitHub Pages 404 頁是 HTML，大小驗不如
        geo-db 有本機檔可比對，故以「抓得到且非空」為準）

2026-07-20 補洞（此前完全沒驗過，站台可能 CSS/JS 404、檢索索引壞掉但
verify_live 仍全綠）：
  CSS／JS：從首頁與所有已抓回的頁面（含人物/領域/歌曲時代頁）收集
           <link rel="stylesheet"> 與 <script src> 的 URL（不限站內——
           Google Fonts 這類外部樣式表載不到，頁面字體一樣會壞），逐一驗
           HTTP 200 且不是誤抓到 HTML（404 頁多半是 HTML）。
  search-index.json：HTTP 200 ＋ json.loads() 可解析且頂層為 list——
           這是首頁站內檢索的資料來源，壞了検索功能會靜靜掛掉，
           使用者看不出來（沒有任何錯誤畫面），舊版完全沒驗過。

用法：
  python scripts/verify_live.py                 # 驗預設站台
  python scripts/verify_live.py --base <url>    # 換站台
"""
import argparse
import concurrent.futures as cf
import json
import re
import sys
import urllib.parse
import urllib.request

DEFAULT_BASE = "https://elliot200852-lab.github.io/taiwan-arts-db"
UA = {"User-Agent": "taiwan-arts-db-verify/1.0"}

HREF = re.compile(r'<a[^>]+href="([^"]+)"', re.IGNORECASE)
IMG_SRC = re.compile(r'<img[^>]+src="([^"]+)"', re.IGNORECASE)
# rel／href 屬性順序不拘（用 lookahead）——本站 head 裡 Google Fonts 那行
# 是「先 href 後 rel」：<link href="...css2?..." rel="stylesheet">。
LINK_STYLESHEET = re.compile(
    r'<link\b(?=[^>]*\brel="stylesheet")(?=[^>]*\bhref="([^"]+)")[^>]*>',
    re.IGNORECASE,
)
SCRIPT_SRC = re.compile(r'<script\b[^>]*\bsrc="([^"]+)"', re.IGNORECASE)


def fetch(url, timeout=30):
    req = urllib.request.Request(url, method="GET", headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.headers.get("Content-Type", ""), r.read()


def is_internal(url, base):
    return url.startswith(base.rstrip("/") + "/") or url == base.rstrip("/")


def normalize(link, page_url):
    """相對連結 → 絕對 URL，去 fragment／query。回傳 None 表示非站內資源。
    給頁面／圖片連結用——這兩者的 query string 從未帶語意。"""
    link = link.strip()
    if not link or link.startswith(("mailto:", "javascript:", "#", "data:")):
        return None
    absolute = urllib.parse.urljoin(page_url, link)
    absolute = absolute.split("#", 1)[0].split("?", 1)[0]
    return absolute


def normalize_asset(link, page_url):
    """相對連結 → 絕對 URL，只去 fragment、**保留 query**。CSS／JS 專用：
    Google Fonts 這類外部樣式表的 query string（`?family=...`）就是資源本體，
    用 normalize() 砍掉會把合法請求打成 400（2026-07-20 補洞時實測踩到）。"""
    link = link.strip()
    if not link or link.startswith(("mailto:", "javascript:", "#", "data:")):
        return None
    absolute = urllib.parse.urljoin(page_url, link)
    return absolute.split("#", 1)[0]


def check_page(url):
    try:
        status, ctype, body = fetch(url)
        if status != 200:
            return f"HTTP {status}"
        if "text/html" not in ctype:
            return f"content-type={ctype!r}（非 HTML）"
        return None
    except Exception as e:
        return f"{type(e).__name__}: {str(e)[:80]}"


def check_resource(url):
    """通用「非 HTML 200 且非空」檢查：圖片／CSS／JS 共用（原 check_img，
    2026-07-20 改名並擴大用途給 CSS/JS 共用，判斷邏輯完全不變）。"""
    try:
        status, ctype, body = fetch(url)
        if status != 200:
            return f"HTTP {status}"
        if "text/html" in ctype:
            return f"content-type={ctype!r}（拿到 HTML，可能是 404 頁）"
        if len(body) == 0:
            return "大小 0"
        return None
    except Exception as e:
        return f"{type(e).__name__}: {str(e)[:80]}"


def collect_asset_urls(html, page_url):
    """從一頁 HTML 收集 <link rel="stylesheet"> 與 <script src> 的 URL
    （相對→絕對）。不限站內：外部資源（Google Fonts）404 一樣會讓頁面壞掉，
    所以站內站外都驗。"""
    urls = set()
    for m in LINK_STYLESHEET.finditer(html):
        u = normalize_asset(m.group(1), page_url)
        if u:
            urls.add(u)
    for m in SCRIPT_SRC.finditer(html):
        u = normalize_asset(m.group(1), page_url)
        if u:
            urls.add(u)
    return urls


def check_search_index(url):
    """search-index.json：HTTP 200 ＋ json.loads() 可解析＋頂層為 list。
    回傳 (筆數, None) 或 (None, 錯誤訊息)。"""
    try:
        status, ctype, body = fetch(url)
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:80]}"
    if status != 200:
        return None, f"HTTP {status}"
    try:
        data = json.loads(body.decode("utf-8"))
    except Exception as e:
        return None, f"json.loads 失敗：{type(e).__name__}: {str(e)[:120]}"
    if not isinstance(data, list):
        return None, f"頂層不是 list（實得 {type(data).__name__}）"
    return len(data), None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=DEFAULT_BASE)
    args = ap.parse_args()
    base = args.base.rstrip("/")

    index_url = base + "/index.html"
    try:
        status, ctype, body = fetch(index_url)
    except Exception as e:
        print(f"✗ 首頁抓不到（{index_url}）：{e}", file=sys.stderr)
        sys.exit(1)
    if status != 200 or "text/html" not in ctype:
        print(f"✗ 首頁異常：HTTP {status}, content-type={ctype!r}", file=sys.stderr)
        sys.exit(1)
    index_html = body.decode("utf-8", errors="replace")

    # 首頁的內部連結頁
    pages = set()
    for m in HREF.finditer(index_html):
        url = normalize(m.group(1), index_url)
        if url and is_internal(url, base) and url.endswith(".html"):
            pages.add(url)
    print(f"首頁解析到 {len(pages)} 個內部頁面，開始驗 {base}", flush=True)

    # 首頁的圖片與 CSS／JS
    imgs = set()
    assets = collect_asset_urls(index_html, index_url)
    for m in IMG_SRC.finditer(index_html):
        url = normalize(m.group(1), index_url)
        if url and is_internal(url, base):
            imgs.add(url)

    bad = []
    page_bodies = {}
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(fetch, u): u for u in sorted(pages)}
        for fut in cf.as_completed(futs):
            u = futs[fut]
            try:
                status, ctype, body = fut.result()
                if status != 200:
                    bad.append((u, f"HTTP {status}"))
                elif "text/html" not in ctype:
                    bad.append((u, f"content-type={ctype!r}（非 HTML）"))
                else:
                    page_bodies[u] = body.decode("utf-8", errors="replace")
            except Exception as e:
                bad.append((u, f"{type(e).__name__}: {str(e)[:80]}"))

    # 逐頁再收圖片與 CSS/JS（頁面本體已經抓過，順手收集不必再多打一次網路；
    # 等於把 84 頁全收，比「只抽樣幾頁」更完整，且零額外 HTTP 成本）。
    for u, html in page_bodies.items():
        for m in IMG_SRC.finditer(html):
            url = normalize(m.group(1), u)
            if url and is_internal(url, base):
                imgs.add(url)
        assets |= collect_asset_urls(html, u)

    print(
        f"頁面 {len(pages)} 個驗畢（{len(bad)} 壞），"
        f"收集到 {len(imgs)} 張站內圖片、{len(assets)} 個 CSS/JS 資源",
        flush=True,
    )

    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(check_resource, u): u for u in sorted(imgs)}
        for i, fut in enumerate(cf.as_completed(futs), 1):
            u = futs[fut]
            err = fut.result()
            if err:
                bad.append((u, err))
            if i % 50 == 0:
                print(f"  … 圖片 {i}/{len(imgs)}（目前共 {len(bad)} 壞）", flush=True)

    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(check_resource, u): u for u in sorted(assets)}
        for fut in cf.as_completed(futs):
            u = futs[fut]
            err = fut.result()
            if err:
                bad.append((u, err))

    index_json_url = base + "/search-index.json"
    n_records, idx_err = check_search_index(index_json_url)
    if idx_err:
        bad.append((index_json_url, idx_err))
    else:
        print(f"search-index.json ✓（{n_records} 筆，JSON 可解析）", flush=True)

    print()
    total = 1 + len(pages) + len(imgs) + len(assets) + 1  # +1 首頁本體、+1 search-index.json
    if bad:
        print(f"✗ {len(bad)}/{total} 項有問題：", file=sys.stderr)
        for u, err in sorted(bad)[:30]:
            print(f"    {u}: {err}", file=sys.stderr)
        if len(bad) > 30:
            print(f"    …還有 {len(bad)-30} 項", file=sys.stderr)
        sys.exit(1)
    print(
        f"✓ 全綠：首頁＋{len(pages)} 頁＋{len(imgs)} 圖＋{len(assets)} CSS/JS＋"
        f"search-index.json（{n_records} 筆）都在 live 站"
    )


if __name__ == "__main__":
    main()
