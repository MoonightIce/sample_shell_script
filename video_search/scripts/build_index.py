#!/usr/bin/env python3
"""
向量/指纹入库模块：对抽帧图片提取特征（pHash 或 CLIP），建立索引。
（技术方案第4-5步，当前以 pHash 骨架运行，换环境可切 --backend clip）

用法:
    python build_index.py --frames data/frames --index-dir data/index --backend phash
"""
import argparse
import json
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DEFAULT_FRAMES = BASE / "data/frames"
DEFAULT_INDEX = BASE / "data/index"
sys.path.insert(0, str(Path(__file__).resolve().parent))
import feature_extract as fe


def build(frames_dir: Path, index_dir: Path, backend: str):
    index_dir.mkdir(parents=True, exist_ok=True)

    # 特征集合
    records = []  # 每条: {video_id, frame_path, timestamp, feat}
    dim = fe.dim(backend)

    videos = sorted([d for d in frames_dir.iterdir() if d.is_dir()])
    print(f"[scan] 发现 {len(videos)} 个视频的帧目录, 后端={backend}, 维度={dim}")
    t_total = time.time()
    total = 0
    for vid in videos:
        imgs = sorted(vid.glob("*.jpg"))
        if not imgs:
            continue
        for p in imgs:
            feat = fe.extract(p, backend)
            ts = int(p.stem.split("_")[1]) * 2 if "_" in p.stem else 0
            records.append({
                "video_id": vid.name,
                "frame_path": str(p),
                "timestamp": ts,
                "feat": feat,
            })
            total += 1
        print(f"[feat] {vid.name}: {len(imgs)} 帧 (累计 {total})")

    if not records:
        print("!! 无帧可入库")
        return

    # 保存记录
    with open(index_dir / "records.json", "w") as f:
        json.dump(records, f, ensure_ascii=False)
    with open(index_dir / "meta.json", "w") as f:
        json.dump({"backend": backend, "dim": dim, "count": total}, f, ensure_ascii=False)

    elapsed = time.time() - t_total
    print(f"\n[入库] 完成: {total} 帧, 后端={backend}, 用时 {elapsed:.1f}s, "
          f"平均 {elapsed/max(total,1):.3f}s/帧")
    print(f"[入库] 索引文件 -> {index_dir/'records.json'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=str, default=str(DEFAULT_FRAMES))
    ap.add_argument("--index-dir", type=str, default=str(DEFAULT_INDEX))
    ap.add_argument("--backend", type=str, default="phash", choices=["phash", "clip"],
                    help="特征后端: phash(当前可用) / clip(需torch,换环境后) ")
    args = ap.parse_args()
    build(Path(args.frames), Path(args.index_dir), args.backend)


if __name__ == "__main__":
    main()
