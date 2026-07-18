#!/usr/bin/env python3
"""從 Google Drive 拉取全站內容（HTML＋圖片）到 site/。

內容 SSOT 在 Drive、repo 只留腳本（~/.claude/CLAUDE.md「網路部署架構鐵則」）。
本 repo 比 taiwan-geo-db 更徹底：不只圖片，連內容 HTML 都在 Drive——
repo 裡完全沒有內容檔，只有腳本、模板與 CSS。

對照 drive-manifest.yaml 的 <本機相對路徑>: <Drive folder ID> 逐夾遞迴下載：
  html 夾（index.html＋pages/*.html）→ site/
  img 夾                            → site/img/
下載完把 repo 的 assets/（CSS 等程式資產）複製進 site/assets/。

認證（channel-deployer SA）：
  CI   → 環境變數 DRIVE_SA_KEY（單行 SA key JSON）
  本機 → DRIVE_SA_KEY_FILE（key 檔路徑）

⚠ 兩道 fail-fast 刻意保留，不要拿掉（沿 taiwan-geo-db pull_images.py 的教訓）：
  1. 在 GitHub Actions 上卻沒有憑證 → 中止；只有本機允許 skip
  2. 拉完 site/ 底下一個 HTML 都沒有 → 中止建置，以免空站上線
內容不在 repo，CI 綠不代表內容在——這兩道閘是唯一擋得住空站上線的地方。
部署後仍要跑 scripts/verify_live.py 對 live 逐檔驗證。
"""
import io
import json
import os
import pathlib
import random
import shutil
import sys
import time

import yaml
from google.oauth2 import service_account
from googleapiclient.discovery import build as build_service
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

ROOT = pathlib.Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "drive-manifest.yaml"
SITE = ROOT / "site"
FOLDER_MIME = "application/vnd.google-apps.folder"
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

TRANSIENT = ("Premature close", "ECONNRESET", "ETIMEDOUT", "socket hang up",
             "500", "502", "503", "504", "429", "rateLimitExceeded",
             "userRateLimitExceeded", "backendError", "internalError")


def is_transient(exc):
    if isinstance(exc, HttpError) and exc.resp.status in (429, 500, 502, 503, 504):
        return True
    return any(t.lower() in str(exc).lower() for t in TRANSIENT)


def with_retry(fn, what, attempts=6):
    """指數退避：1s→2s→4s→8s→16s（cap 20s）。權限／404 直接拋，不重試。"""
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            if i == attempts - 1 or not is_transient(e):
                raise
            delay = min(2 ** i, 20) + random.uniform(0, 0.5)
            print(f"  ! {what} 第 {i+1} 次失敗（{str(e)[:100]}），{delay:.1f}s 後重試",
                  flush=True)
            time.sleep(delay)


def load_credentials():
    raw = (os.environ.get("DRIVE_SA_KEY") or "").strip()
    if raw.startswith("{"):
        return service_account.Credentials.from_service_account_info(
            json.loads(raw), scopes=SCOPES)
    path = os.environ.get("DRIVE_SA_KEY_FILE")
    if path and pathlib.Path(path).exists():
        return service_account.Credentials.from_service_account_file(
            path, scopes=SCOPES)
    return None


def list_children(svc, folder_id):
    out, token = [], None
    while True:
        def _call():
            return svc.files().list(
                q=f"'{folder_id}' in parents and trashed = false",
                fields="nextPageToken, files(id, name, mimeType, size)",
                pageSize=100, pageToken=token,
                supportsAllDrives=True, includeItemsFromAllDrives=True,
            ).execute()

        resp = with_retry(_call, f"list {folder_id}")
        out.extend(resp.get("files", []))
        token = resp.get("nextPageToken")
        if not token:
            return out


def download_file(svc, file_id, dest):
    def _call():
        buf = io.BytesIO()
        req = svc.files().get_media(fileId=file_id, supportsAllDrives=True)
        dl = MediaIoBaseDownload(buf, req, chunksize=5 * 1024 * 1024)
        done = False
        while not done:
            _, done = dl.next_chunk()
        return buf.getvalue()

    data = with_retry(_call, f"download {dest.name}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return len(data)


def pull_folder(svc, folder_id, dest_dir):
    """遞迴下載。回傳 (檔數, 總 bytes)。不刪目的地既有檔。"""
    n, total = 0, 0
    for item in list_children(svc, folder_id):
        target = dest_dir / item["name"]
        if item["mimeType"] == FOLDER_MIME:
            sub_n, sub_b = pull_folder(svc, item["id"], target)
            n += sub_n
            total += sub_b
        else:
            total += download_file(svc, item["id"], target)
            n += 1
            if n % 50 == 0:
                print(f"  … {n} 檔", flush=True)
    return n, total


def copy_assets():
    """repo 的程式資產（CSS 等）疊進 site/assets/——這部分 SSOT 在 repo，不在 Drive。"""
    src = ROOT / "assets"
    dest = SITE / "assets"
    shutil.copytree(src, dest, dirs_exist_ok=True)
    n = sum(1 for p in dest.rglob("*") if p.is_file())
    print(f"✓ assets/ → site/assets/（{n} 檔）")


def main():
    creds = load_credentials()
    if creds is None:
        # fail-fast #1：CI 上沒憑證＝設定壞了，絕不讓它靜靜產出空站
        if os.environ.get("GITHUB_ACTIONS"):
            print("✗ CI 上找不到 DRIVE_SA_KEY，中止建置。", file=sys.stderr)
            sys.exit(1)
        print("⚠ 本機無 Drive 憑證，略過拉取（沿用既有 site/，只更新 assets）。")
        if (ROOT / "assets").exists():
            SITE.mkdir(parents=True, exist_ok=True)
            copy_assets()
        return

    spec = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    files = spec.get("files") or {}
    for rel, folder_id in files.items():
        if str(folder_id).startswith("FILL_ME"):
            print(f"✗ drive-manifest.yaml 的 {rel} 還是占位字串 {folder_id}——"
                  f"先建好 Drive 資料夾並回填 ID。", file=sys.stderr)
            sys.exit(1)

    svc = build_service("drive", "v3", credentials=creds, cache_discovery=False)

    grand = 0
    for rel, folder_id in files.items():
        dest = ROOT / rel
        print(f"→ 拉取 {rel}（Drive {folder_id}）", flush=True)
        n, total = pull_folder(svc, folder_id, dest)
        if n == 0:
            # 圖夾在試點初期可能還是空的，先警告不中止；HTML 由下面那道硬閘把關
            print(f"⚠ {rel} 從 Drive 拉到 0 檔——若非預期，檢查夾 {folder_id} "
                  f"是否還共用給該 SA。", flush=True)
        else:
            print(f"✓ {rel}：{n} 檔 / {total/1048576:.1f} MB", flush=True)
        grand += n

    # fail-fast #2：整個 site/ 一個 HTML 都沒有＝內容夾空了或權限掉了，
    # 中止建置以免空站上線（assets/ 不算內容）
    html_count = sum(1 for p in SITE.rglob("*.html") if "assets" not in p.parts)
    if html_count == 0:
        print("✗ 從 Drive 拉到 0 個 HTML 檔——中止建置以免空站上線。"
              "檢查 html 夾內容與 SA 共用權限。", file=sys.stderr)
        sys.exit(1)
    print(f"✓ 內容 HTML 共 {html_count} 頁")

    copy_assets()
    print(f"完成：Drive 共 {grand} 檔＋repo assets")


if __name__ == "__main__":
    main()
