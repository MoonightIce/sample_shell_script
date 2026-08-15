#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
search_missav.py — 用真实 Chrome 在 missav 站内按关键词搜索，提取候选视频详情页 URL。

用法:
  python search_missav.py <keyword>                 # 只搜索, 输出候选 JSON
  python search_missav.py <keyword> --enqueue       # 命中后自动加入下载队列
  python search_missav.py <keyword> --enqueue --limit 5   # 只入队前5个

详情页判定: 真实详情页 URL 有两种形态 missav.ws/<id> 与 missav.ws/dm<NN>/<id>,
排除已知非详情路径(actress/genre/maker/vip/search/tag)。
"""
import sys, json, asyncio, argparse, os
from pathlib import Path

os.environ.pop("NODE_OPTIONS", None)

async def main(keyword: str, enqueue: bool = False, limit: int = 0):
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
        # 用站内搜索 URL (missav 搜索路由)
        url = f"https://missav.ws/search/{keyword}"
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            print(json.dumps({"error": str(e)}, ensure_ascii=False)); await browser.close(); return
        for _ in range(40):
            t = await page.title()
            if t and "Just a moment" not in t:
                break
            await page.wait_for_timeout(1000)
        await page.wait_for_timeout(4000)
        # 抓取带缩略图卡片链接 = 视频详情页。
        # 用路径段判断详情页: 首段为 dm<NN> 且存在第二段 → 详情页; 否则首段不能是
        # actress/genre/maker/vip/search/tag 等非详情路径。
        cards = await page.eval_on_selector_all(
            "a",
            """els => els.map(a => {
                const href = a.href;
                let seg = [];
                try { seg = new URL(href).pathname.replace(/^\\//, '').split('/'); } catch(e) {}
                const f = seg[0] || '';
                const isDetail = /^dm\\d+$/i.test(f)
                    ? seg.length >= 2 && !/^(actress|genre|maker|vip|search|tag)$/i.test(seg[1])
                    : !/^(actress|genre|maker|vip|search|tag)$/i.test(f) && /[a-z0-9]/.test(f);
                return {
                    href,
                    img: (a.querySelector('img')||{}).src || '',
                    text: (a.getAttribute('title') || a.textContent || '').trim().slice(0,60),
                    isDetail
                };
            }).filter(x => x.isDetail && x.img && x.href.includes('missav.ws'))""",
        )
        seen = {}
        for c in cards:
            if c["href"] not in seen:
                seen[c["href"]] = c
        result = {
            "keyword": keyword,
            "search_url": url,
            "final_url": page.url,
            "title": await page.title(),
            "count": len(seen),
            "results": list(seen.values())[:25],
        }
        if enqueue and result["results"]:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import download_queue as dq
            picked = result["results"][:limit] if limit > 0 else result["results"]
            added = 0
            for c in picked:
                vid, is_new = dq.add_task(c["href"], c["text"])
                if is_new:
                    added += 1
                    print(f"  [入队] {vid}  {c['text'][:40]}", flush=True)
            print(f"[入队] 新增 {added} 个任务 (共命中 {result['count']}), 运行 download_queue.py 开始下载", flush=True)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        await browser.close()

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("keyword")
    ap.add_argument("--enqueue", action="store_true", help="命中后自动加入待下载队列")
    ap.add_argument("--limit", type=int, default=0, help="入队数量上限(0=全部)")
    args = ap.parse_args()
    asyncio.run(main(args.keyword, args.enqueue, args.limit))
