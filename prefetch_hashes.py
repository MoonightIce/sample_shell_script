#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
prefetch_hashes.py — 用一次 Turnstile token 把榜单全部富化为「可免搜索直下」条目
==================================================================================
问题背景：1music.cc 搜索强制要求 Turnstile token（有效期约 5 分钟），而逐首
「搜索→下载」架构会让下载过程中 token 反复过期，需要用户多次在浏览器里点验证。

本脚本解决方式：拿一次 token → 在有效期内把榜单里所有歌曲一次性 search 完，
把 song_hash / videoId / exp / album 写回 JSON。此后 chart_download.py 读该 JSON
时 need_search 为空，下载全程不依赖 token，可完全无人值守后台跑。

用法：
  # 浏览器弹窗取 token（用户手动过 Turnstile，仅此一次交互）
  python3 prefetch_hashes.py --charts data/charts/cantopop_charts_2026-09-02.json --browser

  # 直接给 token
  python3 prefetch_hashes.py --charts data/charts/cantopop_charts_2026-09-02.json --token "0.xxx"

输出：<charts 同目录>/<原名>.prefetched.json（可被 chart_download.py --charts 直接消费）
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import song_pipeline as sp
import chart_download as cd  # 复用 search_best


def acquire_token(args, client) -> str:
    if args.token:
        return args.token
    if args.browser:
        print("[token] 打开浏览器获取 Turnstile token ...")
        return sp.get_token_via_browser("test", max_wait=args.token_wait)
    raise SystemExit("[!] 需要 --token 或 --browser（1music.cc 搜索强制要求 Turnstile token）")


def main():
    ap = argparse.ArgumentParser(description="用一次 token 富化榜单为可免搜索直下条目")
    ap.add_argument("--charts", required=True, help="chart_fetch.py 产出的榜单 JSON")
    ap.add_argument("--out", default="", help="输出 JSON（默认 <charts 前名>.prefetched.json 同目录）")
    ap.add_argument("--token", default="", help="1music.cc 搜索 token（0.xxx）")
    ap.add_argument("--browser", action="store_true", help="浏览器弹窗自动取 token（失效自动重取一次）")
    ap.add_argument("--token-wait", type=int, default=150, help="浏览器取 token 最大等待秒数")
    args = ap.parse_args()

    src = Path(args.charts)
    songs = json.loads(src.read_text(encoding="utf-8"))
    if not songs:
        print("[!] 榜单为空")
        sys.exit(1)
    out_path = Path(args.out) if args.out else src.with_name(src.stem + ".prefetched.json")

    client = sp.OneMusicClient()
    token = acquire_token(args, client)

    already = sum(1 for s in songs if s.get("song_hash") and s.get("exp") and s.get("videoId"))
    print(f"榜单 {len(songs)} 首（已自带 hash+exp {already} 首），开始富化 ...")
    ok = fail = 0
    t0 = time.time()
    for i, s in enumerate(songs, 1):
        title, artist = s.get("title", ""), s.get("artist", "")
        if s.get("song_hash") and s.get("exp") and s.get("videoId"):
            ok += 1
            continue
        best = None
        for attempt in range(2):
            best = cd.search_best(client, s, token)
            if best is not None:
                break
            # 首个 token 可能拿到即失效/过期 → 浏览器模式重新取一次
            if args.browser and attempt == 0:
                print(f"  [{i}] token 无结果（可能失效），重新获取 ...")
                try:
                    token = sp.get_token_via_browser(title or "test", max_wait=args.token_wait)
                except Exception as e:
                    print(f"  [{i}] 重取 token 失败: {e}")
                    break
        if best and best.get("song_hash") and best.get("videoId") and best.get("exp"):
            for k in ("song_hash", "videoId", "exp", "album"):
                if best.get(k):
                    s[k] = best[k]
            ok += 1
        else:
            print(f"  [{i}] 无匹配: {title} — {artist}")
            fail += 1
        time.sleep(0.25)
    client.close()

    Path(out_path).write_text(json.dumps(songs, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n完成：富化 {ok} 首 / 失败 {fail} 首，耗时 {time.time()-t0:.0f}s -> {out_path}")
    if fail:
        print("[提示] 失败的条目可稍后用新 token 重跑本脚本（已富化的会跳过）")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
