#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backfill_meta.py — 为已完成下载但缺元信息 sidecar 的任务补抓元信息。

场景: 旧版 download_queue.py 完成的下载(如 ssis073)没有产出 <id>.json。
本脚本扫描 /Users/moonightice/Movies/系列/*.mp4, 找出缺同名 sidecar 的,
打开详情页抓取 标题/描述/分类/系列/演员/字幕 等元信息,
写 sidecar 到 data/downloads/<id>.json 并回写队列 meta 摘要。

用法:
  python backfill_meta.py                  # 补齐所有缺 sidecar 的任务
  python backfill_meta.py --id ssis073     # 只补指定 id
  python backfill_meta.py --dry-run        # 只列出缺 sidecar 的任务, 不抓取

注意: 需要能访问视频站点的网络环境; 脚本内已自动清理 NODE_OPTIONS 干扰。
"""
import os, sys, json, time, asyncio, argparse
from pathlib import Path

# Playwright driver 在 NODE_OPTIONS 含 --use-system-ca 时会启动失败
os.environ.pop("NODE_OPTIONS", None)

sys.path.insert(0, str(Path(__file__).resolve().parent))

# 环境自举: 自动使用项目 venv(video_search/.venv)运行, 无需手动指定解释器/装依赖
import env_check  # noqa: E402
env_check.ensure_playwright()

import meta_extract as me

BASE = Path(__file__).resolve().parent.parent
VIDEO_DIR = Path("/Users/moonightice/Movies/系列")  # 最终视频输出目录
WORK_DIR = BASE / "data" / "downloads"              # sidecar 输出目录
QUEUE_FILE = BASE / "data" / "download_queue.json"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36")


def load_queue():
    if not QUEUE_FILE.exists():
        return {"tasks": []}
    try:
        return json.loads(QUEUE_FILE.read_text())
    except Exception:
        return {"tasks": []}


def save_queue(q):
    QUEUE_FILE.write_text(json.dumps(q, ensure_ascii=False, indent=2))


def find_missing():
    """返回缺 sidecar 的 mp4 列表: [{id, mp4, url, title}]。"""
    q = load_queue()
    url_by_id = {t["id"]: t.get("url", "") for t in q["tasks"]}
    title_by_id = {t["id"]: t.get("title", "") for t in q["tasks"]}
    missing = []
    for mp4 in sorted(VIDEO_DIR.glob("*.mp4")):
        vid = mp4.stem
        sidecar = WORK_DIR / f"{vid}.json"
        if sidecar.exists():
            continue
        missing.append({
            "id": vid, "mp4": mp4, "url": url_by_id.get(vid, ""),
            "title": title_by_id.get(vid, ""),
        })
    return missing


async def fetch_one(url: str, title_fallback: str = "") -> dict:
    """打开详情页抓元信息。返回归一化 meta dict, 失败抛异常。"""
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.launch(
            headless=True,
            executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
        c = await b.new_context(user_agent=UA, viewport={"width":1280,"height":900})
        pg = await c.new_page()
        try:
            await pg.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception:
            pass
        for _ in range(40):
            try:
                t = await pg.title()
                if t and "Just a moment" not in t and t.strip():
                    break
            except Exception:
                pass
            await pg.wait_for_timeout(1000)
        await pg.wait_for_timeout(3000)
        raw = await me.fetch_meta_in_page(pg)
        await b.close()
        return me.normalize_meta(raw, url, title_fallback=raw.get("title", ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", type=str, default=None, help="只补指定 id")
    ap.add_argument("--dry-run", action="store_true", help="只列出缺 sidecar 的任务")
    args = ap.parse_args()

    missing = find_missing()
    if args.id:
        missing = [m for m in missing if m["id"] == args.id]
    if not missing:
        print("[backfill] 所有下载均已具备 sidecar, 无需补齐")
        return
    print(f"[backfill] {len(missing)} 个任务缺元信息 sidecar:")
    for m in missing:
        print(f"  - {m['id']}  {m['mp4'].name} ({m['mp4'].stat().st_size/1e6:.0f}MB)  url={m['url'] or '(队列无记录)'}")
    if args.dry_run:
        return

    q = load_queue()
    for m in missing:
        vid = m["id"]
        url = m["url"] or f"https://missav.ws/{vid}"
        print(f"\n===== 补抓 {vid}: {url} =====")
        try:
            meta = asyncio.run(fetch_one(url, m["title"]))
        except Exception as e:
            print(f"  ✗ 抓取失败: {e}")
            continue
        meta["id"] = vid
        meta["video_file"] = str(m["mp4"])
        meta["video_size"] = m["mp4"].stat().st_size
        sidecar = WORK_DIR / f"{vid}.json"
        sidecar.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  ✓ sidecar: {sidecar.name}")
        print(f"    标题: {meta.get('title','')[:50]}")
        print(f"    分类{len(meta.get('categories',[]))} 系列{len(meta.get('series',[]))} "
              f"演员{len(meta.get('actress',[]))} 字幕{len(meta.get('subtitles',[]))+len(meta.get('subtitle_files',[]))}")
        # 回写队列 meta 摘要
        for t in q["tasks"]:
            if t["id"] == vid:
                t["title"] = meta.get("title") or t.get("title", "")
                t["meta"] = {
                    "categories": meta.get("categories", []),
                    "series": meta.get("series", []),
                    "actress": meta.get("actress", []),
                    "maker": meta.get("maker", []),
                    "subtitles": meta.get("subtitles", []),
                    "subtitle_files": meta.get("subtitle_files", []),
                }
                break
        time.sleep(1)  # 礼貌间隔, 避免触发风控
    save_queue(q)
    print(f"\n[backfill] 完成, 队列已回写 {len(missing)} 个任务的 meta 摘要")


if __name__ == "__main__":
    main()
