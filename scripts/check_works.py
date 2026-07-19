#!/usr/bin/env python3
"""content/works.yaml 作品登記簿驗證器（schema＋連結）。零參數可跑。

分工（同 check_songs.py 的精神，2026-07-19）：
  - `scripts/build_pages.py` 的 load_works_registry() 在 build 時做離線
    schema fail-fast（必填欄、type 詞彙、pages 型別、title 不含書名號）。
  - 本檔做完整驗證：上述 schema ＋ pages slug 存在性（content/people/、
    content/fields/ 的檔名 stem）＋ 全域條目同名唯一 ＋ **連結驗證**
    （YouTube 打 oEmbed、其他網址驗 HTTP 200——重用 check_songs.py 的
    _check_links 管線，含 timeout／重試／平行）。
  - 通過驗證只代表連結活著；來源授權合格性（SONGS-SPEC §5 優先序，個人
    上傳不收）由人工／盲驗 agent 逐條判，不在此自動化。

零內容過渡態：content/works.yaml 不存在或 `works:` 為空＝通過（登記簿由
後續批次逐步填入，空簿不是錯誤）。

用法：
  python3 scripts/check_works.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

import check_songs  # 重用 UA／oEmbed／HTTP 200 連結驗證管線（_check_links）

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "content"
WORKS_YAML = CONTENT / "works.yaml"
PEOPLE = CONTENT / "people"
FIELDS = CONTENT / "fields"

# 與 build_pages.py 的 WORK_TYPES 同一詞彙表（works.yaml 檔頭 schema 說明）。
WORK_TYPES = {"song", "album", "art", "book", "film", "play", "other"}

WORK_REQUIRED = ("title", "url", "type")


def _known_page_slugs() -> set[str]:
    """pages 欄合法值＝人物頁 slug（content/people/*.md stem）∪ 領域頁 slug
    （content/fields/*.md stem）。"""
    slugs: set[str] = set()
    for d in (PEOPLE, FIELDS):
        if d.is_dir():
            slugs.update(p.stem for p in d.glob("*.md"))
    return slugs


def validate() -> list[str]:
    """回傳錯誤清單（空清單＝全過）。檔案不存在或空簿＝零內容過渡態，通過。"""
    errors: list[str] = []
    if not WORKS_YAML.is_file():
        return errors
    try:
        data = yaml.safe_load(WORKS_YAML.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        return [f"works.yaml：YAML 解析失敗：{e}"]
    works = data.get("works") or []
    if not works:
        return errors

    page_slugs = _known_page_slugs()
    global_titles: dict[str, int] = {}
    link_targets: list[tuple[str, str]] = []

    # 書名號偵測（title 是「不含」書名號的比對用字串）——與 build_pages.py
    # 的 _WORK_TITLE_RE 同義，此處只需判斷有無出現。
    bracket_chars = set("《》〈〉")

    for i, w in enumerate(works):
        if not isinstance(w, dict):
            errors.append(f"works.yaml：第 {i+1} 筆不是合法的作品物件")
            continue
        label = w.get("title") or f"第 {i+1} 筆"
        where = f"works.yaml：{label}"

        for key in WORK_REQUIRED:
            if key not in w:
                errors.append(f"{where}：缺 `{key}`")
        if "type" in w and w["type"] not in WORK_TYPES:
            errors.append(f"{where}：type `{w['type']}` 不合法（須為 {sorted(WORK_TYPES)} 之一）")

        title = w.get("title")
        if isinstance(title, str) and bracket_chars & set(title):
            errors.append(f"{where}：title 不得含書名號《》〈〉（比對用，見檔頭 schema 說明）")

        url = w.get("url")
        if isinstance(url, str):
            if not url.startswith(("http://", "https://")):
                errors.append(f"{where}：url 須為 http(s) 連結（實得 {url!r}）")
            else:
                link_targets.append((url, where))
        elif "url" in w:
            errors.append(f"{where}：url 須為字串（實得 {url!r}）")

        pages = w.get("pages")
        if pages is not None:
            if not isinstance(pages, list) or not all(isinstance(s, str) for s in pages):
                errors.append(f"{where}：pages 須為頁 slug 字串清單（實得 {pages!r}）")
            else:
                if not pages:
                    errors.append(f"{where}：pages 給了卻是空清單（要全站通用就整欄省略）")
                for s in pages:
                    if s not in page_slugs:
                        errors.append(
                            f"{where}：pages slug `{s}` 查無對應頁"
                            "（content/people/*.md 或 content/fields/*.md）"
                        )
        elif isinstance(title, str):
            # 全域條目（無 pages）同名只能一筆，否則掛鏈目標模稜兩可。
            global_titles[title] = global_titles.get(title, 0) + 1

    for title, n in sorted(global_titles.items()):
        if n > 1:
            errors.append(
                f"works.yaml：全域條目（無 pages 限定）title `{title}` 出現 {n} 筆——"
                "同名異作請用 pages 限定消歧義"
            )

    errors.extend(check_songs._check_links(link_targets))
    return errors


def main() -> None:
    errors = validate()
    if errors:
        print(f"[check_works] ✗ {len(errors)} 項問題：", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)
    print("[check_works] ✓ 全綠")


if __name__ == "__main__":
    main()
