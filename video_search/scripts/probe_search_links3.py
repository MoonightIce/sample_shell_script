#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""定位搜索结果文本所在DOM结构"""
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
        await pg.wait_for_timeout(4000)
        # 滚动到底部触发懒加载
        for _ in range(3):
            await pg.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await pg.wait_for_timeout(1000)
        # 找含 FSET 文本的元素及其父级HTML
        html = await pg.evaluate("""() => {
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
            const out = [];
            let n;
            while ((n = walker.nextNode()) && out.length < 5) {
                if (/FSET-\\d{3}/.test(n.textContent)) {
                    let el = n.parentElement;
                    out.push({tag: el.tagName, cls: (el.className||'').slice(0,60),
                              href: el.closest('a') ? el.closest('a').href : '',
                              html: el.outerHTML.slice(0,300)});
                }
            }
            return out;
        }""")
        print("含FSET文本的元素:")
        for x in html:
            print(f"  tag={x['tag']} cls={x['cls']}")
            print(f"  href={x['href']}")
            print(f"  html={x['html'][:200]}")
            print()
        await b.close()

asyncio.run(main())
