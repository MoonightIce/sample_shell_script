#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
download_full_video.py — 在浏览器会话内完整下载目标地址的视频到本地。

背景: 目标地址(HLS m3u8 流)的分片 URL 带时效 token, 脱离浏览器会话会返回 403。
因此必须: 打开页面 → 触发播放 → 提取 master m3u8 → 解析最高画质子流 →
导出会话 cookie → 在同一会话内用 ffmpeg 下载。cookie+Referer 缺一不可。

用法:
  python download_full_video.py <page_url> [out_path] [--max-dur SEC]
  python download_full_video.py https://missav.ws/skmj-774            # 完整下载
  python download_full_video.py https://missav.ws/skmj-774 /tmp/a.mp4 --max-dur 30  # 只下30s
"""
import sys, os, json, subprocess, asyncio, time, re
from pathlib import Path

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36"

async def extract_highest(page_url):
    """打开页面, 提取 master m3u8, 解析出最高画质子流完整URL + 会话cookie + referer."""
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        ctx = await browser.new_context(user_agent=UA, viewport={"width":1280,"height":800})
        page = await ctx.new_page()
        media = []
        page.on("request", lambda r: (
            media.append(r.url) if ".m3u8" in r.url else None
        ))
        status = None
        try:
            resp = await page.goto(page_url, wait_until="domcontentloaded", timeout=45000)
            status = resp.status if resp else None
        except Exception:
            pass
        for _ in range(40):
            t = await page.title()
            if t and "Just a moment" not in t and t.strip():
                break
            await page.wait_for_timeout(1000)
        await page.wait_for_timeout(3000)
        try:
            await page.evaluate("""() => {
                const v=document.querySelector('video');
                if(v){v.muted=true;v.play().catch(()=>{});}
                document.querySelectorAll('button,[class*=play]').forEach(b=>{if(/play/i.test(b.className))b.click();});
            }""")
            await page.wait_for_timeout(6000)
        except Exception:
            pass

        # 主播放列表(master)通常是第一个 m3u8, 内含多码率子流
        m3u8s = [u for u in dict.fromkeys(media) if ".m3u8" in u]
        master = m3u8s[0] if m3u8s else None
        if not master:
            return {"http_status": status, "error": "未捕获 m3u8"}

        # 在页面上下文 fetch master, 解析最高画质子流
        info = await page.evaluate("""async (master) => {
            const r = await fetch(master, {headers:{'Referer':location.origin+'/'}});
            if(!r.ok) return {err:'fetch fail '+r.status};
            const txt = await r.text();
            // 解析 BANDWIDTH 最高的子流
            let best=null, bestBW=-1;
            const lines = txt.split('\\n');
            for(let i=0;i<lines.length;i++){
                if(lines[i].startsWith('#EXT-X-STREAM-INF')){
                    const bw = parseInt((lines[i].match(/BANDWIDTH=([0-9]+)/)||[])[1]||'0');
                    const res = (lines[i].match(/RESOLUTION=([0-9x]+)/)||[])[1]||'';
                    const next = lines[i+1];
                    if(next && !next.startsWith('#') && bw>=bestBW){bestBW=bw; best={url:new URL(next, master).href, bw, res};}
                }
            }
            if(!best) best={url:master, bw:bestBW, res:''};
            return {ok:true, best, master};
        }""", master)

        cookies = await ctx.cookies()
        jar = "; ".join(f"{c['name']}={c['value']}" for c in cookies
                        if c.get('domain','').endswith(('missav.ws','surrit.com','growcdnssedge.com')))
        await browser.close()
        return {"http_status": status, "master": master, "info": info, "cookie": jar,
                "referer": f"https://{page_url.rstrip('/').split('://')[-1].split('/')[0]}/"}

def probe(url, referer, cookie):
    import json as J
    cmd = ["ffprobe","-v","quiet","-print_format","json",
           "-headers", f"Referer: {referer}" + (f"\r\nCookie: {cookie}" if cookie else ""),
           "-user_agent", UA, "-show_format", "-show_streams", url]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    if r.returncode != 0: return {}
    try: info = J.loads(r.stdout)
    except Exception: return {}
    dur = float(info.get("format",{}).get("duration",0))
    w=h=None
    for s in info.get("streams",[]):
        if s.get("codec_type")=="video": w,h=s.get("width"),s.get("height"); break
    return {"duration_sec": round(dur,1), "resolution": f"{w}x{h}" if w else None}

def download(url, referer, cookie, out, max_dur=None):
    cmd = ["ffmpeg","-y","-headers", f"Referer: {referer}" + (f"\r\nCookie: {cookie}" if cookie else ""),
           "-user_agent", UA, "-i", url]
    if max_dur: cmd += ["-t", str(max_dur)]
    cmd += ["-c","copy","-movflags","+faststart", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    return r.returncode==0 and out.exists() and out.stat().st_size>0

def main(page_url, out_path=None, max_dur=None):
    vid = page_url.rstrip("/").split("/")[-1]
    out = Path(out_path) if out_path else Path.cwd()/f"{vid}_full.mp4"
    print(f"[1] 打开目标并在会话内提取源: {page_url}")
    src = asyncio.run(extract_highest(page_url))
    if src.get("error"):
        print(f"  !! {src['error']}"); return
    info = src.get("info",{})
    if not info.get("ok"):
        print(f"  !! 解析主播放列表失败: {info}"); return
    best = info["best"]
    print(f"    主播放列表: {src['master']}")
    print(f"    选择画质: {best.get('res','?')} (bandwidth={best['bw']})")
    print(f"    子流: {best['url']}")

    print(f"[2] 探测媒体信息 ...")
    meta = probe(best["url"], src["referer"], src["cookie"])
    print(f"    时长={meta.get('duration_sec')}s 分辨率={meta.get('resolution')}")

    print(f"[3] 下载 -> {out}" + (f" (限时{max_dur}s)" if max_dur else " (完整)"))
    t0 = time.time()
    ok = download(best["url"], src["referer"], src["cookie"], out, max_dur)
    if ok:
        size_mb = round(out.stat().st_size/1e6,2)
        print(f"[ok] 完成: {size_mb}MB, 用时{time.time()-t0:.0f}s")
        print(json.dumps({"id":vid,"http_status":src["http_status"],"source":best["url"],
            "resolution":best.get('res'),"meta":meta,"out_path":str(out),"size_mb":size_mb},
            ensure_ascii=False, indent=2))
    else:
        print("[!!] 下载失败")

if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    max_dur = None
    if "--max-dur" in sys.argv:
        max_dur = float(sys.argv[sys.argv.index("--max-dur")+1])
    if len(args) < 1:
        print(__doc__); sys.exit(1)
    main(args[0], args[1] if len(args)>1 else None, max_dur)
