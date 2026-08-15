#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
download_queue.py — 待下载队列调度器。
从 data/download_queue.json 读取 pending 任务, 用方案D(浏览器内fetch)逐个下载。
关键: 单个浏览器会话内连续处理多个任务, 避免重复开浏览器触发沙箱并发限制。

队列文件结构 (data/download_queue.json):
{
  "tasks": [
    {"id": "sdmm-097", "url": "https://missav.ws/sdmm097", "status": "pending", "title": "...", "added_at": "..."},
    ...
  ]
}
status: pending → downloading → done / failed
下载完成后自动产出元信息 sidecar: data/downloads/<id>.json
(含 title/description/categories/series/actress/maker/subtitles/subtitle_files),
并把摘要回写队列任务的 meta 字段。

用法:
  python download_queue.py              # 处理所有 pending 任务
  python download_queue.py --once 1     # 只处理1个
"""
import sys, json, asyncio, time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
QUEUE_FILE = BASE / "data" / "download_queue.json"

def load_queue():
    if not QUEUE_FILE.exists():
        return {"tasks": []}
    return json.loads(QUEUE_FILE.read_text())

def save_queue(q):
    QUEUE_FILE.write_text(json.dumps(q, ensure_ascii=False, indent=2))

def add_task(url, title=""):
    """搜索命中后入队。返回任务id。"""
    q = load_queue()
    vid = url.rstrip("/").split("/")[-1]
    # 去重: 同id已存在则跳过
    for t in q["tasks"]:
        if t["id"] == vid:
            return t["id"], False
    task = {"id": vid, "url": url, "title": title, "status": "pending",
            "added_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    q["tasks"].append(task)
    save_queue(q)
    return vid, True

def list_tasks():
    q = load_queue()
    return q["tasks"]

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", type=int, default=0, help="只处理N个任务(0=全部)")
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--session-retries", type=int, default=20)
    ap.add_argument("--interval", type=int, default=180)
    args = ap.parse_args()

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import download_browser as db

    tasks = list_tasks()
    # 待处理 = pending, 或 downloading 但产物不存在(进程已死需续传)
    pending = []
    for t in tasks:
        if t["status"] == "pending":
            pending.append(t)
        elif t["status"] == "downloading":
            # 检查产物是否存在: 若不存在说明进程已死, 需要续传
            out = BASE / "data" / "downloads" / f"{t['id']}.mp4"
            if not out.exists() or out.stat().st_size == 0:
                pending.append(t)
                print(f"  [恢复] {t['id']} 卡在 downloading 且无产物, 转为续传", flush=True)
    if not pending:
        print("[队列] 无待下载任务")
        return
    if args.once > 0:
        pending = pending[:args.once]
    print(f"[队列] {len(pending)} 个任务待下载")

    for task in pending:
        vid = task["id"]
        print(f"\n===== 下载 {vid} : {task['url']} =====")
        # 标记 downloading
        for t in tasks:
            if t["id"] == vid:
                t["status"] = "downloading"
        save_queue({"tasks": tasks})

        out = BASE / "data" / "downloads" / f"{vid}.mp4"
        dl = db.HLSFetch(task["url"], out, seg=120, retries=args.retries)
        ok = False
        for attempt in range(1, args.session_retries+1):
            print(f"  会话尝试 {attempt}/{args.session_retries}", flush=True)
            if asyncio.run(dl.run()):
                ok = True
                break
            print(f"  未完成, 等{args.interval}s重试", flush=True)
            if attempt < args.session_retries:
                time.sleep(args.interval)
        if ok:
            print("  合并中...")
            if dl.merge():
                # 注意: 不在此处 shutil.rmtree 清理临时分片!
                # 沙箱对批量删除(>50文件)会弹确认阻塞进程, 导致状态写回和后续任务中断。
                # 临时分片保留, 由 progress.py 或外部一次性清理。
                print(f"  ✓ 完成: {out}")
                for t in tasks:
                    if t["id"] == vid:
                        t["status"] = "done"
                        # 回写元信息摘要(sidecar 由 download_browser 写入)
                        sidecar = out.with_suffix(".json")
                        if sidecar.exists():
                            try:
                                meta = json.loads(sidecar.read_text())
                                t["title"] = meta.get("title") or t.get("title", "")
                                t["meta"] = {
                                    "categories": meta.get("categories", []),
                                    "series": meta.get("series", []),
                                    "actress": meta.get("actress", []),
                                    "maker": meta.get("maker", []),
                                    "subtitles": meta.get("subtitles", []),
                                    "subtitle_files": meta.get("subtitle_files", []),
                                }
                                print(f"  [meta] 元信息回写队列: 分类{len(t['meta']['categories'])} 演员{len(t['meta']['actress'])} 字幕{len(t['meta']['subtitle_files'])}")
                            except Exception as e:
                                print(f"  [meta] 队列回写失败: {e}")
            else:
                print("  ✗ 合并失败")
                for t in tasks:
                    if t["id"] == vid:
                        t["status"] = "failed"
        else:
            print(f"  ✗ {vid} 下载失败(会话重试耗尽)")
            for t in tasks:
                if t["id"] == vid:
                    t["status"] = "failed"
        save_queue({"tasks": tasks})

    # 汇总
    done = [t for t in list_tasks() if t["status"] == "done"]
    print(f"\n[队列] 完成 {len(done)} 个任务")

if __name__ == "__main__":
    main()
