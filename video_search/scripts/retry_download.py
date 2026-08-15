#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
retry_download.py — 每3分钟重试分段下载直到成功或达上限。
用法: python retry_download.py <page_url> <out.mp4> [max_attempts]
"""
import sys, subprocess, time
from pathlib import Path

PY = sys.executable

def already_done(out, expect_sec):
    if not out.exists() or out.stat().st_size < 10_000_000:  # >10MB 才算有实质
        return False
    r = subprocess.run(["ffprobe","-v","quiet","-show_entries","format=duration",
                        "-of","csv=p=0",str(out)], capture_output=True, text=True)
    try:
        dur = float(r.stdout.strip())
    except Exception:
        return False
    return dur >= expect_sec * 0.95  # 达到预期时长95%即认为完成

def main(page_url, out_path, max_attempts=20, interval=180):
    out = Path(out_path)
    vid = page_url.rstrip("/").split("/")[-1]
    script = Path(__file__).resolve().parent / "download_segmented.py"
    # 探测预期时长(用probe_duration)
    import asyncio, importlib.util
    # 尝试从已有 report 或直接探测; 此处简化: 用 0 表示未知, 靠文件大小判断
    for i in range(1, max_attempts+1):
        print(f"\n===== 第{i}次尝试 ({vid}) @ {time.strftime('%H:%M:%S')} =====", flush=True)
        r = subprocess.run([PY, str(script), page_url, str(out), "--seg","120"],
                           capture_output=True, text=True, timeout=3600)
        print(r.stdout[-2000:], flush=True)
        if r.returncode == 0 and out.exists() and out.stat().st_size > 10_000_000:
            print(f"[成功] {vid} 下载完成: {out.stat().st_size/1e6:.1f}MB", flush=True)
            return 0
        print(f"[未完成] 尝试{i}失败, 等待{interval}s后重试...", flush=True)
        if i < max_attempts:
            time.sleep(interval)
    print(f"[放弃] {vid} 重试{max_attempts}次仍未完成", flush=True)
    return 1

if __name__ == "__main__":
    args = sys.argv[1:]
    max_att = int(args[3]) if len(args)>3 else 20
    interval = int(args[4]) if len(args)>4 else 180
    sys.exit(main(args[0], args[1], max_att, interval))
