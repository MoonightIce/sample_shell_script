#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
download_segmented.py — 单会话内分段下载 HLS 视频并合并为完整文件。
解决: 沙箱对超大单文件下载不稳定(2GB exit 137), 改用多段小文件下载后合并。

方案: 在单个浏览器会话内, 用 ffmpeg 按时间偏移(-ss)逐段下载(每段限时, 体积小),
      所有段在同一会话的 cookie+Referer 下抓取(规避 m3u8 时效), 最后合并。

用法:
  python download_segmented.py <page_url> <out.mp4> [--seg SEC] [--max-seg N]
  --seg 每段秒数(默认120s≈28MB)
"""
import sys, os, json, asyncio, subprocess, time
from pathlib import Path

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36"

def ffmpeg_headers(referer, cookie):
    return f"Referer: {referer}" + (f"\r\nCookie: {cookie}" if cookie else "")

async def main(page_url, out_path, seg=120, max_seg=None):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import download_full_video as dfv
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    vid = page_url.rstrip("/").split("/")[-1]

    print(f"[1] 会话内提取源: {page_url}")
    src = await dfv.extract_highest(page_url)
    if src.get("error"):
        print(f"  !! {src['error']}"); return False
    info = src.get("info", {})
    if not info.get("ok"):
        print(f"  !! 解析失败: {info}"); return False
    best = info["best"]
    referer, cookie = src["referer"], src["cookie"]
    print(f"    画质={best.get('res')} 子流={best['url']}")

    print(f"[2] 探测完整时长")
    meta = dfv.probe(best["url"], referer, cookie)
    total = meta.get("duration_sec") or 0
    print(f"    完整时长={total}s ({total/3600:.2f}h) 分辨率={meta.get('resolution')}")
    if total <= 0:
        print("  !! 无法获取时长"); return False

    n_seg = max_seg or int(total // seg) + 1
    print(f"[3] 分段下载: {n_seg} 段 x {seg}s (offset 0 ~ {total}s)")
    seg_files = []
    t0 = time.time()
    for i in range(n_seg):
        offset = i * seg
        if offset >= total:
            break
        dur = min(seg, total - offset)
        sf = out.parent / f".{vid}_seg{i:03d}.mp4"
        cmd = ["ffmpeg", "-y", "-headers", ffmpeg_headers(referer, cookie),
               "-user_agent", UA, "-ss", str(offset), "-i", best["url"],
               "-t", str(dur), "-c", "copy", "-movflags", "+faststart", str(sf)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        ok = r.returncode == 0 and sf.exists() and sf.stat().st_size > 0
        if ok:
            seg_files.append(sf)
            print(f"    段{i}: offset={offset}s {sf.stat().st_size/1e6:.1f}MB ✓")
        else:
            print(f"    段{i}: 失败({sf.stat().st_size if sf.exists() else 0}字节), 重试一次...")
            # 重试一次
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if r.returncode == 0 and sf.exists() and sf.stat().st_size > 0:
                seg_files.append(sf); print(f"    段{i}: 重试成功 ✓")
            else:
                print(f"    段{i}: 重试仍失败, 跳过. stderr={r.stderr[-200:]}")
        # 放慢避免沙箱/风控
        time.sleep(1)

    if not seg_files:
        print("  !! 无任何分段下载成功"); return False

    print(f"[4] 合并 {len(seg_files)} 段 -> {out}")
    lst = out.parent / f".{vid}_concat.txt"
    lst.write_text("\n".join(f"file '{s.resolve()}'" for s in seg_files))
    r = subprocess.run(["ffmpeg","-y","-f","concat","-safe","0","-i",str(lst),
                        "-c","copy","-movflags","+faststart",str(out)],
                       capture_output=True, text=True, timeout=300)
    merged_ok = r.returncode == 0 and out.exists() and out.stat().st_size > 0
    if not merged_ok:
        print("  !! 合并失败"); return False

    size_mb = round(out.stat().st_size/1e6, 2)
    print(f"[ok] 完成: {size_mb}MB, {len(seg_files)}段, 用时{time.time()-t0:.0f}s")
    # 清理分段
    for sf in seg_files:
        sf.unlink(missing_ok=True)
    lst.unlink(missing_ok=True)
    print(json.dumps({"id":vid,"url":page_url,"source":best["url"],"resolution":best.get('res'),
        "total_sec":round(total,1),"segments":len(seg_files),"out_path":str(out),"size_mb":size_mb},
        ensure_ascii=False, indent=2))
    return True

if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    seg = 120; max_seg = None
    if "--seg" in sys.argv: seg = int(sys.argv[sys.argv.index("--seg")+1])
    if "--max-seg" in sys.argv: max_seg = int(sys.argv[sys.argv.index("--max-seg")+1])
    if len(args) < 2:
        print(__doc__); sys.exit(1)
    ok = asyncio.run(main(args[0], args[1], seg, max_seg))
    sys.exit(0 if ok else 1)
