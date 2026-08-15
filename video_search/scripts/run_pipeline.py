#!/usr/bin/env python3
"""
一键端到端跑通全流程(骨架): 生成素材 → 抽帧 → pHash特征入库 → 检索演示。

当前以 pHash 后端(纯Python+PIL)跑通架构链路。换到能下载 torch 的环境后，
改 --backend clip 即可升级为 CLIP 语义检索(见 feature_extract.py 注释)。

用法:
    python run_pipeline.py                 # 完整跑通 + 演示
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SCRIPTS = BASE / "scripts"
DATA = BASE / "data"
VIDEOS = DATA / "videos"
FRAMES = DATA / "frames"
INDEX = DATA / "index"


def run(cmd):
    print(f"\n$ {' '.join(cmd)}")
    r = subprocess.run(cmd)
    if r.returncode != 0:
        print(f"!! 步骤失败: {' '.join(cmd)}")
        sys.exit(1)
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", type=str, default="phash", choices=["phash", "clip"])
    args = ap.parse_args()
    py = sys.executable

    print("========== 视频画面检索全流程(骨架) ==========")
    print(f"后端: {args.backend}  (phash=纯Python可用 / clip=需torch换环境)")

    # 1. 生成测试素材
    if not any(VIDEOS.glob("*.mp4")):
        print("\n[1/4] 生成测试视频素材 ...")
        run([py, str(SCRIPTS / "gen_test_videos.py")])
    else:
        print(f"\n[1/4] 已有测试素材: {len(list(VIDEOS.glob('*.mp4')))} 个视频")

    # 2. 抽帧
    print("\n[2/4] 视频抽帧 ...")
    run([py, str(SCRIPTS / "extract_frames.py"), "--all",
         "--video-dir", str(VIDEOS), "--out", str(FRAMES), "--interval", "2"])

    # 3. 特征入库
    print("\n[3/4] 特征提取 + 入库 ...")
    t0 = time.time()
    run([py, str(SCRIPTS / "build_index.py"),
         "--frames", str(FRAMES), "--index-dir", str(INDEX), "--backend", args.backend])
    print(f"[time] 建库总耗时 {time.time()-t0:.1f}s")

    # 4. 检索演示
    print("\n[4/4] 检索演示 ...")
    if args.backend == "phash":
        # 颜色检索演示
        for color in ["蓝色", "棕色", "灰色"]:
            run([py, str(SCRIPTS / "search.py"), "--backend", "phash",
                 "--color", color, "--index-dir", str(INDEX)])
    else:
        for q in ["海边", "猫", "会议室"]:
            run([py, str(SCRIPTS / "search.py"), "--backend", "clip",
                 "--query", q, "--index-dir", str(INDEX)])

    print("\n========== 全流程跑通完成 ==========")
    print("颜色检索:  python scripts/search.py --backend phash --color 蓝色")
    print("以图搜图:  python scripts/search.py --backend phash --image 某帧.png")
    print("CLIP语义:  换环境后  python scripts/search.py --backend clip --query 海边")


if __name__ == "__main__":
    main()
