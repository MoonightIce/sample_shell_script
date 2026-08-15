#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探测搜索页链接结构(独立文件避免引号问题)"""
import asyncio, sys
from playwright.async_api import async_playwright
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36"

JS = """els => els.map(a => ({
    href: a.href,
    txt: (a.textContent || "").trim().slice(0, 50),
    img: (a.querySelector('img')||{}).src || ''
})).filter(x => x.href.includes('missav.ws')
    && !/search|dm\\d|actress|genre|maker|vip|flag|\\/cn\\/|\\/en\\/|\\/ja\\/|\\/ko\\//.test(x.href))
    .slice(0, 15)"""

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True,
            executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            args=["--no-sandbox","--disable-blink-features=AutomationControlled"])
        c = await b.new_context(user_agent=UA, viewport={"width":1280,"height":900})
        pg = await c.new_page()
        await pg.goto("https://missav.ws/search/fset294", wait_until="domcontentloaded", timeout=45000)
        for _ in range(40):
            try:
                t = await pg.title()
                if t and "Just a moment" not in t: break
            except Exception: pass
            await pg.wait_for_timeout(1000)
        await pg.wait_for_timeout(3000)
        links = await pg.eval_on_selector_all("a", JS)
        print("链接数:", len(links))
        for l in links:
            print(f"  {l['href'][:65]} | img={'Y' if l['img'] else 'N'} | {l['txt'][:25]!r}")
        await b.close()

asyncio.run(main())
