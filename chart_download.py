#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
chart_download.py — 按榜单批量下载歌曲 + 歌词
==============================================
输入：chart_fetch.py 产出的榜单 JSON（或直接传平台参数现场拉榜）。
流程：榜单歌曲 → 1music.cc 搜索（拿 song_hash）→ 下载 webm → ffmpeg 转 flac
      → 按 歌手/专辑 聚合文件夹 → lyricflow 下载 .lrc 歌词。
目录规范：<out>/<歌手>/<专辑>/<歌名>.flac + <歌名>.lrc（专辑缺失回退 <歌手> - 单曲）。

token 说明：1music.cc 搜索强制要求 Turnstile token（有效期约 5 分钟）。
  - 推荐：先 curl/浏览器拿一个 token 传 --token，批量场景一个 token 可搜索约 20-30 首；
  - 或用 --browser 自动打开浏览器获取（每次 token 失效时自动重取）。
  - 下载 webm 的 backend/download/ 接口本身不需要 token。

用法：
  # 用 chart_fetch.py 产出的 JSON
  python3 chart_download.py --charts data/charts/apple_charts_2026-09-01.json --token "0.xxx" --top 3

  # 现场拉 Apple 榜前 5 首下载
  python3 chart_download.py --platform apple --top 5 --token "0.xxx"

  # 浏览器模式（自动取 token，过期自动重取）
  python3 chart_download.py --all --top 10 --browser --max-songs 6
