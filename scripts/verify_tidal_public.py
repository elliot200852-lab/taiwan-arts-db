#!/usr/bin/env python3
"""驗證九個 TIDAL 歌單是否真的對外可及（別的使用者點連結打得開）。

為什麼需要這支：
  用瀏覽器無痕視窗開歌單網址，不論公開或私人都會叫你登入，**看不出差別**。
  tidal-cli 建歌單時寫死 accessType='UNLISTED'，實測這個層級的歌單在對外
  介面上取不到（embed 回 500、連結預覽拿不到歌單名），等同外人打不開。
  必須在 TIDAL app 內把歌單改成「公開 Public」，改完跑這支確認。

判準（兩個獨立訊號都要過）：
  1. https://embed.tidal.com/playlists/<id> 回 200（私人／未列出會回 500）
  2. https://tidal.com/browse/playlist/<id> 的 og:title 是歌單名稱
     （取不到時會退成 TIDAL 官網樣板標題）

用法：
    python3 scripts/verify_tidal_public.py                    # 讀 arts-db 的 tidal-playlists.yaml
    python3 scripts/verify_tidal_public.py --playlist-id ID   # 驗任意歌單，可重複多次
    python3 scripts/verify_tidal_public.py --playlist-id A --playlist-id B
"""
from __future__ import annotations

import argparse
import re
import sys
import urllib.request
import urllib.error
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PLAYLISTS = ROOT / "content" / "songs" / "tidal-playlists.yaml"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
BOILERPLATE = "TIDAL - High Fidelity Music Streaming"


def fetch(url: str, timeout: int = 25) -> tuple[int, str]:
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:
        return 0, ""


def check(pid: str) -> tuple[bool, bool, str]:
    embed_code, _ = fetch(f"https://embed.tidal.com/playlists/{pid}")
    _, html = fetch(f"https://tidal.com/browse/playlist/{pid}")
    m = re.search(r'<meta property="og:title" content="([^"]*)"', html)
    og = m.group(1) if m else ""
    return embed_code == 200, bool(og) and og != BOILERPLATE, og


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--playlist-id", action="append", dest="playlist_ids", metavar="ID",
        help="直接驗證指定的 TIDAL 歌單 ID（可重複多次）；給了就不讀 arts-db 的 tidal-playlists.yaml",
    )
    args = ap.parse_args()

    if args.playlist_ids:
        pids = args.playlist_ids
        print(f"檢查 {len(pids)} 個歌單是否對外可及\n")
        ok = 0
        for pid in pids:
            embed_ok, og_ok, og = check(pid)
            good = embed_ok and og_ok
            ok += good
            mark = "✅ 對外可及" if good else "❌ 外人打不開"
            detail = f"embed={'200' if embed_ok else 'fail'} og={'✓' if og_ok else '✗'}"
            label = (og if (og_ok and og) else f"(id={pid})")[:34]
            print(f"  {mark}  {label:<36} {detail}   id={pid}")
            if not good and og:
                print(f"      og:title 抓到的是「{og[:46]}」")

        print(f"\n{ok}/{len(pids)} 個對外可及")
        if ok < len(pids):
            print(
                "\n未通過的請在 TIDAL app 開啟該歌單 → 右上「⋯」→ 隱私設定／Privacy →\n"
                "改成「公開 Public」。改完重跑這支確認。"
            )
        return 0 if ok == len(pids) else 1

    # 原本的 arts-db yaml 模式，行為不變。
    if not PLAYLISTS.exists():
        print(f"找不到 {PLAYLISTS}——先跑 scripts/tidal_match.py --create-playlists")
        return 2
    eras = (yaml.safe_load(PLAYLISTS.read_text(encoding="utf-8")) or {}).get("eras") or {}
    if not eras:
        print("歌單清單是空的")
        return 2

    print(f"檢查 {len(eras)} 個歌單是否對外可及\n")
    ok = 0
    for slug, e in eras.items():
        embed_ok, og_ok, og = check(e["playlist_id"])
        good = embed_ok and og_ok
        ok += good
        mark = "✅ 對外可及" if good else "❌ 外人打不開"
        detail = f"embed={'200' if embed_ok else 'fail'} og={'✓' if og_ok else '✗'}"
        print(f"  {mark}  {e['name'][:34]:<36} {detail}")
        if not good and og:
            print(f"      og:title 抓到的是「{og[:46]}」")

    print(f"\n{ok}/{len(eras)} 個對外可及")
    if ok < len(eras):
        print(
            "\n未通過的請在 TIDAL app 開啟該歌單 → 右上「⋯」→ 隱私設定／Privacy →\n"
            "改成「公開 Public」。改完重跑這支確認。"
        )
    return 0 if ok == len(eras) else 1


if __name__ == "__main__":
    sys.exit(main())
