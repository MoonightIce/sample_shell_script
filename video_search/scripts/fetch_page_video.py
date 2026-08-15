#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_page_video.py — 用 Playwright 驱动真实浏览器打开视频站点详情页，
提取页面中的视频源(video/iframe/封面图)等媒体信息。
仅做流程测试: 页面加载 → 解析媒体元素 → 输出结构化结果。

用法:
  python fetch_page_video.py <url>
"""
import sys
import json
import asyncio
from playwright.async_api import async_playwright


async def main(url: str):
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--autoplay-policy=no-user-gesture-required",
            ],
        )
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            viewport={"width": 1280, "height": 800},
        )
        page = await ctx.new_page()
        # 拦截并记录媒体请求
        media_urls = []
        page.on("request", lambda req: (
            media_urls.append(req.url)
            if any(k in req.resource_type for k in ("media", "video"))
            else None
        ))

        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            status = resp.status if resp else None
            # 等待 Cloudflare 质询通过 (最多 40s)
            for _ in range(40):
                t = await page.title()
                if t and "Just a moment" not in t and t.strip():
                    break
                await page.wait_for_timeout(1000)
            await page.wait_for_timeout(5000)  # 等首屏资源
        except Exception as e:
            print(json.dumps({"error": str(e)}, ensure_ascii=False))
            await browser.close()
            return

        # 提取 video 元素信息
        video_info = await page.eval_on_selector_all(
            "video",
            """els => els.map(v => ({
                src: v.currentSrc || v.src || "",
                poster: v.poster || "",
                duration: v.duration || null,
                w: v.videoWidth || null,
                h: v.videoHeight || null,
                autoplay: v.autoplay,
                muted: v.muted
            }))""",
        )

        # 提取 iframe
        iframes = await page.eval_on_selector_all(
            "iframe",
            """els => els.map(f => ({
                src: f.src || "",
                w: f.width || null,
                h: f.height || null
            }))""",
        )

        # 提取页面 og 信息
        og = await page.eval_on_selector_all(
            "meta[property^='og:'], meta[name^='og:']",
            """els => els.map(m => [m.getAttribute('property')||m.getAttribute('name'), m.content])""",
        )

        title = await page.title()

        # 提取页面内视频详情链接
        links = await page.eval_on_selector_all(
            "a",
            """els => els.map(a => ({
                href: a.href || "",
                title: (a.title || a.getAttribute('aria-label') || "").slice(0, 80)
            })).filter(l => l.href.includes('missav.ws/'))""",
        )

        result = {
            "url": url,
            "http_status": status,
            "final_url": page.url,
            "title": title,
            "video_elements": video_info,
            "iframes": iframes,
            "og_meta": og,
            "links": list(dict.fromkeys(l["href"] for l in links))[:40],
            "media_requests": list(dict.fromkeys(media_urls))[:30],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        await browser.close()


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "https://missav.ws/"
    asyncio.run(main(url))
