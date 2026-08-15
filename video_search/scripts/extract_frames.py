#!/usr/bin/env python3
"""
采集与抽帧模块：从本地视频抽帧（技术方案中的第1-2步）。
按固定时间间隔抽取代表性帧，保存为 JPEG 供后续 CLIP 向量化。

用法:
    python extract_frames.py --video data/videos/beach.mp4 --out data/frames --interval 2
    python extract_frames.py --all   # 批量处理 data/videos 下所有视频
"""
import argparse
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DEFAULT_VIDEOS = BASE / "data/videos"
DEFAULT_OUT = BASE / "data/frames"
DEFAULT_INTERVAL = 2  # 秒


def video_info(path: Path):
    """用 ffprobe 读取视频时长和分辨率"""
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        return None, None
    import json
    info = json.loads(r.stdout)
    duration = float(info.get("format", {}).get("duration", 0))
    width = height = None
    for s in info.get("streams", []):
        if s.get("codec_type") == "video":
            width = s.get("width")
            height = s.get("height")
            break
    return duration, (width, height)


def extract_frames(video: Path, out_dir: Path, interval: int, fps_sampling=True):
    """按 interval 秒抽取一帧。返回抽帧数。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    video_id = video.stem
    duration, res = video_info(video)
    if duration is None:
        print(f"  !! 无法读取 {video.name}")
        return 0

    frame_dir = out_dir / video_id
    frame_dir.mkdir(parents=True, exist_ok=True)

    # ffmpeg fps 过滤器: fps = 1/interval → 每 interval 秒一帧
    # 缩放至 224x224 供 CLIP 输入
    vf = f"fps=1/{interval},scale=224:224"
    cmd = [
        "ffmpeg", "-y", "-i", str(video),
        "-vf", vf, "-q:v", "2",
        str(frame_dir / "frame_%05d.jpg"),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  !! 抽帧失败 {video.name}: {r.stderr[-400:]}")
        return 0
    n = len(list(frame_dir.glob("*.jpg")))
    print(f"[ok] {video.name} 时长{duration:.0f}s -> {n} 帧 -> {frame_dir}")
    return n


def process_all(video_dir, out_dir, interval):
    total = 0
    videos = sorted(video_dir.glob("*.mp4")) + sorted(video_dir.glob("*.mov")) + sorted(video_dir.glob("*.mkv"))
    if not videos:
        print("  未找到视频文件")
        return
    for v in videos:
        total += extract_frames(v, out_dir, interval)
    print(f"\n共处理 {len(videos)} 个视频, 抽取 {total} 帧")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", type=str, help="单个视频文件路径")
    ap.add_argument("--all", action="store_true", help="批量处理 data/videos 下所有视频")
    ap.add_argument("--video-dir", type=str, default=str(DEFAULT_VIDEOS))
    ap.add_argument("--out", type=str, default=str(DEFAULT_OUT))
    ap.add_argument("--interval", type=float, default=DEFAULT_INTERVAL)
    args = ap.parse_args()

    out_dir = Path(args.out)
    if args.video:
        extract_frames(Path(args.video), out_dir, args.interval)
    elif args.all:
        process_all(Path(args.video_dir), out_dir, args.interval)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