"""
import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

import song_pipeline as sp  # 复用 OneMusicClient / to_flac / run_lyricflow / 等
from chart_fetch import fetch_apple_charts


# ---------------------------------------------------------------------------
# 搜索匹配
# ---------------------------------------------------------------------------
def search_best(client: sp.OneMusicClient, song: dict, token: str) -> dict | None:
    """在 1music.cc 搜索 title，返回与 artist 匹配的最佳结果；无 artist 时返回第一个"""
    title, artist = song.get("title", ""), song.get("artist", "")
    if not title:
        return None

    results = []
    for query in (title, f"{title} {artist}".strip()):
        try:
            results = client.search(query, token)
        except Exception:
            results = []
        if results:
            break
        time.sleep(0.3)

    if not results:
        return None

    if artist:
        kw = artist.lower()
        # 精确匹配优先，其次包含匹配
        exact = [r for r in results if kw == (r.get("artist") or "").lower()]
        if exact:
            return exact[0]
        fuzzy = [r for r in results if kw in (r.get("artist") or "").lower()]
        if fuzzy:
            return fuzzy[0]
        # 全部不匹配歌手：谨慎起见返回 None（避免下错歌）
        return None

    return results[0]


# ---------------------------------------------------------------------------
# 单曲下载（含转码落盘）
# ---------------------------------------------------------------------------
def download_one(client: sp.OneMusicClient, song: dict, fmt: str,
                 out_root: Path, keep_webm: bool) -> Path:
    """下载一首歌到 <out>/<歌手>/<专辑>/<歌名>.<fmt>，返回最终文件路径"""
    title = song.get("title", "untitled")
    artist = song.get("artist") or "未知歌手"
    album = song.get("album") or f"{artist} - 单曲"

    artist_dir = sp.safe_name(artist)
    album_dir = sp.safe_name(album)
    final_dir = out_root / artist_dir / album_dir
    final_dir.mkdir(parents=True, exist_ok=True)
    final_path = final_dir / f"{sp.safe_name(title)}.{fmt}"

    # 幂等：已存在则跳过
    if final_path.exists() and final_path.stat().st_size > 0:
        print(f"  [skip] 已存在: {final_path}")
        return final_path

    dl_url = client.request_download(song, fmt)
    tmpdir = Path(tempfile.mkdtemp(prefix="chartpipe_"))
    webm_path = tmpdir / "audio.webm"
    try:
        size = sp.download_file(dl_url, webm_path)
        print(f"  webm {size/1024/1024:.1f} MB")
        if fmt == "webm":
            shutil.move(str(webm_path), str(final_path))
        elif fmt == "mp3":
            cmd = ["ffmpeg", "-y", "-i", str(webm_path), "-vn", "-c:a", "libmp3lame", "-b:a", "320k",
                   "-metadata", f"title={song.get('title','')}",
                   "-metadata", f"artist={song.get('artist','')}",
                   "-metadata", f"album={song.get('album','')}",
                   "-metadata", "PURL=1music.cc",
                   str(final_path)]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(f"ffmpeg 转 mp3 失败: {r.stderr[-300:]}")
        else:
            sp.to_flac(webm_path, final_path, song)
    finally:
        if not keep_webm:
            shutil.rmtree(tmpdir, ignore_errors=True)
    print(f"  -> {final_path} ({final_path.stat().st_size/1024/1024:.1f} MB)")
    return final_path


# ---------------------------------------------------------------------------
def get_token(args, client: sp.OneMusicClient) -> str:
    """返回可用 token；未传且允许浏览器时自动获取"""
    if args.token:
        return args.token
    if args.browser:
        print("[token] 打开浏览器获取 Turnstile token ...")
        return sp.get_token_via_browser("test", max_wait=args.token_wait)
    raise SystemExit("[!] 需要 --token 或 --browser（1music.cc 搜索强制要求 Turnstile token）")


def main():
    ap = argparse.ArgumentParser(description="按排行榜批量下载歌曲 + 歌词")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--charts", nargs="+", help="chart_fetch.py 产出的榜单 JSON 文件（可多个）")
    src.add_argument("--platform", choices=["apple"], default="apple",
                     help="现场拉榜：Apple Music 中国区热门榜（中文歌曲榜单唯一来源）")
    src.add_argument("--all", action="store_true", help="现场拉取全部支持平台榜单（即 Apple Music）")
    ap.add_argument("--top", type=int, default=10, help="每榜单取前 N 首（默认 10）")
    ap.add_argument("--max-songs", type=int, default=0, help="总下载上限，0=不限（默认 0）")
    ap.add_argument("--format", dest="fmt", default="flac", choices=["flac", "webm", "mp3"])
    ap.add_argument("--out", default="/Users/moonightice/GitHub/Music",
                    help="输出目录（默认 ~/GitHub/Music，歌手/专辑聚合）")
    ap.add_argument("--token", default="", help="1music.cc 搜索 token")
    ap.add_argument("--browser", action="store_true", help="浏览器模式自动取 token（失效自动重取）")
    ap.add_argument("--token-wait", type=int, default=180, help="浏览器取 token 最大等待秒数")
    ap.add_argument("--no-lyrics", action="store_true", help="跳过歌词下载")
    ap.add_argument("--lyric-provider", default="lrcapi", choices=["tunehub", "lrcapi"],
                    help="歌词 provider（默认 lrcapi，tunehub 在沙箱 502）")
    ap.add_argument("--lyricflow-dir", default=str(Path(__file__).parent / "lyricflow"))
    ap.add_argument("--keep-webm", action="store_true", help="保留中间 webm")
    ap.add_argument("--charts-out", default="", help="现场拉榜时榜单 JSON 的保存目录（可选）")
    args = ap.parse_args()

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    lyricflow_dir = Path(args.lyricflow_dir)

    # ---- 1. 组装榜单歌曲列表 ----
    songs = []
    if args.charts:
        for fp in args.charts:
            try:
                songs += json.loads(Path(fp).read_text(encoding="utf-8"))
                print(f"载入榜单: {fp} ({len(json.loads(Path(fp).read_text(encoding='utf-8')))} 首)")
            except Exception as e:
                print(f"[!] 读取 {fp} 失败: {e}")
    else:
        http = httpx.Client(headers={"User-Agent": sp.UA}, timeout=25, follow_redirects=True)
        print("=== 拉取 Apple Music 中国区热门榜 ===")
        songs += fetch_apple_charts(http, args.top, True)
        http.close()
        if args.charts_out:
            from datetime import date
            Path(args.charts_out).mkdir(parents=True, exist_ok=True)
            part = [s for s in songs if s.get("source") == "apple"]
            if part:
                fp = Path(args.charts_out) / f"apple_charts_{date.today().isoformat()}.json"
                fp.write_text(json.dumps(part, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"榜单已保存: {fp}")

    if not songs:
        print("[!] 榜单为空，无歌曲可下载")
        sys.exit(1)

    if args.max_songs:
        songs = songs[: args.max_songs]
    print(f"\n待下载 {len(songs)} 首：")
    for i, s in enumerate(songs, 1):
        print(f"  {i}. {s.get('title')} — {s.get('artist')} | {s.get('album')} [{s.get('chart','')}]")

    # ---- 2. 逐首下载 ----
    client = sp.OneMusicClient()
    need_search = [s for s in songs if not (s.get("song_hash") and s.get("exp") and s.get("videoId"))]
    token = get_token(args, client) if need_search else ""
    done, failed = [], []
    album_dirs = set()

    for i, song in enumerate(songs, 1):
        print(f"\n[{i}/{len(songs)}] {song.get('title')} — {song.get('artist')}")
        try:
            if song.get("song_hash") and song.get("exp") and song.get("videoId"):
                # 榜单条目自带真实 hash+exp（如来自 backend 推荐列表），免搜索直下
                best = song
            else:
                best = search_best(client, song, token)
                if best is None:
                    # token 可能过期 → 浏览器模式自动重取一次
                    if args.browser:
                        print("  搜索无结果（可能 token 过期），重新获取 token ...")
                        token = sp.get_token_via_browser(song.get("title", ""), max_wait=args.token_wait)
                        best = search_best(client, song, token)
                    if best is None:
                        raise RuntimeError("1music.cc 无匹配结果（或 token 失效）")
                print(f"  匹配: {best.get('title')} — {best.get('artist')} | {best.get('album')}")
            merged = dict(song)
            merged.update({k: best.get(k) for k in ("album", "song_hash", "videoId", "exp") if best.get(k)})
            fp = download_one(client, merged, args.fmt, out_root, args.keep_webm)
            done.append((song, fp))
            album_dirs.add(fp.parent)
        except Exception as e:
            print(f"  [fail] {e}")
            failed.append((song, str(e)))
        time.sleep(0.5)

    client.close()

    # ---- 3. 歌词（对每个专辑目录跑一次 lyricflow）----
    if not args.no_lyrics and album_dirs and (lyricflow_dir / "src" / "main.py").exists():
        print(f"\n=== lyricflow 下载歌词（{len(album_dirs)} 个专辑目录）===")
        for d in sorted(album_dirs):
            print(f"  [{d}]")
            sp.run_lyricflow(d, lyricflow_dir, args.lyric_provider)
    elif not args.no_lyrics:
        print("\n=== 跳过歌词：无下载成功或 lyricflow 未安装 ===")

    # ---- 4. 汇总 ----
    print(f"\n=== 完成：成功 {len(done)} 首，失败 {len(failed)} 首 ===")
    if done:
        print("成功：")
        for s, fp in done:
            print(f"  ✓ {s.get('title')} — {s.get('artist')} -> {fp}")
    if failed:
        print("失败：")
        for s, e in failed:
            print(f"  ✗ {s.get('title')} — {s.get('artist')}: {e}")
    sys.exit(1 if failed and not done else 0)


if __name__ == "__main__":
    main()
