#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探测搜索页结果卡片结构 - 找含番号的元素"""
import asyncio
from playwright.async_api import async_playwright
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36"

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

        # 1. 抓所有 a href 里含 fset 的
        fset_links = await pg.eval_on_selector_all("a", "els=>els.map(a=>a.href).filter(h=>/fset/i.test(h)).slice(0,15)")
        print("含fset的链接:", len(fset_links))
        for h in fset_links[:15]: print("  ", h)

        # 2. 检查 body 里的缩略图结构: 找 video 或 source
        vids = await pg.eval_on_selector_all("video, source", "els=>els.map(e=>(e.src||e.currentSrc||'').slice(0,80)).filter(s=>s)")
        print("video/source:", len(vids))
        for v in vids[:8]: print("  ", v)

        # 3. 抓所有含 title 属性的 a
        t_links = await pg.eval_on_selector_all("a[title]", "els=>els.map(a=>({href:a.href,t:(a.title||'').slice(0,50)})).slice(0,12)")
        print("带title的a:", len(t_links))
        for x in t_links[:12]: print("  ", x['href'][:60], "|", x['t'][:30])
        await b.close()

asyncio.run(main())
