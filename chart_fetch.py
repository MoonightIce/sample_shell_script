#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
chart_fetch.py — 获取 Apple Music 中国区热门中文歌曲排行榜
============================================================
数据源（2026-09 验证可用）：
  - Apple Music 中国区热门榜：https://music.apple.com/cn/charts
      页面内嵌 <script id="serialized-server-data"> JSON，
      data[0].data.sections 中 id=most-played 的 items[] 即榜单歌曲。
      字段：title / subtitleLinks[0].title(歌手) / rankingText(排名) /
           playAction.items[0].contentDescriptor.url(歌曲链接，含专辑名+albumID+trackID) / duration
  - 中文过滤：歌名或歌手含 CJK 字符（\u4e00-\u9fff）即视为中文歌曲。

说明（2026-09 决策）：曾用 Spotify 中国香港/中国台湾日榜（kworb）作为"热门中文歌曲"补充源，
但港台榜被本地偶像（MIRROR 系等）霸榜、与内地听众口味差异极大，且 kworb 无专辑信息，
故已移除。中文歌曲榜单一律以 Apple Music 中国区榜为准。

用法：
  # Apple Music 中国区热门榜前 10 首中文歌（默认）
  python3 chart_fetch.py --top 10

  # 粤语流行热门歌曲（Apple Music 粤语流行编辑页「热门歌曲」，61 首）
  python3 chart_fetch.py --genre cantopop --top 20

  # 输出 JSON 文件到 data/charts
  python3 chart_fetch.py --genre cantopop --top 20 --out data/charts

  # stdout 输出 JSON（供管道/程序消费）
  python3 chart_fetch.py --genre cantopop --top 5 --json

