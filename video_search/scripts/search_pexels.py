#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
search_pexels.py — 用真实 Chrome 打开 Pexels 搜索视频，提取候选视频详情页 URL。
用法: python search_pexels.py <keyword>
"""
import sys, json, asyncio
from playwright.async_api import async_playwright

async def main(keyword: str):
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        ctx = await browser.new_context(
            user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
            viewport={"width": 1280, "height": 900},
        )
        page = await ctx.new_page()
        url = f"https://www.pexels.com/search/videos/{keyword}/"
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            print(json.dumps({"error": str(e)}, ensure_ascii=False)); await browser.close(); return
        await page.wait_for_timeout(5000)
        # Pexels 视频卡片是 <a href="/video/..."> 且含 <video> 或封面
        cards = await page.eval_on_selector_all(
            "a[href*='/video/']",
            """els => els.map(a => ({
                href: a.href,
                img: (a.querySelector('img')||{}).src || ''
            }))""",
        )
        seen = {}
        for c in cards:
            if c["href"] not in seen and c["img"]:
                seen[c["href"]] = c
        result = {
            "keyword": keyword,
            "search_url": url,
            "final_url": page.url,
            "title": await page.title(),
            "count": len(seen),
            "results": list(seen.values())[:20],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        await browser.close()

asyncio.run(main(sys.argv[1]))
