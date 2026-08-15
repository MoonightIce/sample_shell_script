#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pipeline_verify.py — 对单个目标地址完整管线验证，产物保留到本地。
步骤: 打开页面→提取视频源→抓帧→抽帧→入库→检索
产物: validation/<番号>/video.mp4(抓帧) + validation/<番号>/frames/*.jpg(抽帧)
输出: 每步技术状态(JSON)，不展示帧画面。

用法: python pipeline_verify.py <page_url> [duration_sec]
"""
import sys, os, json, subprocess, asyncio, time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
VALID = BASE / "data/validation"
PY = sys.executable  # 用当前 venv python 调用子脚本

def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    return r.returncode == 0, (r.stdout or r.stderr)

async def open_and_extract(page_url):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        ctx = await browser.new_context(
            user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
            viewport={"width": 1280, "height": 800},
        )
        page = await ctx.new_page()
        media = []
        page.on("request", lambda req: (
            media.append(req.url) if req.resource_type in ("media", "video")
            or (req.resource_type in ("fetch","xhr") and (".mp4" in req.url or ".m3u8" in req.url)) else None
        ))
        status = None
        try:
            resp = await page.goto(page_url, wait_until="domcontentloaded", timeout=45000)
            status = resp.status if resp else None
        except Exception:
            pass
        for _ in range(40):
            t = await page.title()
            if t and "Just a moment" not in t and t.strip():
                break
            await page.wait_for_timeout(1000)
        await page.wait_for_timeout(3000)
        # 主动触发视频加载: 滚动到播放器并调用 play()
        try:
            await page.evaluate("""() => {
                const v = document.querySelector('video');
                if (v) { v.muted = true; v.play().catch(()=>{}); }
                document.querySelectorAll('button, [class*=play]').forEach(b => {
                    if (/play/i.test(b.className)) b.click();
                });
            }""")
            await page.wait_for_timeout(6000)
        except Exception:
            pass
        # 找正片媒体: 优先 m3u8(完整 HLS), 其次非 init 的完整 mp4
        m3u8s = [u for u in dict.fromkeys(media) if ".m3u8" in u]
        mp4s = [u for u in dict.fromkeys(media)
                if ".mp4" in u and "init_" not in u and "medium.mp4" not in u and not u.startswith("blob:")]
        vids = m3u8s + mp4s
        title = await page.title()
        await browser.close()
        return {"http_status": status, "title_len": len(title or ""), "video_url": vids[0] if vids else None}

async def main(page_url, dur):
    vid = page_url.rstrip("/").split("/")[-1]
    out = VALID / vid
    (out / "frames").mkdir(parents=True, exist_ok=True)
    report = {"id": vid, "url": page_url, "steps": {}}

    # 步骤1-2: 打开+提源
    info = await open_and_extract(page_url)
    report["steps"]["1_open"] = {"http_status": info["http_status"]}
    report["steps"]["2_extract"] = {"video_url": info["video_url"]}
    print(json.dumps({"id": vid, "stage": "open+extract", **info}, ensure_ascii=False), flush=True)
    if not info["video_url"]:
        report["steps"]["error"] = "未提取到视频源"
        print(json.dumps(report, ensure_ascii=False)); return
    time.sleep(2)  # 放慢避免风控

    # 步骤3: 抓帧 (HLS 需 Referer)
    vfile = out / "video.mp4"
    referer = page_url[:page_url.rfind("/")]  # 页面域名作 Referer
    ok, _ = run(["ffmpeg", "-y",
                 "-headers", f"Referer: {referer}",
                 "-user_agent", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36",
                 "-i", info["video_url"], "-t", str(dur),
                 "-vf", "scale=1280:-2,fps=1", "-c:v", "libx264", "-preset", "veryfast",
                 "-crf", "28", "-movflags", "+faststart", str(vfile)])
    report["steps"]["3_grab"] = {"ok": ok, "path": str(vfile), "size_mb": round(vfile.stat().st_size/1e6,2) if vfile.exists() else 0}
    print(json.dumps({"id": vid, "stage": "grab", **report["steps"]["3_grab"]}, ensure_ascii=False), flush=True)
    if not ok:
        print(json.dumps(report, ensure_ascii=False)); return

    # 步骤4: 抽帧 (输出到 out/<vid>/frames, 让 <vid> 成为 build_index 识别的一个视频目录)
    fdir = out / vid / "frames"
    fdir.mkdir(parents=True, exist_ok=True)
    ok, _ = run(["ffmpeg", "-y", "-i", str(vfile), "-vf", "fps=1/2,scale=224:224",
                 "-q:v", "2", str(fdir / "frame_%05d.jpg")])
    n_frames = len(list(fdir.glob("*.jpg")))
    report["steps"]["4_extract"] = {"ok": ok and n_frames>0, "frames": n_frames, "path": str(fdir)}
    print(json.dumps({"id": vid, "stage": "extract", "frames": n_frames}, ensure_ascii=False), flush=True)

    # 步骤5: 入库 (frames 传 out/<vid>, 其子目录 frames 被当作一个视频)
    idx = VALID / vid / "index"
    ok, _ = run([PY, str(BASE/"scripts/build_index.py"),
                 "--frames", str(out / vid), "--index-dir", str(idx), "--backend", "phash"])
    meta = {}
    if (idx/"meta.json").exists():
        meta = json.loads((idx/"meta.json").read_text())
    report["steps"]["5_index"] = {"ok": ok, "count": meta.get("count", 0), "path": str(idx)}
    print(json.dumps({"id": vid, "stage": "index", **report["steps"]["5_index"]}, ensure_ascii=False), flush=True)

    # 步骤6: 检索(用第1帧以图搜图, 验证命中同源)
    if n_frames > 0 and (idx/"records.json").exists():
        first = str(fdir / "frame_00001.jpg")
        r = subprocess.run([PY, str(BASE/"scripts/search.py"),
                            "--backend", "phash", "--image", first,
                            "--index-dir", str(idx)], capture_output=True, text=True, timeout=60)
        report["steps"]["6_search"] = {"ok": r.returncode==0, "detail": r.stdout.strip()}
        print(json.dumps({"id": vid, "stage": "search", "ok": r.returncode==0}, ensure_ascii=False), flush=True)

    # 保存报告
    (VALID / vid / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({"id": vid, "done": True, "summary": {k:v for k,v in report["steps"].items() if isinstance(v,dict)}}, ensure_ascii=False), flush=True)

if __name__ == "__main__":
    url = sys.argv[1]
    dur = float(sys.argv[2]) if len(sys.argv) > 2 else 10
    asyncio.run(main(url, dur))
