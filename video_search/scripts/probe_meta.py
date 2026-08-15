#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
probe_meta.py — 探测 missav 详情页元信息 DOM 结构(开发用临时脚本)。

抓取: JSON-LD / meta description / og 标签 / genre(分类) / series(系列) /
      actress(演员) / 字幕相关标记, 用于确定 download_browser.py 的选择器。

用法:
  python probe_meta.py <page_url>
"""
import sys, json, asyncio
from playwright.async_api import async_playwright

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36"

def clip(s, n=120):
    s = (s or "").strip()
    return s if len(s) <= n else s[:n] + "…"

async def main(url):
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True,
            executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
        c = await b.new_context(user_agent=UA, viewport={"width":1280,"height":900})
        pg = await c.new_page()
        media = []
        pg.on("request", lambda r: media.append(r.url) if ".m3u8" in r.url else None)
        try:
            await pg.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception:
            pass
        for _ in range(40):
            try:
                t = await pg.title()
                if t and "Just a moment" not in t and t.strip():
                    break
            except Exception:
                pass
            await pg.wait_for_timeout(1000)
        await pg.wait_for_timeout(3000)

        out = await pg.evaluate("""() => {
            const res = {};
            // 1. JSON-LD
            res.jsonld = [];
            document.querySelectorAll('script[type="application/ld+json"]').forEach(s=>{
                try { res.jsonld.push(JSON.parse(s.textContent)); }
                catch(e) { res.jsonld.push({raw: s.textContent.slice(0,500)}); }
            });
            // 2. meta
            const meta = {};
            document.querySelectorAll('meta').forEach(m=>{
                const k = m.getAttribute('name') || m.getAttribute('property');
                if (k) meta[k] = (m.getAttribute('content')||'').slice(0,300);
            });
            res.meta = meta;
            // 3. 链接分类: genre / series / actress / tag / maker
            const links = {};
            ['genre','series','actress','tag','maker','subtitle'].forEach(k=>links[k]=[]);
            document.querySelectorAll('a[href]').forEach(a=>{
                const h = a.href;
                if (/\\/genre\\//.test(h)) links.genre.push({href:h, text:(a.textContent||'').trim().slice(0,60)});
                else if (/\\/series\\//.test(h)) links.series.push({href:h, text:(a.textContent||'').trim().slice(0,60)});
                else if (/\\/actress\\//.test(h)) links.actress.push({href:h, text:(a.textContent||'').trim().slice(0,60)});
                else if (/\\/tag\\//.test(h)) links.tag.push({href:h, text:(a.textContent||'').trim().slice(0,60)});
                else if (/\\/maker\\//.test(h)) links.maker.push({href:h, text:(a.textContent||'').trim().slice(0,60)});
            });
            res.links = links;
            // 4. 字幕关键词
            res.subtitle_keywords = [];
            ['字幕','Subtitle','subtitle','中文字幕','English Sub'].forEach(k=>{
                const els = [...document.querySelectorAll('span,div,li,button,p')].filter(e=>e.textContent.trim()===k);
                res.subtitle_keywords.push({k, count: els.length,
                    sample: els.slice(0,3).map(e=>e.className||e.tagName)});
            });
            // 5. h1 标题
            res.h1 = (document.querySelector('h1')||{}).textContent || '';
            // 6. 详情块常见类名
            res.info_blocks = [];
            document.querySelectorAll('[class*=info],[class*=detail],[class*=meta],[class*=desc]').forEach(e=>{
                if(e.children.length<=3) res.info_blocks.push({cls: e.className, text: e.textContent.trim().slice(0,150)});
            });
            res.info_blocks = res.info_blocks.slice(0, 20);
            return res;
        }""")
        m3u8s = [u for u in dict.fromkeys(media) if ".m3u8" in u]
        out["title"] = await pg.title()
        out["m3u8s"] = m3u8s
        await b.close()

        print(json.dumps(out, ensure_ascii=False, indent=2)[:6000])

if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "https://missav.ws/sdmm097"))
