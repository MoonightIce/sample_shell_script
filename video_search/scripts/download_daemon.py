#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
download_daemon.py — 守护式下载恢复器。

解决的问题: 搜索/其他操作中止调度器后, 下载不会自动恢复。
本守护进程持续监控队列:
  - 发现"需要下载但无进程在跑"的任务 → 自动拉起 download_queue.py
  - 调度器退出(完成/被杀)后若队列仍有未完成任务 → 等待后再次拉起
  - 队列全部 done → 休眠等待新任务

用法:
  python download_daemon.py                  # 默认每 30s 检查一次
  python download_daemon.py --interval 10    # 每 10s 检查
  python download_daemon.py --once           # 只处理一轮后退出(用于测试)
"""
import sys, json, subprocess, time, os, signal
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
QUEUE = BASE / "data" / "download_queue.json"
SCRIPTS = Path(__file__).resolve().parent
PY = sys.executable


def load_queue():
    if not QUEUE.exists():
        return {"tasks": []}
    try:
        return json.loads(QUEUE.read_text())
    except Exception:
        return {"tasks": []}


def has_unfinished():
    """队列中是否有 pending 或 downloading 状态的任务。"""
    for t in load_queue()["tasks"]:
        if t["status"] in ("pending", "downloading"):
            return True
    return False


def downloader_running():
    """检查是否有 download_queue.py 进程在跑。"""
    try:
        r = subprocess.run(["pgrep", "-f", "download_queue.py"],
                           capture_output=True, text=True)
        return bool(r.stdout.strip())
    except Exception:
        return False


def launch_downloader():
    """拉起 download_queue.py (后台, 不阻塞)。"""
    print(f"[daemon] 拉起调度器: {PY} download_queue.py", flush=True)
    log = open(BASE / "data" / "downloads" / ".daemon.log", "a")
    proc = subprocess.Popen(
        [PY, str(SCRIPTS / "download_queue.py")],
        stdout=log, stderr=subprocess.STDOUT,
        start_new_session=True,  # 独立进程组, 不随守护退出
    )
    print(f"[daemon] 调度器 PID={proc.pid}", flush=True)
    return proc


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=30, help="检查间隔秒")
    ap.add_argument("--once", action="store_true", help="处理一轮后退出")
    args = ap.parse_args()

    print(f"[daemon] 下载恢复守护启动, 每 {args.interval}s 检查队列", flush=True)
    rounds = 0
    while True:
        rounds += 1
        unfinished = has_unfinished()
        running = downloader_running()
        if unfinished and not running:
            print(f"[daemon] 发现未完成任务且调度器未运行 → 拉起", flush=True)
            launch_downloader()
        elif unfinished and running:
            print(f"[daemon] 调度器运行中, 等待完成...", flush=True)
        else:
            print(f"[daemon] 队列全部完成或空闲", flush=True)

        if args.once and rounds >= 1:
            print("[daemon] --once 模式退出", flush=True)
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[daemon] 守护已停止", flush=True)