输出：每首歌 dict：{rank, title, artist, album, duration, source, chart, url}
"""
import argparse
import json
import re
import sys
import urllib.parse
from datetime import date
from pathlib import Path

import httpx

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

APPLE_CHARTS_URL = "https://music.apple.com/cn/charts"
# Apple Music 粤语流行编辑页「热门歌曲」Room（2026-09 验证：61 首纯粤语歌）
APPLE_CANTOPOP_ROOM_URL = "https://music.apple.com/cn/room/6503392786"

CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def is_chinese(text: str) -> bool:
    """含中文字符即视为中文"""
    return bool(CJK_RE.search(text or ""))


# ---------------------------------------------------------------------------
# Apple Music
# ---------------------------------------------------------------------------
def fetch_apple_charts(client: httpx.Client, top: int, chinese_only: bool) -> list[dict]:
    r = client.get(APPLE_CHARTS_URL)
    r.raise_for_status()
    m = re.search(
        r'<script type="application/json" id="serialized-server-data"[^>]*>(.*?)</script>',
        r.text, re.S)
    if not m:
        raise RuntimeError("Apple Music 页面未找到 serialized-server-data")

    data = json.loads(m.group(1))
    sections = data["data"][0]["data"]["sections"]
    sec = next((s for s in sections if s.get("id") == "most-played"), None)
    if not sec:
        raise RuntimeError("Apple Music 页面未找到 most-played 榜单 section")

    songs = []
    for it in sec.get("items", []):
        title = it.get("title", "")
        subtitle = it.get("subtitleLinks") or []
        artist = subtitle[0].get("title", "") if subtitle else ""
        # 歌曲链接：https://music.apple.com/cn/album/<歌名编码>/<albumID>?i=<trackID>
        url = ""
        pa = it.get("playAction") or {}
        pa_items = pa.get("items") or []
        if pa_items:
            cd = pa_items[0].get("contentDescriptor") or {}
            url = cd.get("url", "")
        # 专辑名：URL 中 album 段是歌名而非专辑名，须用 iTunes lookup 按 trackID 查 collectionName
        album = ""
        track_id = ""
        if url:
            q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            track_id = (q.get("i") or [""])[0]
        if track_id:
            try:
                lu = client.get(
                    "https://itunes.apple.com/lookup",
                    params={"id": track_id, "country": "cn"})
                lu.raise_for_status()
                lu_data = lu.json()
                if lu_data.get("results"):
                    album = lu_data["results"][0].get("collectionName", "")
            except Exception:
                pass
        if chinese_only and not (is_chinese(title) or is_chinese(artist)):
            continue
        songs.append({
            "rank": it.get("rankingText"),
            "title": title,
            "artist": artist,
            "album": album,
            "duration": it.get("duration"),
            "source": "apple",
            "chart": "apple-music-cn-charts",
            "url": url,
        })
        if len(songs) >= top:
            break
    return songs


# ---------------------------------------------------------------------------
# Apple Music 粤语流行（编辑页「热门歌曲」Room）
# ---------------------------------------------------------------------------
def fetch_apple_cantopop(client: httpx.Client, top: int) -> list[dict]:
    """Apple Music 粤语流行编辑页的热门歌曲（Room 页），61 首纯粤语歌。

    URL: /cn/room/6503392786（curator 1019398918「粤语流行」页面的热门歌曲 see-all）
    """
    r = client.get(APPLE_CANTOPOP_ROOM_URL)
    r.raise_for_status()
    m = re.search(
        r'<script type="application/json" id="serialized-server-data"[^>]*>(.*?)</script>',
        r.text, re.S)
    if not m:
        raise RuntimeError("Apple Music 粤语 Room 页未找到 serialized-server-data")

    data = json.loads(m.group(1))
    sections = data["data"][0]["data"]["sections"]
    sec = next((s for s in sections if "copper-track" in s.get("id", "")), None)
    if not sec:
        raise RuntimeError("Apple Music 粤语 Room 页未找到歌曲列表 section")

    songs = []
    for it in sec.get("items", []):
        title = it.get("title", "")
        artist = it.get("artistName") or ""
        if not artist:
            subtitle = it.get("subtitleLinks") or []
            artist = subtitle[0].get("title", "") if subtitle else ""
        # 歌曲链接：https://music.apple.com/cn/album/<名>/<albumID>?i=<trackID>
        url = ""
        pa = it.get("playAction") or {}
        pa_items = pa.get("items") or []
        if pa_items:
            cd = pa_items[0].get("contentDescriptor") or {}
            url = cd.get("url", "")
        album = ""
        track_id = ""
        if url:
            q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            track_id = (q.get("i") or [""])[0]
        if track_id:
            try:
                lu = client.get(
                    "https://itunes.apple.com/lookup",
                    params={"id": track_id, "country": "cn"})
                lu.raise_for_status()
                lu_data = lu.json()
                if lu_data.get("results"):
                    album = lu_data["results"][0].get("collectionName", "")
            except Exception:
                pass
        songs.append({
            "rank": len(songs) + 1,
            "title": title,
            "artist": artist,
            "album": album,
            "duration": it.get("duration"),
            "source": "apple",
            "chart": "apple-music-cantopop-hot",
            "url": url,
        })
        if len(songs) >= top:
            break
    return songs


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="获取 Apple Music 中国区热门中文歌曲排行榜")
    ap.add_argument("--genre", choices=["all", "cantopop"], default="all",
                    help="榜单类型：all=中国区热门榜（默认），cantopop=粤语流行热门歌曲")
    ap.add_argument("--top", type=int, default=10, help="取前 N 首（默认 10）")
    ap.add_argument("--no-chinese-filter", action="store_true", help="不过滤非中文歌曲（仅 all 榜生效）")
    ap.add_argument("--out", default="", help="输出目录（默认只打印；给目录则写 JSON 文件）")
    ap.add_argument("--json", action="store_true", help="stdout 输出 JSON（供管道使用）")
    args = ap.parse_args()

    client = httpx.Client(headers={"User-Agent": UA}, timeout=25, follow_redirects=True)
    if args.genre == "cantopop":
        print("=== Apple Music 粤语流行热门歌曲 ===")
        songs = fetch_apple_cantopop(client, args.top)
        suffix = "cantopop"
    else:
        print("=== Apple Music 中国区热门榜 ===")
        songs = fetch_apple_charts(client, args.top, not args.no_chinese_filter)
        suffix = "apple"
    client.close()
    print(f"  获取 {len(songs)} 首")
    for s in songs:
        print(f"  #{s['rank']} {s['title']} — {s['artist']} | {s['album']}")

    if args.out:
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        fp = out_dir / f"{suffix}_charts_{date.today().isoformat()}.json"
        fp.write_text(json.dumps(songs, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已写入: {fp}")
    if args.json:
        print(json.dumps({suffix: songs}, ensure_ascii=False))


if __name__ == "__main__":
    main()
