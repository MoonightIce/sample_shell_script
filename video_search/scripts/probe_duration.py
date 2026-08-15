#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""获取目标地址完整时长与最高画质(不下载全片)。"""
import sys, json, asyncio, subprocess
from pathlib import Path
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36"
sys.path.insert(0, str(Path(__file__).resolve().parent))
import download_full_video as dfv

async def main(url):
    src = await dfv.extract_highest(url)
    if src.get("error"):
        print(json.dumps({"id": url.split("/")[-1], "error": src["error"]}, ensure_ascii=False)); return
    info = src.get("info", {})
    if not info.get("ok"):
        print(json.dumps({"id": url.split("/")[-1], "error": str(info)}, ensure_ascii=False)); return
    best = info["best"]
    meta = dfv.probe(best["url"], src["referer"], src["cookie"])
    print(json.dumps({
        "id": url.rstrip("/").split("/")[-1],
        "url": url,
        "resolution": best.get("res"),
        "source": best["url"],
        "duration_sec": meta.get("duration_sec"),
        "duration_h": round((meta.get("duration_sec") or 0)/3600, 2),
    }, ensure_ascii=False))

asyncio.run(main(sys.argv[1]))
