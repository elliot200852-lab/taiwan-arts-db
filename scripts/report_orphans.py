#!/usr/bin/env python3
"""Drive html 夾孤兒檔報告（report-only，絕不刪除）。

背景：`scripts/deploy.sh` 的換版邏輯（`sync_one`）只增改（新增／覆蓋有變動的
檔），從不刪除 Drive 上既有的檔。當一個人物頁被改名或整篇下架，舊的
`pages/<old-slug>.html` 不會再被任何本機 build 產出提及，但它仍然留在 Drive
html 夾裡——變成永遠沒人管的孤兒檔（可能仍被舊連結／搜尋引擎索引到）。

做法：
  1. 用 gws 遞迴列出 Drive html 夾（`drive-manifest.yaml` 的 `files.site`，
     含 `pages/` 子夾）的全部檔案，取得相對路徑集合。
  2. 用 `_build/`（`scripts/build_pages.py` 的產出，鏡射 Drive html 夾結構）
     取得「本機 build 認得的檔案」相對路徑集合，作為 canonical 清單。
     若呼叫當下 `_build/` 不存在或不完整，本腳本會自己先跑一次
     build_pages.py 產生（離線、無需 Drive 憑證，見該檔檔頭）。
  3. Drive 有、build 沒有 → 孤兒；印出清單（相對路徑＋Drive file id，方便
     David 手動到 Drive 介面或用 gws 逐一確認／刪除）。
  4. **只報告，不呼叫任何刪除 API**——是否刪除、要不要先備份，由人決定。

例外清單（ALLOWLIST）：目前查無任何「合法但非 build_pages.py 產物」的
Drive 檔案——`index.html`／`search-index.json`／`sitemap.xml` 皆已是
build_pages.py 的正式產物（見該檔檔頭與 docs/DEPLOY.md），保留空清單以備
未來若真的手動放了合法例外檔可以加進來，不會被誤判孤兒。

用法：
  python3 scripts/report_orphans.py              # 一般模式：印孤兒清單
  python3 scripts/report_orphans.py --no-rebuild # 信任呼叫端已跑過 build_pages.py，
                                                  # 不重跑（deploy.sh 收尾呼叫用，
                                                  # 避免同一次部署重複 build）

退出碼：發現孤兒＝1（不代表失敗，只是提醒；deploy.sh 呼叫時不因此中止部署）；
未發現孤兒或空的過渡態＝0；gws／Drive 存取本身出錯＝2（無法判斷，非「有孤兒」）。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "drive-manifest.yaml"
BUILD = ROOT / "_build"
FOLDER_MIME = "application/vnd.google-apps.folder"

# 已知「合法但非 build_pages.py 產物」的例外清單，相對於 Drive html 夾根目錄
# （見檔頭說明，目前為空）。
ALLOWLIST: set[str] = set()


def gws_json(args: list[str]) -> dict:
    """呼叫 gws CLI 並解析 JSON stdout。gws 對某些呼叫會在 cwd 留
    0-byte download.html 殘檔——呼叫端（main()）負責事後清掉。"""
    proc = subprocess.run(
        ["gws", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"gws {' '.join(args)} 失敗（exit {proc.returncode}）：{proc.stderr.strip()}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"gws {' '.join(args)} 回傳非合法 JSON：{e}\n{proc.stdout[:500]}") from e


def list_children(folder_id: str) -> list[dict]:
    """列出單一 Drive 資料夾底下的直屬項目（含分頁）。"""
    out: list[dict] = []
    token: str | None = None
    while True:
        params = {
            "q": f"'{folder_id}' in parents and trashed = false",
            "fields": "nextPageToken, files(id, name, mimeType)",
            "pageSize": 200,
        }
        if token:
            params["pageToken"] = token
        resp = gws_json(["drive", "files", "list", "--params", json.dumps(params)])
        out.extend(resp.get("files", []))
        token = resp.get("nextPageToken")
        if not token:
            return out


def walk_drive(folder_id: str, prefix: str = "") -> dict[str, str]:
    """遞迴列出 Drive 資料夾（含子夾）全部檔案，回傳 {相對路徑: file id}。"""
    found: dict[str, str] = {}
    for item in list_children(folder_id):
        rel = f"{prefix}{item['name']}"
        if item["mimeType"] == FOLDER_MIME:
            found.update(walk_drive(item["id"], prefix=f"{rel}/"))
        else:
            found[rel] = item["id"]
    return found


def _read_build_dir() -> set[str]:
    """單純讀取現有 `_build/` 內容為相對路徑集合，不觸發任何 build。"""
    if not BUILD.is_dir():
        return set()
    paths: set[str] = set()
    for p in BUILD.iterdir():
        if p.is_file():
            paths.add(p.name)
    pages_dir = BUILD / "pages"
    if pages_dir.is_dir():
        for p in pages_dir.glob("*.html"):
            paths.add(f"pages/{p.name}")
    return paths


def local_expected_paths(*, force_rebuild: bool, never_rebuild: bool) -> set[str]:
    """本機 build 產出的相對路徑集合（canonical，鏡射 Drive html 夾結構）。

    - `force_rebuild`（--rebuild）：不管 `_build/` 現況，先重跑一次 build_pages.py。
    - `never_rebuild`（--no-rebuild）：信任呼叫端已跑過 build_pages.py
      （deploy.sh 收尾呼叫的情境——STEP 1 才剛 build 過），絕不重跑，
      即使 `_build/` 看起來是空的也只回傳空集合並讓呼叫端自行決定要不要警告。
    - 兩者皆否（預設，獨立執行時）：`_build/` 缺 `index.html`（不存在或不完整）
      才自動跑一次 build_pages.py 補齊，避免拿殘缺/過期的 _build/ 誤判孤兒。
    """
    if never_rebuild:
        return _read_build_dir()

    if force_rebuild or not (BUILD / "index.html").is_file():
        print("[report_orphans] 跑一次 build_pages.py 產生本機 canonical 清單…")
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "build_pages.py")],
            cwd=ROOT, check=True,
        )
    return _read_build_dir()


def main() -> None:
    ap = argparse.ArgumentParser(description="Drive html 夾孤兒檔報告（report-only）")
    ap.add_argument(
        "--no-rebuild", action="store_true",
        help="信任呼叫端已跑過 build_pages.py、直接讀既有 _build/（deploy.sh 收尾呼叫用）",
    )
    ap.add_argument(
        "--rebuild", action="store_true",
        help="強制重跑 build_pages.py（即使 _build/ 看起來已存在）",
    )
    args = ap.parse_args()

    if args.no_rebuild and args.rebuild:
        print("✗ --no-rebuild 與 --rebuild 不能同時指定。", file=sys.stderr)
        sys.exit(2)

    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    html_folder_id = manifest.get("files", {}).get("site")
    if not html_folder_id or str(html_folder_id).startswith("FILL_ME"):
        print("✗ drive-manifest.yaml 的 files.site 還沒填實際 folder ID，中止。", file=sys.stderr)
        sys.exit(2)

    try:
        drive_paths = walk_drive(html_folder_id)
    except Exception as e:
        print(f"✗ 讀取 Drive html 夾失敗：{e}", file=sys.stderr)
        sys.exit(2)
    finally:
        # gws 對本次用到的 list 呼叫（無 body）會在 cwd 留 0-byte download.html
        stray = ROOT / "download.html"
        if stray.is_file() and stray.stat().st_size == 0:
            stray.unlink()

    expected_paths = local_expected_paths(force_rebuild=args.rebuild, never_rebuild=args.no_rebuild)
    if args.no_rebuild and not expected_paths:
        print("⚠ --no-rebuild 但 _build/ 是空的或不存在——canonical 清單為空，"
              "此次報告不具參考價值，建議先跑 build_pages.py。", file=sys.stderr)

    orphans = {rel: fid for rel, fid in drive_paths.items()
               if rel not in expected_paths and rel not in ALLOWLIST}

    print(f"[report_orphans] Drive html 夾共 {len(drive_paths)} 檔；"
          f"本機 build 認得 {len(expected_paths)} 檔。")

    if not orphans:
        print("[report_orphans] ✓ 沒有孤兒檔。")
        sys.exit(0)

    print(f"[report_orphans] ⚠ 發現 {len(orphans)} 個孤兒檔"
          "（Drive 有、本機 build 沒有——只報告不刪除，請人工確認後再決定要不要清）：")
    for rel in sorted(orphans):
        print(f"  - {rel}（file id: {orphans[rel]}）")
    sys.exit(1)


if __name__ == "__main__":
    main()
