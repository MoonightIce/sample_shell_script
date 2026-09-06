#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
probe_91porn.py — 探测 91porn 视频页结构, 决定复用现有下载链路的方式。

背景: 沙箱网络对 91porn.com 有白名单拦截(HTTP 连接被指到 127.0.0.1, 同 missav.ws),
     真实页面结构必须在本机运行本脚本验证。

它回答三个问题:
  1. 91porn 视频源是什么形态 → HLS(m3u8) 还是 mp4 直链?
     - 若是 HLS: 现有 download_browser.py(HLSFetch) 可直接复用, 零改动
     - 若是 mp4 直链: 走 download_full_video.py 的泛化 mp4 直链路径(已支持)
  2. 详情页标题/编码如何(供 sidecar 元信息复用)
  3. 首页(index.php)上视频卡片链接长什么样(供搜索/入队复用)

用法:
  python probe_91porn.py                      # 探测首页, 列出视频卡片链接
  python probe_91porn.py <url>                # 探测指定页面(详情页或首页)
  python probe_91porn.py <viewkey>            # 只给 viewkey, 自动拼 view_video.php

输出 JSON(结构摘要):
  {
    "http_status": 200, "final_url": "...", "title": "...", "h1": "...",
    "video_src": "...",            # <video> 元素 src
    "video_sources": [...],        # <source> 列表
    "flashvars": [...],            # 脚本中 含 url 关键字的 key=value 配置(老式播放器)
    "media_requests": [...],       # 页面加载期间捕获的 m3u8/mp4/ts 网络请求
    "cards": [...],                # 首页视频卡片(view_video.php 链接)
    "source_type": "hls|mp4|unknown"
  }
