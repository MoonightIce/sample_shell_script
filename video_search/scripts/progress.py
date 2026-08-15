#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
progress.py — 下载进度管理: 展示队列中所有任务的实时进度。

数据源:
  - data/download_queue.json : 任务列表(状态 pending/downloading/done/failed)
  - data/downloads/.progress.json : 正在下载任务的实时进度(分片数/百分比/速度/ETA)
  - data/downloads/*.mp4 : 已完成的产物(时长/大小)

用法:
  python progress.py            # 查看一次
  python progress.py --watch    # 持续刷新(每2秒)
"""
import sys, json, time, glob, subprocess
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
QUEUE = BASE / "data" / "download_queue.json"
DOWNLOADS = BASE / "data" / "downloads"
PROGRESS = DOWNLOADS / ".progress.json"


def load_queue():
    if not QUEUE.exists():
        return {"tasks": []}
    return json.loads(QUEUE.read_text())


def load_progress():
    if not PROGRESS.exists():
        return {}
    try:
        return json.loads(PROGRESS.read_text())
    except Exception:
        return {}


def probe_mp4(p):
    """返回 mp4 的时长/大小。"""
    try:
        r = subprocess.run(["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                            "-of", "csv=p=0", str(p)], capture_output=True, text=True, timeout=15)
        dur = float(r.stdout.strip())
        return round(dur), round(p.stat().st_size / 1e6, 1)
    except Exception:
        return 0, 0


def fmt_eta(sec):
    if sec < 0: return "?"
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"


def bar(pct, width=22):
    filled = int(pct / 100 * width)
    return "█" * filled + "░" * (width - filled)


def render():
    q = load_queue()
    prog = load_progress()
    lines = []
    lines.append(f"{'ID':<22} {'状态':<12} {'进度':<28} {'大小':>8} {'时长':>7}")
    lines.append("-" * 80)

    done_files = {}
    for f in DOWNLOADS.glob("*.mp4"):
        done_files[f.stem] = f

    # 当前下载中(有实时进度且未 finished)
    # 注意: .progress.json 是 download_browser 每次下载时重建的,
    # 完成后残留的 status=finished 不算"下载中", 避免把已完成任务误显示为下载中
    if prog and prog.get("status") != "finished":
        p = prog
        lines.append(f"{p['id']:<22} {'⬇ 下载中':<12} {bar(p['pct'])} {p['pct']:>5.1f}%  "
                     f"{p['done']}/{p['total']}片  速度{p['speed_pps']:.1f}片/s  ETA {fmt_eta(p['eta_sec'])}")

    # 队列任务
    for t in q["tasks"]:
        vid = t["id"]
        if prog and prog.get("status") != "finished" and prog.get("id") == vid:
            continue  # 已在上面展示
        status = t["status"]
        icon = {"pending": "⏳", "downloading": "⬇", "done": "✅", "failed": "❌"}.get(status, "?")
        if status == "done" and vid in done_files:
            dur, size = probe_mp4(done_files[vid])
            lines.append(f"{vid:<22} {icon} {status:<11} {'✓ 已完成':<26} {size:>7.1f}M {dur:>6}s")
        elif status == "pending":
            lines.append(f"{vid:<22} {icon} {status:<11} {'等待中':<26}")
        else:
            lines.append(f"{vid:<22} {icon} {status:<11}")
    return "\n".join(lines)


def render_block():
    """渲染完整面板(含标题), 返回多行文本。"""
    return f"=== 下载进度管理 @ {time.strftime('%H:%M:%S')} ===\n" + render()


def main():
    # 默认: 单次输出即退出。
    # --watch [秒]: 首次输出后, 用 ANSI 控制符在同一区域每 N 秒原地刷新
    #   (终端里是"一块面板不断跳动更新", 不是逐条新消息)。
    watch_sec = 0
    if "--watch" in sys.argv:
        idx = sys.argv.index("--watch")
        watch_sec = float(sys.argv[idx + 1]) if len(sys.argv) > idx + 1 else 5
    try:
        if not watch_sec:
            print(render_block())
            return
        # --watch 模式: 原地刷新
        import os
        is_tty = sys.stdout.isatty()
        print(render_block(), flush=True)
        n_lines = render_block().count("\n") + 1
        while True:
            time.sleep(watch_sec)
            if is_tty:
                # 终端: 光标上移 n_lines 行, 覆盖重绘 (原地更新)
                print(f"\033[{n_lines}A", end="")
                block = render_block()
                print(block, flush=True)
                n_lines = block.count("\n") + 1
            else:
                # 非终端(管道/日志): 输出分隔线+新快照, 避免刷屏但仍持续更新
                print("\n" + "-" * 40)
                print(render_block(), flush=True)
    except KeyboardInterrupt:
        print("\n[停止监控]")


if __name__ == "__main__":
    main()
