#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探测 missav 首页视频卡片链接，提取详情页 URL 格式"""
import asyncio, json
from playwright.async_api import async_playwright

async def main():
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
        await page.goto("https://missav.ws/", wait_until="domcontentloaded", timeout=45000)
        for _ in range(40):
            t = await page.title()
            if t and "Just a moment" not in t:
                break
            await page.wait_for_timeout(1000)
        await page.wait_for_timeout(4000)
        print("title:", await page.title())

        # 抓取所有 <a> 的 href + 内部是否有 <img> (缩略图卡片)
        cards = await page.eval_on_selector_all(
            "a",
            """els => els.map(a => ({
                href: a.href,
                has_img: !!a.querySelector('img'),
                img_src: (a.querySelector('img')||{}).src || ''
            })).filter(x => x.has_img && x.href.includes('missav.ws'))""",
        )
        print("card links:", len(cards))
        seen = set()
        for c in cards:
            if c["href"] in seen:
                continue
            seen.add(c["href"])
            print(c["href"])
        await browser.close()

asyncio.run(main())
