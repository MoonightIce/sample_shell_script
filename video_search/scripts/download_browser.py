#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
download_browser.py — 浏览器会话内 fetch 全部分片下载 HLS 视频(方案D)。

原理: 在 Playwright 浏览器页面上下文内用 JS fetch 拉取全部分片,
      复用浏览器完整会话(cookie/指纹/Referer), 最贴近真实用户, 不易触发风控。
三层兜底:
  1. 分片级: 每个分片独立下载, 失败单独重试(最多3次), 不拖累其他
  2. 会话级: 若会话被风控断开, 自动重开新会话, 已下载分片保留只续传缺失
  3. 限速: 分片间随机延迟, 模拟真实播放节奏
最后: 本地构造 m3u8, ffmpeg concat 合并为 mp4。

用法:
  python download_browser.py <page_url> <out.mp4> [--seg SEC] [--retries N]
"""
import sys, os, json, re, asyncio, subprocess, time, random
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import meta_extract as me

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36"

class HLSFetch:
    """在浏览器会话内解析并下载 HLS 分片。"""
    def __init__(self, page_url, out, seg=120, retries=3):
        self.page_url = page_url
        self.out = Path(out)
        self.seg = seg
        self.retries = retries
        self.tmp = self.out.parent / f".{self.out.stem}_hls"
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.parts_path = self.tmp / "segments.json"   # 记录已下载分片(断点续传)
        self.segments = self._load_state()
        self.meta = {}              # 详情页元信息(标题/描述/分类/系列/演员/字幕)
        self.subtitle_files = []    # 已下载字幕文件路径

    def _load_state(self):
        if self.parts_path.exists():
            try: return json.loads(self.parts_path.read_text())
            except Exception: pass
        return {}

    def _save_state(self):
        self.parts_path.write_text(json.dumps(self.segments, ensure_ascii=False))

    async def _session_flow(self, browser, ctx, page):
        """在页面上下文内完成: 解析m3u8→fetch分片。返回 (总体进度, 错误消息)。"""
        out = await page.evaluate("""async ({page_url, seg, tmp_hint}) => {
            const info = {done: 0, total: 0, failed: [], skipped: 0};
            try {
                // 1. 找 video 并触发
                const v = document.querySelector('video');
                if (v) { v.muted = true; v.play().catch(()=>{}); }
                document.querySelectorAll('button,[class*=play]').forEach(b=>{if(/play/i.test(b.className))b.click();});
                await new Promise(r=>setTimeout(r, 4000));

                // 2. 从当前播放器/网络抓 m3u8 (通过 performance 或 media 源)
                //    兜底: 从 performance entries 找 m3u8
                let master = null;
                const perfs = performance.getEntriesByType('resource').map(e=>e.name);
                for (const n of perfs) if (n.includes('.m3u8')) { master = n; break; }
                return {master, perfs};
            } catch(e) { return {error: String(e)}; }
        }""", {"page_url": self.page_url, "seg": self.seg, "tmp_hint": str(self.tmp)})
        return out

    async def run(self):
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True,
                executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                args=["--no-sandbox","--disable-blink-features=AutomationControlled"])
            ctx = await browser.new_context(user_agent=UA, viewport={"width":1280,"height":900})
            page = await ctx.new_page()

            # 捕获 m3u8 请求
            m3u8s = []
            page.on("request", lambda r: m3u8s.append(r.url) if ".m3u8" in r.url else None)

            try:
                await page.goto(self.page_url, wait_until="domcontentloaded", timeout=45000)
            except Exception:
                pass
            for _ in range(40):
                try:
                    t = await page.title()
                    if t and "Just a moment" not in t and t.strip(): break
                except Exception: pass
                await page.wait_for_timeout(1000)
            try:
                await page.wait_for_timeout(3000)
                await page.evaluate("""()=>{const v=document.querySelector('video');if(v){v.muted=true;v.play().catch(()=>{});}document.querySelectorAll('button,[class*=play]').forEach(b=>{if(/play/i.test(b.className))b.click();});}""")
                await page.wait_for_timeout(5000)
            except Exception: pass

            # 抓取详情页元信息(标题/描述/分类/系列/演员/字幕标记), 失败不阻塞下载
            try:
                await self._grab_meta(page)
            except Exception as e:
                print(f"  (元信息抓取失败, 跳过: {e})", flush=True)

            # 选择 master 或子流 m3u8 (优先 master 列表, 再解析最高画质)
            media_m3u8s = [u for u in dict.fromkeys(m3u8s) if ".m3u8" in u]
            print(f"[session] 捕获到 {len(media_m3u8s)} 个 m3u8")
            if not media_m3u8s:
                print("  !! 未捕获 m3u8 (可能风控)"); await browser.close(); return False
            master = media_m3u8s[0]

            # 解析 master → 最高画质子流 + 字幕轨(#EXT-X-MEDIA:TYPE=SUBTITLES)
            res = await page.evaluate("""async (master) => {
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
            }""", master)
            if not res.get("ok"):
                print(f"  !! master 解析失败: {res}"); await browser.close(); return False
            best = res["best"]
            sub_tracks = res.get("subTracks") or []
            print(f"[session] 选择画质 {best.get('res','?')} (BW={best.get('bw')}) : {best['url']}")
            if sub_tracks:
                print(f"[session] 发现 {len(sub_tracks)} 条字幕轨")
                try:
                    await self._grab_subtitles(page, sub_tracks)
                except Exception as e:
                    print(f"  (字幕下载失败, 跳过: {e})", flush=True)

            # 解析分片列表
            seg_res = await page.evaluate("""async (url) => {
                const r = await fetch(url, {headers:{'Referer':location.origin+'/'}});
                if(!r.ok) return {err:'fetch '+r.status};
                const txt = await r.text();
                const base = url.slice(0, url.lastIndexOf('/')+1);
                const lines = txt.split('\\n');
                const segs = [];
                for(let i=0;i<lines.length;i++){
                    const m = lines[i].match(/^#EXTINF:([0-9.]+),(.*)/);
                    if(m && i+1<lines.length && !lines[i+1].startsWith('#')){
                        // 取当前 #EXTINF 行的下一行作为分片URI(用索引i, 不用indexOf!)
                        segs.push({url:new URL(lines[i+1].trim(), base).href, dur:parseFloat(m[1])});
                    }
                }
                return {ok:true, segs, count:segs.length};
            }""", best["url"])
            if not seg_res.get("ok"):
                print(f"  !! 分片解析失败: {seg_res}"); await browser.close(); return False
            segs = seg_res["segs"]
            print(f"[session] 分片总数: {len(segs)}")

            # 初始化进度文件
            self._init_progress(self.page_url, len(segs))

            # 逐片 fetch 下载, 分片级重试 + 限速
            total = len(segs)
            done = 0
            failed = []
            for i, sg in enumerate(segs):
                # 断点续传: 已下载过则跳过
                key = str(i)
                part = self.tmp / f"seg_{i:05d}.ts"
                if self.segments.get(key, {}).get("ok") and part.exists():
                    done += 1; continue
                # 下载该分片 (在页面上下文 fetch 成 base64 传回)
                got = await page.evaluate("""async (url) => {
                    try {
                        const r = await fetch(url, {headers:{'Referer':location.origin+'/'}});
                        if(!r.ok) return {err:'fetch '+r.status};
                        const buf = await r.arrayBuffer();
                        const bytes = new Uint8Array(buf);
                        let bin=''; const CH=0x8000;
                        for(let k=0;k<bytes.length;k+=CH){bin+=String.fromCharCode.apply(null,bytes.subarray(k,k+CH));}
                        return {ok:true, b64: btoa(bin)};
                    } catch(e){ return {err:String(e)}; }
                }""", sg["url"])
                ok = False
                if got.get("ok"):
                    try:
                        import base64
                        data = base64.b64decode(got["b64"])
                        part.write_bytes(data)
                        self.segments[key] = {"ok": True, "bytes": len(data)}
                        self._save_state()
                        ok = True
                    except Exception:
                        ok = False
                if not ok:
                    # 分片级重试
                    for a in range(self.retries):
                        time.sleep(1.5+random.random())
                        got = await page.evaluate("""async (url) => {
                            try{const r=await fetch(url,{headers:{'Referer':location.origin+'/'}});if(!r.ok)return{err:'f'+r.status};const b=await r.arrayBuffer();const by=new Uint8Array(b);let s='';const C=0x8000;for(let k=0;k<by.length;k+=C)s+=String.fromCharCode.apply(null,by.subarray(k,k+C));return{ok:true,b64:btoa(s)};
                            }catch(e){return{err:String(e)}}}""", sg["url"])
                        if got.get("ok"):
                            try:
                                import base64
                                part.write_bytes(base64.b64decode(got["b64"]))
                                self.segments[key]={"ok":True,"bytes":part.stat().st_size}
                                self._save_state(); ok=True; break
                            except Exception: pass
                if ok: done += 1
                else:
                    failed.append(i)
                    print(f"    分片{i} 最终失败")
                self._update_progress(done, total)
                if (i+1) % 10 == 0:
                    print(f"    进度 {i+1}/{total} (done={done})", flush=True)
                # 限速
                await page.wait_for_timeout(int(80 + random.random()*150))

            await browser.close()
            if failed:
                print(f"[结果] 完成 {done}/{total}, 失败 {len(failed)} 个分片: {failed[:10]}")
                return False
            self.save_meta()   # 分片就绪即先写一份元信息 sidecar
            return True

    async def _grab_meta(self, page):
        """在页面上下文抓取详情页元信息(标题/描述/分类/系列/演员/字幕标记)。"""
        raw = await me.fetch_meta_in_page(page)
        self.meta = me.normalize_meta(raw, self.page_url,
                                      title_fallback=raw.get("title", ""))
        t = self.meta.get("title", "")
        print(f"[meta] 标题: {t[:60]}", flush=True)
        if self.meta.get("categories"):
            print(f"[meta] 分类: {', '.join(self.meta['categories'][:10])}", flush=True)
        if self.meta.get("series"):
            print(f"[meta] 系列: {', '.join(self.meta['series'][:5])}", flush=True)
        if self.meta.get("actress"):
            print(f"[meta] 演员: {', '.join(self.meta['actress'][:10])}", flush=True)
        if self.meta.get("subtitles"):
            print(f"[meta] 页面字幕标记: {', '.join(self.meta['subtitles'])}", flush=True)

    async def _grab_subtitles(self, page, tracks):
        """下载 HLS 字幕轨(单文件 vtt 或分片拼接), 存 <stem>.<lang>.vtt/srt。"""
        for i, tr in enumerate(tracks):
            uri = tr.get("uri")
            if not uri:
                continue
            got = await page.evaluate("""async (uri) => {
                try { const r = await fetch(uri, {headers:{'Referer':location.origin+'/'}});
                      if (!r.ok) return {err:'fetch '+r.status};
                      return {ok:true, text: await r.text()}; }
                catch(e) { return {err:String(e)}; }
            }""", uri)
            if not got.get("ok"):
                print(f"  [sub] 轨{i} 抓取失败: {got.get('err')}", flush=True)
                continue
            text = got["text"]
            # 若返回的是 m3u8 分片列表 → 逐片抓取拼接
            if text.lstrip().startswith("#EXTM3U"):
                lines = [l.strip() for l in text.splitlines()
                         if l.strip() and not l.startswith("#")]
                parts = []
                for su in lines:
                    seg_url = su if su.startswith("http") else uri.rsplit("/", 1)[0] + "/" + su
                    g2 = await page.evaluate("""async (uri) => {
                        try { const r = await fetch(uri, {headers:{'Referer':location.origin+'/'}});
                              if (!r.ok) return {err:r.status};
                              return {ok:true, text: await r.text()}; }
                        catch(e) { return {err:String(e)}; }
                    }""", seg_url)
                    if g2.get("ok"):
                        parts.append(g2["text"])
                    else:
                        print(f"  [sub] 分片失败: {seg_url}", flush=True)
                if not parts:
                    continue
                text = "\n".join(parts)
            ext = "vtt" if "WEBVTT" in text else "srt"
            lang = tr.get("lang") or tr.get("name") or f"sub{i}"
            safe_lang = re.sub(r"[^A-Za-z0-9_-]", "_", lang)[:20].strip("_") or f"sub{i}"
            out = self.out.with_name(f"{self.out.stem}.{safe_lang}.{ext}")
            try:
                out.write_text(text, encoding="utf-8")
            except Exception as e:
                print(f"  [sub] 写入失败: {e}", flush=True)
                continue
            self.subtitle_files.append(str(out))
            tr["file"] = str(out)
            print(f"[sub] 字幕已保存: {out.name} ({ext}, {len(text)} 字符)", flush=True)

    def save_meta(self):
        """写元信息 sidecar: <out>.json(与 mp4 同目录同 basename)。"""
        if not self.meta:
            return
        meta = dict(self.meta)
        meta["id"] = self.out.stem
        if self.subtitle_files:
            meta["subtitle_files"] = self.subtitle_files
        if self.out.exists():
            meta["video_file"] = str(self.out)
            meta["video_size"] = self.out.stat().st_size
        out = self.out.with_suffix(".json")
        try:
            out.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[meta] 元信息已保存: {out.name}", flush=True)
        except Exception as e:
            print(f"[meta] 写入失败: {e}", flush=True)

    def _init_progress(self, url, total):
        """初始化进度文件 (data/downloads/.progress.json)。"""
        self.t0 = time.time()
        self.progress_file = self.tmp.parent / ".progress.json"
        self._update_progress(0, total, init=True)

    def _update_progress(self, done, total, init=False):
        """更新进度文件: 每片完成后调用, 记录完成数/总片数/百分比/速度/ETA。"""
        if not hasattr(self, "progress_file"):
            return
        elapsed = time.time() - self.t0
        pct = done / total * 100 if total else 0
        speed = done / elapsed if elapsed > 0 else 0  # 片/秒
        eta = (total - done) / speed if speed > 0 else -1
        data = {
            "id": self.out.stem,
            "url": self.page_url,
            "done": done,
            "total": total,
            "pct": round(pct, 1),
            "speed_pps": round(speed, 2),
            "eta_sec": int(eta) if eta > 0 else -1,
            "status": "downloading" if done < total else "finished",
            "updated_at": time.strftime("%H:%M:%S"),
        }
        try:
            self.progress_file.write_text(json.dumps(data, ensure_ascii=False))
        except Exception:
            pass

    def merge(self):
        """把已下载的分片按序 concat 成 mp4。"""
        parts = sorted(self.tmp.glob("seg_*.ts"))
        if not parts:
            return False
        lst = self.tmp / "concat.txt"
        lst.write_text("\n".join(f"file '{p.resolve()}'" for p in parts))
        r = subprocess.run(["ffmpeg","-y","-f","concat","-safe","0","-i",str(lst),
                            "-c","copy","-movflags","+faststart",str(self.out)],
                           capture_output=True, text=True, timeout=300)
        ok = r.returncode==0 and self.out.exists() and self.out.stat().st_size>0
        if ok:
            self.save_meta()   # 合并完成后更新 sidecar 的 video_file/video_size
        return ok

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("url"); ap.add_argument("out")
    ap.add_argument("--seg", type=int, default=120)
    ap.add_argument("--retries", type=int, default=3, help="每个分片重试次数")
    ap.add_argument("--session-retries", type=int, default=20, help="会话级重试次数(风控时等待后重开)")
    ap.add_argument("--interval", type=int, default=180, help="会话级重试间隔秒")
    args = ap.parse_args()

    dl = HLSFetch(args.url, args.out, args.seg, args.retries)
    print(f"[启动] 浏览器内fetch下载: {args.url}")
    for attempt in range(1, args.session_retries+1):
        print(f"\n===== 会话尝试 {attempt}/{args.session_retries} =====", flush=True)
        ok = asyncio.run(dl.run())
        if ok:
            print("[下载完成] 全部分片就绪")
            break
        print(f"[未完成] 会话尝试{attempt}失败(可能风控/中断), 等待{args.interval}s后重试...", flush=True)
        if attempt < args.session_retries:
            time.sleep(args.interval)
    else:
        print(f"[放弃] {args.session_retries}次会话尝试后仍未完成")
        return

    print("[合并] 本地 concat 为 mp4 ...")
    mok = dl.merge()
    if mok:
        size = dl.out.stat().st_size/1e6
        print(f"[ok] 合并完成: {dl.out} ({size:.1f}MB)")
        # 临时分片不在此删除(沙箱批量删除确认会阻塞), 由外部统一清理
    else:
        print("[!!] 合并失败, 分片保留在 " + str(dl.tmp))

if __name__ == "__main__":
    main()
