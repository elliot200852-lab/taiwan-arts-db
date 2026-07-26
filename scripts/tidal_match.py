#!/usr/bin/env python3
"""把 content/songs/*.yaml 的九期曲目比對到 TIDAL，並（選用）建立每期一個 TIDAL 歌單。

用法：
    python3 scripts/tidal_match.py                    # 只比對、出報告，不動 TIDAL 帳號
    python3 scripts/tidal_match.py --create-playlists # 比對後建立／更新九個歌單
    python3 scripts/tidal_match.py --era era-78rpm    # 只跑一期（除錯用）

前置：已安裝 @lucaperret/tidal-cli 並跑過 `tidal-cli auth`。

版本挑選規則（David 2026-07-25 授權由 AI 決定，規則寫死在這裡以便日後稽核）：
  1. 歌名要對得上（CJK 正規化後完全相同 > 互相包含），對不上直接淘汰。
  2. 演唱者優先序：原唱 > 本庫 listen 欄位點名的版本演唱者 > 其他。
  3. 同分再比 TIDAL popularity。
每一筆都會記錄命中的是哪一條規則（confidence 欄位），報告可逐首覆核。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SONGS_DIR = ROOT / "content" / "songs"
OUT_DIR = ROOT / "_build"
TIDAL_PLAYLISTS_YAML = SONGS_DIR / "tidal-playlists.yaml"

# 所有 tidal-cli 呼叫一律序列執行、經 tidal-guard 的跨程序鎖（2026-07-26 審查校準）。
# tidal-cli 的憑證檔沒有原子寫入，並發呼叫是 A0004 損毀的唯一病因；鎖之下開多條
# worker 只是排隊搶鎖，反而更容易撞 90 秒 acquire timeout，所以不再用 ThreadPoolExecutor。
SEARCH_TAKE = 20   # 從搜尋結果取前幾筆進入評分（TIDAL 一次回 20 筆）
INFO_TAKE = 8      # 其中最多幾筆去查 track info（要拿演唱者）

# TIDAL 的中文詮釋資料簡繁混雜（「鳳飛飛」在 TIDAL 上寫「凤飞飞」、「望春風」寫「望春风」）。
# 不折疊簡繁，大量原唱其實命中卻會被判成「非原唱」。opencc 缺席時降級為不折疊，
# 腳本照跑、只是命中率會掉。安裝：pip3 install --user opencc-python-reimplemented
try:
    from opencc import OpenCC as _OpenCC
    _T2S = _OpenCC("t2s")

    def fold(s: str) -> str:
        return _T2S.convert(s or "")
except Exception:  # pragma: no cover
    def fold(s: str) -> str:
        return s or ""

CJK = re.compile(r"[㐀-䶿一-鿿豈-﫿]")


class TidalError(RuntimeError):
    """tidal-cli／tidal-guard 呼叫失敗。任何失敗都要浮上來，不得靜默降級成空結果。"""


class TidalCredentialError(TidalError):
    """偵測到 A0004（憑證檔壞掉）。呼叫端必須整批中止，不得繼續跑、不得降級成 not_found。"""


def run_tidal(args: list[str], timeout: int = 280):
    """經 `tidal-guard run --` 呼叫 tidal-cli --json。

    所有 tidal-cli 呼叫都必須走 tidal-guard 的跨程序鎖——tidal-cli 的憑證檔沒有
    原子寫入，並發呼叫是 A0004 損毀的唯一病因（2026-07-26 查明，見 music-mcp/README.md
    與 memory reference_tidal_cli_a0004_storage）。逾時給到 280 秒是刻意留寬：
    tidal-guard 自己鎖 acquire 上限 90 秒、內部 exec timeout 180 秒，Python 這層的
    timeout 只是最後防線，不該比它們先觸發（先觸發等於我們自己 SIGKILL 掉正在持鎖的
    tidal-guard，那正是 README 記的第二個憑證損毀病因）。
    """
    cmd = ["tidal-guard", "run", "--", "--json", *args]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise TidalError(f"tidal-guard 逾時未回應（{timeout}s，指令：{' '.join(args)}）") from e
    except FileNotFoundError as e:
        raise TidalError(
            "找不到 tidal-guard（預期在 ~/.local/bin）。所有 tidal-cli 呼叫都必須經過它的鎖，"
            "不能直接呼叫 tidal-cli。"
        ) from e

    stdout = (p.stdout or "").strip()
    stderr = (p.stderr or "").strip()

    if "A0004" in stderr or "A0004" in stdout:
        raise TidalCredentialError(
            "偵測到 A0004（TIDAL 憑證檔壞掉，不是 token 過期）。"
            "先跑 `tidal-guard doctor` 修復憑證後重來——本次整批已中止。"
            f"（指令：tidal-cli {' '.join(args)}；stderr：{stderr[:200]}）"
        )

    if p.returncode != 0:
        summary = (stderr or stdout)[:200] or "(無輸出)"
        raise TidalError(
            f"tidal-cli 失敗（exit {p.returncode}，指令：{' '.join(args)}）：{summary}"
        )

    if not stdout:
        raise TidalError(f"tidal-cli 沒有輸出（指令：{' '.join(args)}）")

    try:
        return json.loads(stdout)
    except json.JSONDecodeError as e:
        raise TidalError(
            f"tidal-cli 輸出不是合法 JSON（指令：{' '.join(args)}）：{stdout[:200]}"
        ) from e


def norm(s: str) -> str:
    """正規化：全形轉半形、去標點空白、小寫。用於歌名與人名比對。"""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", fold(s))
    s = re.sub(r"[\s　]+", "", s)
    s = re.sub(r"[^\w㐀-鿿豈-﫿]", "", s, flags=re.UNICODE)
    return s.lower()


def cjk_core(s: str) -> str:
    """只留漢字。TIDAL 上台語老歌常掛英譯副標（「生蚵仔嫂 The Oysterman's Wife」），
    用漢字核心比對才不會因為副標而漏掉。"""
    return "".join(CJK.findall(fold(s) or ""))


def expected_artists(song: dict) -> list[str]:
    """本庫對這首歌認可的演唱者：原唱優先，其次是 listen 欄位點名的版本演唱者。"""
    out = []
    singer = (song.get("credits") or {}).get("original_singer")
    if singer:
        # 「純純」「鳳飛飛」；也可能是「A、B」或「A／B」的合唱
        out.extend(re.split(r"[、,，/／&＆與和]", singer))
    for item in song.get("listen") or []:
        label = item.get("label") or ""
        # label 形如「許石編曲・太王管弦樂團復刻版（…）」，取括號前、分隔符前的人名片段
        head = re.split(r"[（(]", label)[0]
        out.extend(re.split(r"[・·、,，/／]", head))
    cleaned = []
    for a in out:
        a = re.sub(r"(編曲|演唱|原唱|版本|復刻版|翻唱|現場|版)$", "", a.strip())
        if a and len(a) >= 2:
            cleaned.append(a)
    # 去重保序
    seen, uniq = set(), []
    for a in cleaned:
        if a not in seen:
            seen.add(a)
            uniq.append(a)
    return uniq


def title_score(song_title: str, cand_title: str) -> float:
    a_core, b_core = cjk_core(song_title), cjk_core(cand_title)
    a_norm, b_norm = norm(song_title), norm(cand_title)
    if a_core and b_core:
        if a_core == b_core:
            return 1.0
        if a_core in b_core or b_core in a_core:
            # 包含比對很容易誤中：短的那側必須夠長，且兩邊長度不能差太多。
            # 反例（未收緊前實際踩到的）：
            #   「珈琲！珈琲！」核心 珈琲珈琲 ⊃「アメリカン珈琲」核心 珈琲 → 誤判成同一首
            #   「誉れの軍夫」核心 軍夫 ⊂「デス夫のブルース」核心 夫 → 同樣是雜訊
            shorter, longer = sorted((len(a_core), len(b_core)))
            # 前綴命中是強證據（「青蘋果樂園+星星的約會」＝合輯串曲，確實包含這首），
            # 不受長度比限制；中段包含才需要長度比把關。
            if shorter >= 3 and (b_core.startswith(a_core) or a_core.startswith(b_core)):
                return 0.9
            if shorter >= 3 and shorter / longer >= 0.6:
                return 0.8
        return 0.0
    if a_norm and b_norm:
        if a_norm == b_norm:
            return 1.0
        if a_norm in b_norm or b_norm in a_norm:
            return 0.8 if len(a_norm) >= 4 else 0.5
    return 0.0


def artist_score(expected: list[str], cand_artists: list[str]) -> tuple[float, str]:
    if not cand_artists:
        return 0.0, ""
    cand_norm = [norm(a) for a in cand_artists]
    for rank, exp in enumerate(expected):
        e = norm(exp)
        if not e:
            continue
        for orig, cn in zip(cand_artists, cand_norm):
            if e == cn or (len(e) >= 2 and (e in cn or cn in e)):
                # 名單前面的（原唱）給滿分，後面的（listen 點名版本）次之
                return (1.0 if rank == 0 else 0.8), orig
    return 0.0, ""


def artist_first_lookup(title: str, singers: list[str]):
    """補救路徑：TIDAL 對中文的「曲目」搜尋不完整，原唱版本常常不在前 20 筆裡
    （實測：小虎隊〈青蘋果樂園〉曲目搜尋找不到，但從藝人頁的代表作就找得到，
    它在 TIDAL 上收在「青蘋果樂園+星星的約會」串曲裡）。
    所以主掃描沒對到演唱者時，改從藝人頁的代表作再找一次。"""
    for s in singers[:2]:
        arts = run_tidal(["search", "artist", s])
        if not isinstance(arts, list):
            continue
        hit = next((a for a in arts if norm(a.get("name", "")) == norm(s)), None)
        if not hit:
            continue
        tracks = run_tidal(["artist", "tracks", str(hit["id"])])
        if not isinstance(tracks, list):
            continue
        best_t, best_ts = None, 0.0
        for t in tracks:
            tt = t.get("title") or t.get("name") or ""
            ts = title_score(title, tt)
            if ts > best_ts:
                best_t, best_ts = t, ts
        if best_t is not None and best_ts > 0:
            return {
                "id": str(best_t["id"]),
                "tidal_title": best_t.get("title") or best_t.get("name"),
                "artists": [hit["name"]],
                "album": None,
                "isrc": best_t.get("isrc"),
                "popularity": float(best_t.get("popularity") or 0),
                "title_score": best_ts,
                "artist_score": 1.0,
                "matched_artist": hit["name"],
                "total": best_ts * 2 + 3.0,
            }
    return None


def match_song(song: dict) -> dict:
    title = song["title"]
    exp = expected_artists(song)
    singer = (song.get("credits") or {}).get("original_singer") or ""

    # ⚠️ 純歌名要放第一、而且兩條查詢都要跑完不能短路。
    # TIDAL 把「歌名 藝人」當成模糊多詞查詢，中文尤其糟：查「出走 蔡健雅」第一名回
    # 「My Jinji」，而且照樣回滿 20 筆垃圾。早期版本湊滿門檻就 break，於是純歌名那條
    # 永遠沒機會跑 —— 單查「出走」「又到天黑」其實一查就中。這是 103 首「找不到」的主因。
    queries = [title]
    if singer:
        queries.append(f"{title} {singer}".strip())

    candidates, seen_ids = [], set()
    for q in queries:
        res = run_tidal(["search", "track", q])
        if not isinstance(res, list):
            continue
        for t in res[:SEARCH_TAKE]:
            tid = str(t.get("id") or "")
            if tid and tid not in seen_ids:
                seen_ids.add(tid)
                candidates.append(t)

    if not candidates:
        return {**base(song), "status": "not_found", "reason": "TIDAL 搜尋無結果"}

    # 先用歌名過濾，只有通過的才值得花一次 track info 去查演唱者
    scored = []
    for c in candidates:
        ts = title_score(title, c.get("name") or "")
        if ts > 0:
            scored.append((ts, float((c.get("extra") or {}).get("popularity") or 0), c))
    if not scored:
        return {**base(song), "status": "not_found",
                "reason": f"有搜尋結果但歌名都對不上（如：{candidates[0].get('name','')[:30]}）"}

    scored.sort(key=lambda x: (-x[0], -x[1]))
    best = None
    for ts, pop, c in scored[:INFO_TAKE]:
        info = run_tidal(["track", "info", str(c["id"])])
        artists = (info or {}).get("artists") or []
        a_s, matched_name = artist_score(exp, artists)
        total = ts * 2 + a_s * 3 + pop  # 演唱者權重最高，歌名次之，人氣只當 tie-break
        row = {
            "id": str(c["id"]),
            "tidal_title": (info or {}).get("title") or c.get("name"),
            "artists": artists,
            "album": (info or {}).get("album"),
            "isrc": (info or {}).get("isrc") or (c.get("extra") or {}).get("isrc"),
            "popularity": pop,
            "title_score": ts,
            "artist_score": a_s,
            "matched_artist": matched_name,
            "total": total,
        }
        if best is None or total > best["total"]:
            best = row
        if ts == 1.0 and a_s == 1.0:
            break  # 歌名與原唱都完全命中，不必再查下去

    if best["artist_score"] == 0.0 and exp:
        rescue = artist_first_lookup(title, exp)
        if rescue is not None:
            best = rescue

    if best["artist_score"] >= 1.0 and best["title_score"] >= 1.0:
        conf, rule = "high", "歌名完全相符＋原唱本人"
    elif best["artist_score"] >= 0.8 and best["title_score"] >= 0.8:
        conf, rule = "high", "歌名相符＋本庫點名的版本演唱者"
    elif best["title_score"] >= 1.0:
        conf, rule = "medium", "歌名完全相符，演唱者非原唱（取人氣最高者）"
    else:
        conf, rule = "low", "只有歌名部分相符，需人工覆核"

    return {**base(song), "status": "matched", "confidence": conf, "rule": rule, **best}


def base(song: dict) -> dict:
    return {
        "song_id": song.get("id"),
        "title": song["title"],
        "year": song.get("year"),
        "era": song.get("era"),
        "original_singer": (song.get("credits") or {}).get("original_singer"),
    }


def load_eras(only: str | None):
    eras = {}
    for f in sorted(SONGS_DIR.glob("*.yaml")):
        slug = f.stem
        if only and slug != only:
            continue
        md = SONGS_DIR / f"{slug}.md"
        meta = {}
        if md.exists():
            text = md.read_text(encoding="utf-8")
            if text.startswith("---"):
                meta = yaml.safe_load(text.split("---")[1]) or {}
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        eras[slug] = {
            "slug": slug,
            "title": meta.get("title", slug),
            "period": meta.get("period", ""),
            "order": meta.get("order", 99),
            "songs": data.get("songs") or [],
        }
    return dict(sorted(eras.items(), key=lambda kv: kv[1]["order"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--era")
    ap.add_argument("--create-playlists", action="store_true")
    args = ap.parse_args()

    eras = load_eras(args.era)
    total = sum(len(e["songs"]) for e in eras.values())
    print(f"共 {len(eras)} 期 / {total} 首，開始比對（序列執行，經 tidal-guard 鎖）…\n", flush=True)

    results = {}
    done = 0
    for slug, era in eras.items():
        rows = [match_song(s) for s in era["songs"]]
        results[slug] = rows
        done += len(rows)
        hi = sum(1 for r in rows if r.get("confidence") == "high")
        md = sum(1 for r in rows if r.get("confidence") == "medium")
        lo = sum(1 for r in rows if r.get("confidence") == "low")
        nf = sum(1 for r in rows if r.get("status") == "not_found")
        print(f"  {era['title']:<8} {len(rows):>3} 首 → 高信心 {hi:>2} / 中 {md:>2} / 低 {lo:>2} / 找不到 {nf:>2}"
              f"   [{done}/{total}]", flush=True)

    OUT_DIR.mkdir(exist_ok=True)
    payload = {"eras": {s: eras[s] | {"songs": None} for s in eras}, "results": results}
    (OUT_DIR / "tidal-match-report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n報告：{OUT_DIR / 'tidal-match-report.json'}")

    if args.create_playlists:
        create_playlists(eras, results)


def create_playlists(eras, results):
    """建立／更新九個歌單。

    冪等設計（2026-07-26 P1 修正，David 拍板）：CLI／MCP 都沒有「讀回歌單曲目」
    的能力（審查報告 C-3），沒辦法用伺服器端狀態防重複。改用客戶端帳本——
    上次成功加入的 track id 記在 tidal-playlists.yaml 的 added_track_ids，
    重跑時只對「還沒記錄成功」的曲目補加，記錄過的一律跳過，不會重複加歌。
    加歌部分失敗時，那幾首不標進 added_track_ids（下次會再補試一次），
    也不吞成一個數字——逐首列進 failed 清單並印出來。
    """
    print("\n=== 建立 TIDAL 歌單 ===")
    existing = run_tidal(["playlist", "list"])
    if not isinstance(existing, list):
        existing = []
    by_name = {p["name"]: p["id"] for p in existing if isinstance(p, dict)}

    prev_eras = {}
    if TIDAL_PLAYLISTS_YAML.exists():
        prev_eras = (yaml.safe_load(TIDAL_PLAYLISTS_YAML.read_text(encoding="utf-8")) or {}).get("eras") or {}

    out = {}
    any_failed = False

    for slug, era in eras.items():
        # 低信心的一律不進公開歌單：這是要掛在網站上給人點的，寧可漏收也不要放錯歌
        rows = [r for r in results[slug]
                if r.get("status") == "matched" and r.get("confidence") in ("high", "medium")]
        if not rows:
            print(f"  {era['title']}：零命中，跳過")
            continue
        name = f"臺灣歌謠 {era['order']:02d}・{era['title']}（{era['period']}）"
        hi = sum(1 for r in rows if r.get("confidence") == "high")
        desc = (f"《認識臺灣・臺灣人文藝術資料庫》歌曲線第 {era['order']} 期"
                f"（{era['period']}）。本期收錄 {len(results[slug])} 首，"
                f"TIDAL 可得 {len(rows)} 首，其中 {hi} 首為原唱或本庫指定版本，"
                f"其餘為後人翻唱——早期曲盤錄音多不在串流平台，聽到的旋律相同但非原始錄音。"
                f"原始典藏錄音請見資料庫網站。")

        pid = by_name.get(name)
        if pid:
            print(f"  {era['title']}：沿用既有歌單 {pid}")
        else:
            created = run_tidal(["playlist", "create", "--name", name, "--desc", desc])
            pid = (created or {}).get("id")
            if not pid:
                print(f"  ❌ {era['title']}：建立失敗（回應沒有 id：{created!r}）")
                any_failed = True
                continue
            print(f"  {era['title']}：建立 {pid}")

        # 只有「上次記錄的也是同一個歌單」才信任那份 added_track_ids；
        # 歌單換了（例如同名被刪掉重建）就當全新的，全部重補。
        prev_era = prev_eras.get(slug) or {}
        already_added = (set(prev_era.get("added_track_ids") or [])
                          if prev_era.get("playlist_id") == pid else set())

        to_add = [r for r in rows if r["id"] not in already_added]
        skipped = len(rows) - len(to_add)
        if skipped:
            print(f"     已標記加入過 {skipped} 首，跳過只補缺 {len(to_add)} 首")

        newly_added, failed = [], []
        for r in sorted(to_add, key=lambda x: (x.get("year") or 0)):
            try:
                run_tidal(["playlist", "add-track", "--playlist-id", pid, "--track-id", r["id"]])
                newly_added.append(r["id"])
            except TidalCredentialError:
                # 憑證壞掉：繼續加下去只會製造更多壞資料，整批中止。
                raise
            except TidalError as e:
                failed.append({"song_id": r.get("song_id"), "title": r.get("title"),
                                "tidal_id": r["id"], "error": str(e)[:200]})

        added_ids = sorted(already_added | set(newly_added))
        out[slug] = {"playlist_id": pid, "name": name,
                     "url": f"https://tidal.com/browse/playlist/{pid}",
                     "added": len(added_ids), "added_track_ids": added_ids,
                     "matched": len(rows), "total": len(results[slug]),
                     "failed": failed}
        print(f"     本次新增 {len(newly_added)}/{len(to_add)} 首"
              f"（累計 {len(added_ids)}/{len(rows)}）→ https://tidal.com/browse/playlist/{pid}")
        if failed:
            any_failed = True
            print(f"     ⚠️  {len(failed)} 首加入失敗（下次重跑會自動補試）：")
            for f in failed:
                print(f"        - {f['title']}（tidal id={f['tidal_id']}）：{f['error']}")

    (OUT_DIR / "tidal-playlists.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    # 同時寫進 content/（純文字、進 git）：build_pages.py 從這裡讀 name/url/added，才能在
    # 每期「這個時代的歌」標題列渲染 TIDAL 按鈕。_build/ 那份只是執行紀錄。
    TIDAL_PLAYLISTS_YAML.write_text(
        "# 由 scripts/tidal_match.py --create-playlists 產生，勿手改。\n"
        "# 每期一個 TIDAL 歌單；build_pages.py 讀 name/url/added 渲染歌單按鈕。\n"
        "# added_track_ids／failed 是冪等帳本：重跑只補缺，不會重複加歌（2026-07-26）。\n"
        + yaml.safe_dump({"eras": out}, allow_unicode=True, sort_keys=False),
        encoding="utf-8")
    print(f"\n歌單索引：{OUT_DIR / 'tidal-playlists.json'}")
    print(f"建置用資料：{TIDAL_PLAYLISTS_YAML}")
    if any_failed:
        print("\n⚠️  本次執行有部分曲目建立/加入失敗，詳見上方清單與 tidal-playlists.json 的 failed 欄位。")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except TidalCredentialError as e:
        print(f"\n❌ 中止（憑證損毀）：{e}", file=sys.stderr)
        sys.exit(1)
    except TidalError as e:
        print(f"\n❌ 中止：{e}", file=sys.stderr)
        sys.exit(1)
