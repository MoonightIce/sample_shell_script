#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_video_sources.py — 对一批 missav 详情页逐个提取真实视频源 CDN URL。
用法:
  python fetch_video_sources.py url1 url2 ...
输出: JSON 数组 [{id, page_url, title, video_url}]
"""
import sys, json, asyncio
from playwright.async_api import async_playwright

async def grab_one(page, url):
    media = []
    page.on("request", lambda req: (
        media.append(req.url) if req.resource_type in ("media", "video") else None
    ))
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    except Exception:
        pass
    for _ in range(40):
        t = await page.title()
        if t and "Just a moment" not in t:
            break
        await page.wait_for_timeout(1000)
    await page.wait_for_timeout(3000)
    title = await page.title()
    # 找真正的视频 mp4 (排除 blob/封面预览 medium)
    vids = []
    for u in dict.fromkeys(media):
        if u.startswith("blob:") or "medium.mp4" in u:
            continue
        if u.endswith(".mp4") or ".mp4?" in u:
            vids.append(u)
    return {"url": url, "title": title, "video_url": vids[0] if vids else None, "all_media": media[:8]}

async def main(urls):
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
        results = []
        for u in urls:
            r = await grab_one(page, u)
            r["id"] = u.rstrip("/").split("/")[-1]
            results.append(r)
            print(json.dumps(r, ensure_ascii=False), flush=True)
        await browser.close()

asyncio.run(main(sys.argv[1:]))
