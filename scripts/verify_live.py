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

用法：
  python scripts/verify_live.py                 # 驗預設站台
  python scripts/verify_live.py --base <url>    # 換站台
"""
import argparse
import concurrent.futures as cf
import re
import sys
import urllib.parse
import urllib.request

DEFAULT_BASE = "https://elliot200852-lab.github.io/taiwan-arts-db"
UA = {"User-Agent": "taiwan-arts-db-verify/1.0"}

HREF = re.compile(r'<a[^>]+href="([^"]+)"', re.IGNORECASE)
IMG_SRC = re.compile(r'<img[^>]+src="([^"]+)"', re.IGNORECASE)


def fetch(url, timeout=30):
    req = urllib.request.Request(url, method="GET", headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.headers.get("Content-Type", ""), r.read()


def is_internal(url, base):
    return url.startswith(base.rstrip("/") + "/") or url == base.rstrip("/")


def normalize(link, page_url):
    """相對連結 → 絕對 URL，去 fragment／query。回傳 None 表示非站內資源。"""
    link = link.strip()
    if not link or link.startswith(("mailto:", "javascript:", "#", "data:")):
        return None
    absolute = urllib.parse.urljoin(page_url, link)
    absolute = absolute.split("#", 1)[0].split("?", 1)[0]
    return absolute


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


def check_img(url):
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

    # 逐頁抓回並收集圖片（首頁的圖也算）
    imgs = set()
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

    for u, html in page_bodies.items():
        for m in IMG_SRC.finditer(html):
            url = normalize(m.group(1), u)
            if url and is_internal(url, base):
                imgs.add(url)

    print(f"頁面 {len(pages)} 個驗畢（{len(bad)} 壞），收集到 {len(imgs)} 張站內圖片",
          flush=True)

    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(check_img, u): u for u in sorted(imgs)}
        for i, fut in enumerate(cf.as_completed(futs), 1):
            u = futs[fut]
            err = fut.result()
            if err:
                bad.append((u, err))
            if i % 50 == 0:
                print(f"  … 圖片 {i}/{len(imgs)}（目前共 {len(bad)} 壞）", flush=True)

    print()
    total = 1 + len(pages) + len(imgs)
    if bad:
        print(f"✗ {len(bad)}/{total} 項有問題：", file=sys.stderr)
        for u, err in sorted(bad)[:30]:
            print(f"    {u}: {err}", file=sys.stderr)
        if len(bad) > 30:
            print(f"    …還有 {len(bad)-30} 項", file=sys.stderr)
        sys.exit(1)
    print(f"✓ 全綠：首頁＋{len(pages)} 頁＋{len(imgs)} 圖都在 live 站")


if __name__ == "__main__":
    main()
