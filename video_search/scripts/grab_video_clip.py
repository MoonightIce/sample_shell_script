#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
grab_video_clip.py — 从真实视频 URL 抓取短片段，供抽帧流程测试。
用 ffmpeg 直接流式读取 URL 前 N 秒，转成 1280x720 短 mp4，避免下载全片。

用法:
  python grab_video_clip.py <video_url> <out.mp4> [duration_sec]
"""
import subprocess
import sys
from pathlib import Path

def grab(url: str, out: Path, dur: float):
    out.parent.mkdir(parents=True, exist_ok=True)
    # -t 限定时长, 缩放到 1280 宽保持比例, yuv420p 保证兼容
    cmd = [
        "ffmpeg", "-y",
        "-i", url,
        "-t", str(dur),
        "-vf", "scale=1280:-2,fps=1",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
        "-c:a", "aac", "-b:a", "96k",
        "-movflags", "+faststart",
        str(out),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        print("FAIL:", r.stderr[-800:])
        return False
    print(f"[ok] 片段已保存 -> {out}")
    return True

if __name__ == "__main__":
    url = sys.argv[1]
    out = Path(sys.argv[2])
    dur = float(sys.argv[3]) if len(sys.argv) > 3 else 20
    grab(url, out, dur)