"""
import sys, os, json, re, asyncio
from pathlib import Path

os.environ.pop("NODE_OPTIONS", None)  # 沙箱 Playwright 需要

sys.path.insert(0, str(Path(__file__).resolve().parent))

# 环境自举: 自动使用项目 venv(video_search/.venv)运行, 无需手动指定解释器/装依赖
import env_check  # noqa: E402
env_check.ensure_playwright()

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

MEDIA_RE = re.compile(r"\.(m3u8|mp4|ts)(\?|$)", re.I)

PAGE_EXTRACT_JS = r"""
() => {
  const res = {
    title: document.title || '',
    h1: (document.querySelector('h1')||{}).textContent || '',
    video_src: '', video_sources: [],
    videos: [],           // 所有 <video> 元素详情(定位主播放器 vs 广告)
    flashvars: [],        // 脚本中含 url 关键字的键值
    player_scripts: [],   // 含 video_url/flashvars/player 的脚本原文片段(诊断用)
    cards: []
  };
  // <video>/<source>
  const v = document.querySelector('video');
  if (v) {
    res.video_src = v.src || v.currentSrc || '';
    res.video_sources = [...(v.querySelectorAll('source')||[])]
      .map(s => s.getAttribute('src')).filter(Boolean);
  }
  document.querySelectorAll('video').forEach(v => {
    const src = v.src || v.currentSrc || (v.querySelector('source')||{}).src || '';
    res.videos.push({
      src: src.slice(0, 160),
      id: v.id || '', cls: (v.className||'').slice(0,60),
      w: v.videoWidth||0, h: v.videoHeight||0,
      inPlayer: !!(v.closest('#player,.player,[id*=player],[class*=player]'))
    });
  });
  // 首页视频卡片(view_video.php 链接) — 供搜索/入队复用
  document.querySelectorAll('a[href*="view_video"]').forEach(a => {
    const u = a.href;
    if (!res.cards.some(c => c.href === u)) {
      res.cards.push({href: u, text: (a.textContent||'').trim().replace(/\s+/g,' ').slice(0,60)});
    }
  });
  // 老式播放器配置(flashvars / player): 提取含 url/src/link/file 字样的键值
  document.querySelectorAll('script').forEach(s => {
    const t = s.textContent || '';
    if (!/video_url|flashvars|player|\.mp4|\.m3u8/i.test(t)) return;
    const re = /([A-Za-z_][A-Za-z0-9_]*)\s*[:=]\s*"([^"]{5,500})"/g;
    let m;
    while ((m = re.exec(t))) {
      if (/url|src|link|file|source|addr/i.test(m[1])) {
        res.flashvars.push({k: m[1], v: m[2].replace(/&amp;/g,'&')});
      }
    }
    if (res.player_scripts.length < 5) {
      res.player_scripts.push(t.replace(/\s+/g,' ').slice(0, 500));
    }
  });
  return res;
}
"""


def build_url(arg: str) -> str:
    """把输入归一为可访问 URL: 完整 URL / viewkey / 空(默认首页)。"""
    if not arg:
        return "https://91porn.com/index.php"
    if arg.startswith("http://") or arg.startswith("https://"):
        return arg
    # 裸 viewkey → 详情页
    return f"https://91porn.com/view_video.php?viewkey={arg}"


def classify(probe: dict) -> str:
    """根据捕获到的素材判断视频源形态: hls / mp4 / unknown。"""
    hay = []
    hay += probe.get("media_requests", [])
    hay += probe.get("video_sources", [])
    if probe.get("video_src"):
        hay.append(probe["video_src"])
    hay += [f["v"] for f in probe.get("flashvars", [])]
    for u in hay:
        if ".m3u8" in u:
            return "hls"
    for u in hay:
        if re.search(r"\.mp4", u, re.I):
            return "mp4"
    return "unknown"


async def probe(url: str) -> dict:
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        ctx = await browser.new_context(user_agent=UA, viewport={"width": 1280, "height": 900})
        page = await ctx.new_page()

        media = []
        page.on("request", lambda r: media.append(r.url) if MEDIA_RE.search(r.url) else None)

        status = None
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            status = resp.status if resp else None
        except Exception:
            pass
        # 等待 Cloudflare 挑战页通过
        for _ in range(40):
            try:
                t = await page.title()
                if t and "Just a moment" not in t and t.strip():
                    break
            except Exception:
                pass
            await page.wait_for_timeout(1000)
        await page.wait_for_timeout(3000)
        # 触发播放(懒加载的播放器才会上报 m3u8/mp4 请求)
        try:
            await page.evaluate("""() => {
                const v = document.querySelector('video');
                if (v) { v.muted = true; v.play().catch(()=>{}); }
                document.querySelectorAll('button,[class*=play]').forEach(b=>{
                    if (/play/i.test(b.className)) b.click();
                });
            }""")
            await page.wait_for_timeout(5000)
        except Exception:
            pass

        info = await page.evaluate(PAGE_EXTRACT_JS)
        final_url = page.url
        await browser.close()

        return {
            "http_status": status,
            "final_url": final_url,
            "title": info["title"],
            "h1": info["h1"],
            "video_src": info["video_src"],
            "video_sources": info["video_sources"],
            "videos": info["videos"],
            "flashvars": info["flashvars"],
            "player_scripts": info["player_scripts"],
            "media_requests": list(dict.fromkeys(media))[:20],
            "cards": info["cards"][:15],
        }


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    url = build_url(arg)
    print(f"[probe] 探测: {url}", flush=True)
    result = asyncio.run(probe(url))
    result["source_type"] = classify(result)
    print(json.dumps(result, ensure_ascii=False, indent=2))

    print("\n[结论]", flush=True)
    if result["source_type"] == "hls":
        print("  视频源为 HLS(m3u8) → 直接复用 download_browser.py, 零改动:")
        print(f"    python download_browser.py '{result['final_url']}' /path/out.mp4")
    elif result["source_type"] == "mp4":
        print("  视频源为 mp4 直链 → 复用 download_full_video.py(已支持直链兜底):")
        print(f"    python download_full_video.py '{result['final_url']}' /path/out.mp4")
    else:
        print("  未捕获到明确视频源(可能需登录/验证码/更高超时)。请人工打开页面查看播放器, 再决定适配方式。")
    if result["cards"]:
        print(f"  首页发现 {len(result['cards'])} 个视频卡片, 示例: {result['cards'][0]['href']}")


if __name__ == "__main__":
    main()
