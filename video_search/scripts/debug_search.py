#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""调试: 为什么 eval_on_selector_all 拿不到 FSET 卡片而 TreeWalker 可以"""
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
        await pg.wait_for_timeout(8000)
        for _ in range(5):
            await pg.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await pg.wait_for_timeout(1500)

        # 方法1: eval_on_selector_all 抓 textContent 含 FSET 的 a
        try:
            out1 = await pg.eval_on_selector_all("a",
                "els=>els.map(a=>({href:a.href,t:(a.textContent||'').slice(0,30)})).filter(x=>/FSET/.test(x.t)).slice(0,5)")
            print("eval_on_selector_all 含FSET:", len(out1))
            for x in out1: print("   ", x["href"][:55], "|", repr(x["t"]))
        except Exception as e:
            print("方法1异常:", str(e)[:100])

        # 方法2: 直接 evaluate + querySelectorAll
        out2 = await pg.evaluate("""() => {
            const res = [];
            document.querySelectorAll('a').forEach(a => {
                if (/FSET/.test(a.textContent)) res.push({href: a.href, t: a.textContent.slice(0,30)});
            });
            return res.slice(0,5);
        }""")
        print("querySelectorAll 含FSET:", len(out2))
        for x in out2: print("   ", x["href"][:55], "|", repr(x["t"]))

        # 方法3: 检查 Alpine x-text 是否已渲染 (看 x-text 属性)
        out3 = await pg.evaluate("""() => {
            const res = [];
            document.querySelectorAll('[x-text]').forEach(a => {
                res.push({t: (a.textContent||'').slice(0,30), attr: a.getAttribute('x-text')});
            });
            return {count: res.length, sample: res.slice(0,5)};
        }""")
        print("x-text 元素数:", out3["count"])
        for x in out3["sample"]: print("   ", repr(x["t"]), "| attr:", x["attr"])
        await b.close()

asyncio.run(main())
