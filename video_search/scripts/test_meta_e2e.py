#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_meta_e2e.py — 本地端到端测试: m3u8 字幕轨解析 + 字幕下载 + 元信息 sidecar。
起本地 HTTP 服务模拟站点, 用 Playwright 页面跑 HLSFetch 核心新逻辑, 不依赖外网。

用法:
  python test_meta_e2e.py
"""
import json, sys, threading, time
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import download_browser as db
import meta_extract as me

ROOT = Path("/tmp/meta_e2e_site")
ROOT.mkdir(parents=True, exist_ok=True)

# ---- 构造模拟站点文件 ----
MASTER = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="Chinese",DEFAULT=YES,URI="subs/zh.vtt"
#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="English",URI="subs/en.vtt"
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=1280x720
video/720.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=1500000,RESOLUTION=1920x1080
video/1080.m3u8
"""
VIDEO_720 = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:2
#EXTINF:1.0,
seg0.ts
#EXTINF:1.0,
seg1.ts
#EXT-X-ENDLIST
"""
VIDEO_1080 = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:2
#EXTINF:1.0,
seg0.ts
#EXT-X-ENDLIST
"""
VTT = """WEBVTT

00:00.000 --> 00:02.000
hello world
"""

(ROOT / "subs").mkdir(exist_ok=True)
(ROOT / "video").mkdir(exist_ok=True)
(ROOT / "master.m3u8").write_text(MASTER)
(ROOT / "video/720.m3u8").write_text(VIDEO_720)
(ROOT / "video/1080.m3u8").write_text(VIDEO_1080)
(ROOT / "subs/zh.vtt").write_text(VTT)
(ROOT / "subs/en.vtt").write_text(VTT)
(ROOT / "video/seg0.ts").write_bytes(b"\x00" * 1024)
(ROOT / "video/seg1.ts").write_bytes(b"\x00" * 1024)
(ROOT / "index.html").write_text("""<html><head>
<title>TEST-001 本地测试标题 - missav</title>
<meta property="og:title" content="TEST-001 本地测试标题">
<meta property="og:description" content="测试描述">
<script type="application/ld+json">{"@type":"VideoObject","name":"TEST-001 本地测试标题","genre":["测试类"],"actor":[{"name":"测试演员"}]}</script>
</head><body><h1>TEST-001 本地测试标题</h1>
<a href="/genre/测试类">测试类</a><a href="/actress/测试演员">测试演员</a><a href="/series/测试系列">测试系列</a>
<span>中文字幕</span></body></html>""")


class H(SimpleHTTPRequestHandler):
    def guess_type(self, path):
        # 文本类型强制带 utf-8, 避免本地测试中文乱码
        ct = super().guess_type(path)
        return ct + "; charset=utf-8" if ct and ct.startswith("text/") else ct

    def log_message(self, *a):
        pass


def main():
    handler = partial(H, directory=str(ROOT))
    srv = HTTPServer(("127.0.0.1", 18765), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print("[test] 本地站点: http://127.0.0.1:18765/")
    time.sleep(0.5)

    import asyncio
    from playwright.async_api import async_playwright

    UA = db.UA
    out = Path("/tmp/meta_e2e_out.mp4")

    async def run():
        async with async_playwright() as p:
            b = await p.chromium.launch(headless=True,
                executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled",
                      "--no-proxy-server"])
            c = await b.new_context(user_agent=UA, viewport={"width":1280,"height":900})
            pg = await c.new_page()
            await pg.goto("http://127.0.0.1:18765/", wait_until="domcontentloaded")

            # 1. 页面元信息抓取
            dl = db.HLSFetch("http://127.0.0.1:18765/index.html", str(out))
            await dl._grab_meta(pg)
            m = dl.meta
            print("\n[test 1] 页面元信息:")
            print(json.dumps(m, ensure_ascii=False, indent=2))
            assert m["title"] == "TEST-001 本地测试标题", m["title"]
            assert "测试类" in m["categories"], m["categories"]
            assert "测试演员" in m["actress"], m["actress"]
            assert "测试系列" in m["series"], m["series"]
            assert "中文字幕" in m["subtitles"], m["subtitles"]
            assert m["description"] == "测试描述", m["description"]
            print("[test 1] PASS")

            # 2. master 解析(含字幕轨) —— 用页面内 fetch 跑与 run() 相同 JS
            res = await pg.evaluate("""async (master) => {
                const r = await fetch(master, {headers:{'Referer':location.origin+'/'}});
                if(!r.ok) return {err:'fetch '+r.status};
                const txt = await r.text();
                let best=null,bestBW=-1;
                const subTracks=[];
                const L=txt.split('\\n');
                for(let i=0;i<L.length;i++){
                    if(L[i].startsWith('#EXT-X-STREAM-INF')){
                        const bw=parseInt((L[i].match(/BANDWIDTH=([0-9]+)/)||[])[1]||'0');
                        const res=(L[i].match(/RESOLUTION=([0-9x]+)/)||[])[1]||'';
                        const nx=L[i+1];
                        if(nx&&!nx.startsWith('#')&&bw>=bestBW){bestBW=bw;best={url:new URL(nx,master).href,bw,res};}
                    } else if(L[i].startsWith('#EXT-X-MEDIA')){
                        const tm=L[i].match(/TYPE="?([A-Z]+)"?/);
                        if(tm&&tm[1]==='SUBTITLES'){
                            const um=L[i].match(/URI="([^"]+)"/);
                            const nm=L[i].match(/NAME="([^"]*)"/);
                            const lm=L[i].match(/LANGUAGE="([^"]*)"/);
                            if(um) subTracks.push({uri:new URL(um[1],master).href,
                                name:nm?nm[1]:'', lang:lm?lm[1]:''});
                        }
                    }
                }
                return {ok:true,best:best||{url:master,bw:bestBW,res:''}, subTracks};
            }""", "http://127.0.0.1:18765/master.m3u8")
            assert res.get("ok"), res
            assert res["best"]["url"].endswith("video/1080.m3u8"), res["best"]
            assert len(res["subTracks"]) == 2, res["subTracks"]
            assert res["subTracks"][0]["name"] == "Chinese", res["subTracks"]
            print("\n[test 2] master 解析: 最高画质", res["best"]["url"],
                  "| 字幕轨", [(t["name"], t["lang"]) for t in res["subTracks"]])
            print("[test 2] PASS")

            # 3. 字幕下载
            dl2 = db.HLSFetch("http://127.0.0.1:18765/index.html", str(out))
            dl2.meta = dict(dl.meta)   # 模拟真实流程: 同一实例先抓 meta 再下字幕
            await dl2._grab_subtitles(pg, res["subTracks"])
            files = dl2.subtitle_files
            print("\n[test 3] 字幕文件:", [Path(f).name for f in files])
            assert len(files) == 2, files
            for f in files:
                content = Path(f).read_text()
                assert "WEBVTT" in content, f
            print("[test 3] PASS")

            # 4. save_meta sidecar
            dl2.save_meta()
            sidecar = out.with_suffix(".json")
            assert sidecar.exists(), sidecar
            data = json.loads(sidecar.read_text())
            assert data["id"] == "meta_e2e_out", data["id"]
            assert len(data["subtitle_files"]) == 2, data["subtitle_files"]
            print(f"\n[test 4] sidecar: {sidecar.name} 字段={list(data.keys())}")
            print("[test 4] PASS")

            await b.close()

    asyncio.run(run())
    print("\n===== 全部测试通过 =====")


if __name__ == "__main__":
    main()
