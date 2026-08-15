#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证修复后的分片解析逻辑: 对比修复前(indexOf) vs 修复后(索引i)"""
import asyncio, sys
from playwright.async_api import async_playwright

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36"

OLD_JS = """async (url) => {
    const r = await fetch(url, {headers:{'Referer':location.origin+'/'}});
    const txt = await r.text();
    const base = url.slice(0, url.lastIndexOf('/')+1);
    const segs = [];
    for(const ln of txt.split('\\n')){
        const m = ln.match(/^#EXTINF:([0-9.]+),(.*)/);
        if(m){
            const uri = txt.split('\\n')[txt.split('\\n').indexOf(ln)+1];
            segs.push({url:new URL(uri, base).href, dur:parseFloat(m[1])});
        }
    }
    return segs;
}"""

NEW_JS = """async (url) => {
    const r = await fetch(url, {headers:{'Referer':location.origin+'/'}});
    const txt = await r.text();
    const base = url.slice(0, url.lastIndexOf('/')+1);
    const lines = txt.split('\\n');
    const segs = [];
    for(let i=0;i<lines.length;i++){
        const m = lines[i].match(/^#EXTINF:([0-9.]+),(.*)/);
        if(m && i+1<lines.length && !lines[i+1].startsWith('#')){
            segs.push({url:new URL(lines[i+1].trim(), base).href, dur:parseFloat(m[1])});
        }
    }
    return segs;
}"""

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True,
            executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            args=["--no-sandbox","--disable-blink-features=AutomationControlled"])
        c = await b.new_context(user_agent=UA, viewport={"width":1280,"height":800})
        pg = await c.new_page()
        m3u8s = []
        pg.on("request", lambda r: m3u8s.append(r.url) if ".m3u8" in r.url else None)
        await pg.goto("https://missav.ws/fc2-ppv-4882476", wait_until="domcontentloaded", timeout=45000)
        for _ in range(40):
            try:
                t = await pg.title()
                if t and "Just a moment" not in t: break
            except Exception: pass
            await pg.wait_for_timeout(1000)
        try:
            await pg.evaluate("""()=>{const v=document.querySelector('video');if(v){v.muted=true;v.play().catch(()=>{});}}""")
            await pg.wait_for_timeout(5000)
        except Exception: pass
        # 选一个子流
        target = None
        for u in m3u8s:
            if "1080p" in u: target = u; break
        if not target:
            target = [u for u in m3u8s if "video.m3u8" in u][-1] if any("video.m3u8" in u for u in m3u8s) else m3u8s[-1]
        print("测试子流:", target)
        try:
            old = await pg.evaluate(OLD_JS, target)
            print(f"修复前(indexOf): {len(old)} 个分片, 唯一 {len(set(x['url'] for x in old))} 个")
        except Exception as e:
            print("旧JS异常:", str(e)[:80])
        try:
            new = await pg.evaluate(NEW_JS, target)
            print(f"修复后(索引i):  {len(new)} 个分片, 唯一 {len(set(x['url'] for x in new))} 个")
        except Exception as e:
            print("新JS异常:", str(e)[:80])
        await b.close()

asyncio.run(main())
