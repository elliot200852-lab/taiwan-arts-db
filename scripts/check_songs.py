#!/usr/bin/env python3
"""臺灣歌曲線登記簿驗證器（schema＋孤兒歌名＋連結）。SSOT 規格＝docs/SONGS-SPEC.md。

分工（重要，2026-07-18）：
  - `scripts/build_pages.py` 在 build 開頭以 `validate(no_net=True)` 呼叫本檔——
    只做 schema／孤兒歌名驗證，**不打任何網路連結**，build 才能在離線環境
    快速 fail-fast。
  - 完整的連結驗證（YouTube oEmbed／HTTP 200）耗時且依賴網路，屬於「部署前」
    另外手動跑的一關：`python3 scripts/check_songs.py`（不加 --no-net）。
    CI／`verify_live.py` 不重複做這件事——歌單外部連結只在這裡管。

驗證項目（SONGS-SPEC §2.2／§6／§7）：
  1. schema：必填欄、era 僅限 9 個合法 slug 且與分片檔名一致、id 跨片唯一、
     hook ≤45 字、listen 1–3 條且欄位齊全、source_type／version 合法值、
     sources ≥1、people slug 存在於 content/people/、每片至少 1 首、
     有 MD 無分片或有分片無 MD 都算 fail。
  2. 孤兒歌名：時代頁正文《…》若不在登記簿 title 集合內即 fail（跳過已在
     `[label](url)` markdown 連結內的文字、跳過「## 出處」之後的區段）。
  3. 連結：YouTube 打 oEmbed（非 200 即 fail，timeout 10s、重試 2 次）；
     非 YouTube 驗 HTTP 200。`--no-net` 跳過本項。

零內容過渡態（content/songs/ 不存在或全空）：回傳 pass，不是 fail——歌曲線
尚未開工不算錯誤（同首頁地圖 tab 的過渡態前例）。

用法：
  python3 scripts/check_songs.py             # 完整驗證（含連結，需要網路）
  python3 scripts/check_songs.py --no-net    # 跳過連結驗證（build_pages.py 用）
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "content"
SONGS = CONTENT / "songs"
PEOPLE = CONTENT / "people"

UA = {"User-Agent": "taiwan-arts-db-check-songs/1.0"}

# 九個合法時代 slug（SONGS-SPEC §1，定案不得自行增刪改名；第九期 David 2026-07-20 拍板新增）。
ERA_SLUGS = [
    "era-roots",
    "era-78rpm",
    "era-postwar",
    "era-mandarin",
    "era-folk",
    "era-turning",
    "era-new-taiwanese",
    "era-polyphony",
    "era-streaming",
]

SOURCE_TYPES = {"official", "archive", "broadcaster", "foundation", "other"}
VERSIONS = {"original", "famous_cover", "live"}

SONG_REQUIRED = ("id", "title", "year", "era", "language", "hook", "listen", "sources")
LISTEN_REQUIRED = ("url", "label", "source_type", "version")

_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.S)
_LINK_RE = re.compile(r"\[[^\]]*\]\([^)]*\)")
_TITLE_RE = re.compile(r"《([^》]+)》")
_FOOTNOTES_HEADING_RE = re.compile(r"^## 出處\s*$", re.M)


def _split_frontmatter(path: Path) -> tuple[dict | None, str]:
    """回傳 (frontmatter dict 或 None, body)。frontmatter 缺失／解析失敗回 None，
    呼叫端自行決定要不要記錯誤（本檔不像 build_pages.py 那樣直接 die，
    是收集完整錯誤清單再一次回報）。"""
    raw = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        return None, raw
    try:
        fm = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return None, m.group(2)
    return fm, m.group(2)


def _find_orphan_song_titles(era_md_paths: list[Path], songs_by_title: dict[str, dict]) -> list[str]:
    """掃時代頁正文《…》，不在登記簿 title 集合內者列為孤兒。只掃「## 出處」
    之前的區段（腳註區不算）；跳過已在 `[label](url)` markdown 連結內的文字
    （避免誤判寫手手寫連結的文字裡剛好帶書名號）。"""
    orphans: list[str] = []
    for md_path in era_md_paths:
        fm, body = _split_frontmatter(md_path)
        if fm is None:
            continue  # frontmatter 本身有錯，schema 檢查已經報過，這裡不重複
        cut = _FOOTNOTES_HEADING_RE.search(body)
        scoped = body[: cut.start()] if cut else body
        scoped = _LINK_RE.sub("", scoped)
        for m in _TITLE_RE.finditer(scoped):
            title = m.group(1)
            if title not in songs_by_title:
                orphans.append(f"{md_path.name}：正文出現《{title}》但登記簿查無此曲（孤兒歌名）")
    return orphans


def _is_youtube(url: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    return host.endswith("youtube.com") or host.endswith("youtu.be")


def _check_youtube(url: str) -> str | None:
    """打 YouTube oEmbed，非 200 即失敗；timeout 10s，重試 2 次（共 3 次嘗試）。
    回傳 None＝通過，否則錯誤訊息。"""
    oembed = "https://www.youtube.com/oembed?url=" + urllib.parse.quote(url, safe="") + "&format=json"
    last_err = "未知錯誤"
    for _ in range(3):
        try:
            req = urllib.request.Request(oembed, headers=UA)
            with urllib.request.urlopen(req, timeout=10) as r:
                if r.status == 200:
                    return None
                last_err = f"HTTP {r.status}"
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}"
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
    return f"YouTube oEmbed 失敗（{last_err}）"


def _check_http_200(url: str) -> str | None:
    try:
        req = urllib.request.Request(url, headers=UA, method="GET")
        with urllib.request.urlopen(req, timeout=10) as r:
            if r.status == 200:
                return None
            return f"HTTP {r.status}"
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        if "CERTIFICATE_VERIFY_FAILED" in err:
            # 台灣政府／機構站（*.gov.tw、culture.tw、tfam.museum 等）的 TWCA
            # 憑證缺 Subject Key Identifier，Python 3.13 的鏈驗證一律拒收；
            # curl 走系統信任庫可過。退回 curl 驗，避免誤報活連結為死鏈。
            return _check_http_200_curl(url)
        return err


def _check_http_200_curl(url: str) -> str | None:
    try:
        r = subprocess.run(
            ["curl", "-sL", "-o", "/dev/null", "-w", "%{http_code}",
             "--max-time", "20", "-A", UA["User-Agent"], url],
            capture_output=True, text=True, timeout=30,
        )
        code = r.stdout.strip()
        return None if code == "200" else f"HTTP {code or '無回應'}（curl fallback）"
    except Exception as e:
        return f"curl fallback 失敗：{type(e).__name__}: {e}"


def _check_links(link_targets: list[tuple[str, str]]) -> list[str]:
    """link_targets＝[(url, 出處描述), ...]；平行打連結，回傳失敗清單。"""
    errors: list[str] = []
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        futs = {
            ex.submit(_check_youtube if _is_youtube(url) else _check_http_200, url): (url, where)
            for url, where in link_targets
        }
        for fut in cf.as_completed(futs):
            url, where = futs[fut]
            err = fut.result()
            if err:
                errors.append(f"{where}：連結失敗（{url}）：{err}")
    return errors


def validate(no_net: bool) -> list[str]:
    """跑完整 schema＋孤兒歌名驗證，`no_net=False` 時再加連結驗證。回傳錯誤
    清單（空清單＝全過）。content/songs/ 不存在或全空＝零內容過渡態，視為
    通過（不是 fail）——見檔頭說明。"""
    errors: list[str] = []
    if not SONGS.is_dir():
        return errors
    era_md_paths = sorted(SONGS.glob("era-*.md"))
    era_yaml_paths = sorted(SONGS.glob("era-*.yaml"))
    if not era_md_paths and not era_yaml_paths:
        return errors

    md_slugs = {p.stem for p in era_md_paths}
    yaml_slugs = {p.stem for p in era_yaml_paths}
    for slug in sorted(md_slugs - yaml_slugs):
        errors.append(f"{slug}.md：有時代頁 MD 但缺對應登記簿 {slug}.yaml")
    for slug in sorted(yaml_slugs - md_slugs):
        errors.append(f"{slug}.yaml：有登記簿但缺對應時代頁 {slug}.md")

    person_slugs = {p.stem for p in PEOPLE.glob("*.md")} if PEOPLE.is_dir() else set()

    for md_path in era_md_paths:
        fm, _ = _split_frontmatter(md_path)
        if fm is None:
            errors.append(f"{md_path.name}：找不到 YAML frontmatter 或解析失敗")
            continue
        for key in ("title", "slug", "period", "order", "axis"):
            if key not in fm:
                errors.append(f"{md_path.name}：frontmatter 缺 `{key}`")
        if fm.get("slug") != md_path.stem:
            errors.append(f"{md_path.name}：frontmatter slug（{fm.get('slug')!r}）與檔名不一致")
        elif fm.get("slug") not in ERA_SLUGS:
            errors.append(f"{md_path.name}：slug `{fm.get('slug')}` 不在合法的 9 個時代 slug 內")

    all_ids: dict[str, str] = {}
    songs_by_title: dict[str, dict] = {}

    for yaml_path in era_yaml_paths:
        slug = yaml_path.stem
        if slug not in ERA_SLUGS:
            errors.append(f"{yaml_path.name}：檔名 slug 不在合法的 9 個時代 slug 內")
        try:
            data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            errors.append(f"{yaml_path.name}：YAML 解析失敗：{e}")
            continue
        songs = data.get("songs")
        if not songs:
            errors.append(f"{yaml_path.name}：登記簿至少要有 1 首歌（`songs:` 缺或為空）")
            continue
        for i, song in enumerate(songs):
            if not isinstance(song, dict):
                errors.append(f"{yaml_path.name}：第 {i+1} 筆不是合法的歌曲物件")
                continue
            label = song.get("title") or song.get("id") or f"第 {i+1} 筆"
            where = f"{yaml_path.name}：{label}"

            for key in SONG_REQUIRED:
                if key not in song:
                    errors.append(f"{where}：缺 `{key}`")
            if not song.get("credits"):
                errors.append(f"{where}：credits 至少要有一欄")

            sid = song.get("id")
            if sid:
                if sid in all_ids:
                    errors.append(f"{where}：id `{sid}` 與 {all_ids[sid]} 重複")
                else:
                    all_ids[sid] = yaml_path.name

            if "era" in song and song["era"] != slug:
                errors.append(f"{where}：era `{song['era']}` 與檔名 slug `{slug}` 不符")

            hook = song.get("hook")
            if isinstance(hook, str) and len(hook) > 45:
                errors.append(f"{where}：hook 超過 45 字（實得 {len(hook)} 字）")

            for pslug in song.get("people") or []:
                if pslug not in person_slugs:
                    errors.append(f"{where}：people slug `{pslug}` 查無 content/people/{pslug}.md")

            listen = song.get("listen")
            if listen is not None:
                if not isinstance(listen, list) or not (1 <= len(listen) <= 3):
                    errors.append(f"{where}：listen 須為 1–3 條清單（實得 {listen!r}）")
                else:
                    for j, l in enumerate(listen):
                        if not isinstance(l, dict):
                            errors.append(f"{where}：listen[{j}] 不是合法物件")
                            continue
                        for lkey in LISTEN_REQUIRED:
                            if lkey not in l:
                                errors.append(f"{where}：listen[{j}] 缺 `{lkey}`")
                        if "source_type" in l and l["source_type"] not in SOURCE_TYPES:
                            errors.append(
                                f"{where}：listen[{j}] source_type `{l['source_type']}` 不合法"
                                f"（須為 {sorted(SOURCE_TYPES)} 之一）"
                            )
                        if "version" in l and l["version"] not in VERSIONS:
                            errors.append(
                                f"{where}：listen[{j}] version `{l['version']}` 不合法"
                                f"（須為 {sorted(VERSIONS)} 之一）"
                            )

            sources = song.get("sources")
            if sources is not None and (not isinstance(sources, list) or not sources):
                errors.append(f"{where}：sources 至少要有 1 條")

            title = song.get("title")
            if title:
                songs_by_title.setdefault(title, song)

    errors.extend(_find_orphan_song_titles(era_md_paths, songs_by_title))

    if not no_net:
        link_targets: list[tuple[str, str]] = []
        for yaml_path in era_yaml_paths:
            data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
            for i, song in enumerate(data.get("songs") or []):
                if not isinstance(song, dict):
                    continue
                label = song.get("title") or song.get("id") or f"第 {i+1} 筆"
                for j, l in enumerate(song.get("listen") or []):
                    if isinstance(l, dict) and l.get("url"):
                        link_targets.append((l["url"], f"{yaml_path.name}：{label} listen[{j}]"))
        errors.extend(_check_links(link_targets))

    return errors


def main() -> None:
    ap = argparse.ArgumentParser(description="臺灣歌曲線登記簿驗證（schema＋孤兒歌名＋連結）")
    ap.add_argument(
        "--no-net",
        action="store_true",
        help="跳過連結驗證（build_pages.py fail-fast 用；完整連結驗證需部署前另跑，見檔頭說明）",
    )
    args = ap.parse_args()

    errors = validate(no_net=args.no_net)
    if errors:
        print(f"[check_songs] ✗ {len(errors)} 項問題：", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)

    suffix = "（--no-net，跳過連結驗證）" if args.no_net else ""
    print(f"[check_songs] ✓ 全綠{suffix}")


if __name__ == "__main__":
    main()
