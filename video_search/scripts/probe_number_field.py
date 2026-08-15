#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探测 missav 页面中番号字段的形态，确认精准提取位置。"""
import sys, json, asyncio
from playwright.async_api import async_playwright
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36"

async def main(page_url):
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True,
            executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            args=["--no-sandbox","--disable-blink-features=AutomationControlled"])
        c = await b.new_context(user_agent=UA, viewport={"width":1280,"height":900})
        pg = await c.new_page()
        await pg.goto(page_url, wait_until="domcontentloaded", timeout=45000)
        for _ in range(40):
            t = await pg.title()
            if t and "Just a moment" not in t: break
            await pg.wait_for_timeout(1000)
        await pg.wait_for_timeout(3000)
        # 抓取可能含番号的文本: 标题、h1、按钮、链接文本
        info = await pg.evaluate("""() => {
            const pick = (sel) => [...document.querySelectorAll(sel)].map(e=>e.textContent.trim()).slice(0,15);
            return {
                title: document.title,
                h1: pick('h1'),
                h2: pick('h2'),
                a_texts: [...document.querySelectorAll('a')].map(a=>a.textContent.trim()).filter(t=>t && t.length<40).slice(0,30),
                spans: pick('.text-sm, .text-xs, [class*=text]'),
            };
        }""")
        print(json.dumps(info, ensure_ascii=False, indent=2))
        await b.close()

asyncio.run(main(sys.argv[1])) if __name__ == "__main__" else None
