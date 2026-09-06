#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chart_download_v2：search→立即 download 紧耦合批量下载。
关键认知（2026-09-02 验证）：song_hash/exp/thumbnail 是 search 返回的一次性凭据，
约 10 分钟内失效且会被后续 search 挤掉——必须先 search 立刻 download，不可批量富化后下载。
用法:
  python chart_download_v2.py --charts data/charts/cantopop_charts_2026-09-02.json [--interval 18]
输出: /Users/moonightice/GitHub/Music/<歌手>/<专辑>/<歌名>.flac (+ .lrc)
"""
import argparse
import json
import shutil
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prefetch_reliable as pr  # robust_search / CdpPage / artist_match
import song_pipeline as sp

CDP = "http://localhost:9222"
OUT_ROOT = Path("/Users/moonightice/GitHub/Music")
LYRICFLOW_DIR = Path(__file__).resolve().parent / "lyricflow"
INTERVAL = 25.0  # 相邻 search 间隔（防软限流 200+[]，22s 是可靠富化验证阈值）


def _http_json(url):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def _existing_flac(out_root, artist, title):
    """落盘预检：Music/<artist>/**/<title归一>.flac 已存在 → 返回路径。
    避免已下载歌曲再烧一次 search/download（search 是一次性凭据 + 限流配额）。"""
    if not artist or not out_root:
        return None
    base = out_root / sp.safe_name(artist)
    if not base.is_dir():
        return None
    tn = pr._norm(title)
    for p in base.rglob("*.flac"):
        if pr._norm(p.stem) == tn:
            return p
    return None


def _probe_dur(path):
    """ffprobe 返回音频时长（秒），失败返回 0"""
    try:
        import json as _json
        import subprocess as _sp
        r = _sp.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "json", str(path)], capture_output=True, text=True, timeout=60)
        return float(_json.loads(r.stdout)["format"]["duration"])
    except Exception:
        return 0.0


def process_one(client, page, song, out_root, keep_webm=False, provider="lrcapi"):
    """单首：search(原版) → 立即 download → webm → flac。返回 (状态, 信息, album_dir)"""
    title, artist = song.get("title", ""), song.get("artist", "")
    pre = _existing_flac(out_root, artist, title)
    if pre:
        return ("dup", f"已存在: {pre}", pre.parent)
    # --- search 原版（robust：内部处理 token 过期/软限流/CDP 竞态）---
    best = None
    queries = [title] if not artist else [title, f"{title} {artist}"]
    for qi, q in enumerate(queries):
        best, how = pr.robust_search(page, q, artist, title)
        if best:
            break
        if qi == 0 and how == "giveup":
            # q1 耗尽重试（多半被软限流拖累），多歇一阵再试 q2
            print(f"  q1 放弃({how})，歇 60s 再试 q2", flush=True)
            time.sleep(60)
    if not best:
        return ("miss", f"库内无原版: {title} — {artist}", None)
    # best 为 1music search item（含新鲜 song_hash/exp/thumbnail）

    # --- 立即 download（凭据新鲜窗口内）---
    try:
        url = client.request_download(best, "flac")
    except Exception as e:
        # 偶发凭据翻转：reload 后重试一次
        page.reload_wait_token()
        try:
            best2, _ = pr.robust_search(page, queries[0], artist, title)
            if not best2:
                return ("fail", f"download 404 + 重搜无原版: {title} ({e})", None)
            url = client.request_download(best2, "flac")
        except Exception as e2:
            return ("fail", f"{title} — {artist}: {e2}", None)

    tmpdir = Path(tempfile.mkdtemp(prefix="cdl_"))
    webm = tmpdir / "audio.webm"
    try:
        size = sp.download_file(url, webm)
        # --- 归档：<歌手>/<专辑>/<歌名>.flac（目录名用榜单简体，专辑用 1music album）---
        artist_dir = sp.safe_name(artist or best.get("artist") or "未知歌手")
        album = best.get("album") or f"{artist} - 单曲"
        album_dir = out_root / artist_dir / sp.safe_name(album)
        album_dir.mkdir(parents=True, exist_ok=True)
        final = album_dir / f"{sp.safe_name(best.get('title') or title)}.flac"
        if final.exists():
            return ("dup", f"已存在: {final}", album_dir)
        sp.to_flac(webm, final, best)
        mb = final.stat().st_size / 1024 / 1024
        # --- 时长校验：榜单 duration(ms) 与 flac 实际时长比对，明显短版(剪辑/试听)判异常 ---
        chart_ms = song.get("duration") or 0
        if chart_ms > 120000:  # 榜单≥2min 才校验（防短歌误杀）
            dur = _probe_dur(final)
            ratio = dur / (chart_ms / 1000) if chart_ms else 0
            if 0 < ratio < 0.55:
                final.unlink(missing_ok=True)
                return ("fail", f"短版/剪辑版: {title} — {artist} (flac {dur:.0f}s vs 榜单 {chart_ms/1000:.0f}s)，已删除待重下", None)
        return ("ok", f"{final.relative_to(out_root)} ({mb:.1f} MB, webm {size/1024/1024:.1f}MB)", album_dir)
    except Exception as e:
        return ("fail", f"{title} — {artist}: {e}", None)
    finally:
        if not keep_webm:
            shutil.rmtree(tmpdir, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--charts", required=True)
    ap.add_argument("--out", default=str(OUT_ROOT))
    ap.add_argument("--interval", type=float, default=INTERVAL)
    ap.add_argument("--no-lyrics", action="store_true")
    ap.add_argument("--lyric-provider", default="lrcapi", choices=["tunehub", "lrcapi"])
    ap.add_argument("--keep-webm", action="store_true")
    ap.add_argument("--skip", type=int, default=0, help="跳过前 N 首（断点续跑）")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 首（0=全部）")
    args = ap.parse_args()

    songs = json.load(open(args.charts))
    if args.limit > 0:
        songs = songs[:args.limit]
    if args.skip > 0:
        songs = songs[args.skip:]
    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)

    tabs = _http_json(f"{CDP}/json")
    tab = next((t for t in tabs if "1music" in t.get("url", "")), None)
    if not tab:
        print("no 1music tab"); sys.exit(1)
    page = pr.CdpPage(tab["webSocketDebuggerUrl"])
    print("[*] reload 拿 token...", flush=True)
    page.reload_wait_token()

    client = sp.OneMusicClient()
    stats = {"ok": 0, "dup": 0, "miss": 0, "fail": 0}
    new_album_dirs = []
    failed = []

    todo = songs
    print(f"[*] 批量紧耦合下载 {len(todo)} 首（间隔 {args.interval}s）", flush=True)
    for i, song in enumerate(todo, 1):
        idx = args.skip + i
        title, artist = song.get("title", ""), song.get("artist", "")
        st, info, album_dir = process_one(client, page, song, out_root,
                                          keep_webm=args.keep_webm, provider=args.lyric_provider)
        stats[st] += 1
        mark = {"ok": "OK ", "dup": "DUP", "miss": "MISS", "fail": "FAIL"}[st]
        print(f"[{idx}/{len(songs)}] {mark} {info}", flush=True)
        if album_dir:
            new_album_dirs.append(Path(album_dir))
        if st == "fail":
            failed.append(f"{title} — {artist}")
        time.sleep(args.interval)
        if i % 5 == 0:
            print(f"  --- 进度 {idx}/{len(songs)} ok={stats['ok']} miss={stats['miss']} fail={stats['fail']} ---", flush=True)
            page.reload_wait_token()

    # --- 歌词：对出现过的专辑目录统一跑 lyricflow ---
    if not args.no_lyrics and (LYRICFLOW_DIR / "src" / "main.py").exists():
        print("\n=== 歌词下载 ===", flush=True)
        seen = set()
        for d in new_album_dirs:
            d = Path(d)
            if d in seen or not d.is_dir():
                continue
            seen.add(d)
            print(f"  lyricflow → {d}", flush=True)
            try:
                sp.run_lyricflow(d, LYRICFLOW_DIR, args.lyric_provider)
            except Exception as e:
                print(f"    歌词失败: {e}", flush=True)
    else:
        print("\n=== 跳过歌词（--no-lyrics 或 lyricflow 缺失）===")

    print("\n=== 汇总 ===", flush=True)
    for k, v in stats.items():
        print(f"  {k}: {v}", flush=True)
    if failed:
        print("  失败项:", flush=True)
        for f in failed:
            print(f"    {f}", flush=True)
    client.close()


if __name__ == "__main__":
    main()
