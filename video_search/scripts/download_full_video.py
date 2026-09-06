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

sys.path.insert(0, str(Path(__file__).resolve().parent))

# 环境自举: 自动使用项目 venv(video_search/.venv)运行, 无需手动指定解释器/装依赖
import env_check  # noqa: E402
env_check.ensure_playwright()
env_check.check_ffmpeg()
env_check.check_chrome()

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36"

# 已知 CDN 域(missav 系), 与页面主域一并保留 cookie
KNOWN_CDN_DOMAINS = ("missav.ws", "surrit.com", "growcdnssedge.com")

# 已知广告域特征(快手 kwai 广告等)
AD_HINTS = ("kwai.net", "doubleclick", "googlesyndication", "amazon-adsystem")

def is_ad_like(url: str) -> bool:
    """判断 URL 是否疑似广告: 已知广告域 + 路径含 ad/ads/banner/advert 段(含 ad- 前缀形态)。

    例: https://s1.kwai.net/bs2/ad-i18n-dsp/xxx.mp4 → True(快手广告 CDN + ad- 路径)
    """
    if not url:
        return False
    u = url.lower()
    for h in AD_HINTS:
        if h in u:
            return True
    return bool(re.search(r"(^|/)(ad|ads|banner|advert|adx)(/|[-_]|$)", u))

def _cookie_keep(page_url: str, domain: str) -> bool:
    """cookie 是否应随下载请求携带: 当前页面主域或其子域, 或已知 CDN 域。"""
    if not domain:
        return False
    host = page_url.rstrip("/").split("://")[-1].split("/")[0]
    d = domain.lstrip(".")
    if host == d or d.endswith("." + host):
        return True
    return any(d == k or d.endswith("." + k) for k in KNOWN_CDN_DOMAINS)

async def extract_from_page(page, ctx, page_url, wait_play=6000):
    """从已打开(已通过 CF 挑战)的页面提取视频源。供批量流程复用, 无需重复开浏览器。

    提取策略(不依赖预先注册 request 监听, 用 performance 资源 + DOM):
      1. 触发播放(找 <video> / play 按钮), 等待播放器上报资源
      2. performance.getEntriesByType('resource') 收集 m3u8/mp4 请求
      3. DOM: 脚本里的 flashvars 配置 / <video src> / <source>
      4. 有 m3u8 → 页面内 fetch master 解析最高画质子流
         无 m3u8 → mp4 直链兜底(91porn 等老式播放器常见)
    返回 dict: {source_type: "hls"|"mp4", ... , cookie, referer, title, http_status}
    """
    # 1. 触发播放
    try:
        await page.evaluate("""() => {
            const v=document.querySelector('video');
            if(v){v.muted=true;v.play().catch(()=>{});}
            document.querySelectorAll('button,[class*=play]').forEach(b=>{if(/play/i.test(b.className))b.click();});
        }""")
        await page.wait_for_timeout(wait_play)
    except Exception:
        pass

    # 2+3. 收集候选(m3u8 / mp4), 分优先级:
    #   pref: flashvars 专键(video_url/playurl 等主视频字段) + 播放器容器内 <video>
    #   mp4s: 泛键/非播放器 video/performance 资源(含广告, 仅兜底)
    cand = await page.evaluate("""() => {
        const res = {m3u8s: [], pref: [], mp4s: []};
        const push = (a, v) => { if (v && !a.includes(v)) a.push(v); };
        const isAdUrl = u => /(^|\\/)(ad|ads|banner|advert|adv)(\\/|$)/i.test(u)
            || /doubleclick|googlesyndication|amazon-adsystem/i.test(u);
        // performance 资源(播放器触发的媒体请求, 可能含广告)
        for (const e of performance.getEntriesByType('resource')) {
            const n = e.name;
            if (/\\.m3u8(\\?|$)/i.test(n)) push(res.m3u8s, n);
            else if (/\\.mp4(\\?|$)/i.test(n)) push(res.mp4s, n);
        }
        // 脚本配置(flashvars/player): 专键=主视频, 泛键=兜底, 广告键跳过
        const ADKEY = /^ad|banner|advert/i;
        const MAINKEY = /^(video_url|videourl|playurl|play_url|vurl|video|url|src|file|source|hd_url|sd_url|ld_url|mp4|download_url|video_url2|video_url3)$/i;
        document.querySelectorAll('script').forEach(s => {
            const t = s.textContent || '';
            if (!/video_url|flashvars|player|\\.mp4|\\.m3u8/i.test(t)) return;
            const re = /([A-Za-z_][A-Za-z0-9_]*)\\s*[:=]\\s*["']([^"']{5,800})["']/g;
            let m;
            while ((m = re.exec(t))) {
                if (ADKEY.test(m[1])) continue;
                const v = m[2].replace(/&amp;/g,'&');
                if (/\\.m3u8(\\?|$)/i.test(v) && !res.m3u8s.includes(v)) res.m3u8s.push(v);
                else if (/\\.mp4(\\?|$)/i.test(v) && !isAdUrl(v)) {
                    if (MAINKEY.test(m[1])) push(res.pref, v); else push(res.mp4s, v);
                }
            }
        });
        // <video> 元素: 播放器容器内(#player/.player)视为主视频
        document.querySelectorAll('video').forEach(v => {
            const src = v.src || v.currentSrc || (v.querySelector('source')||{}).src || '';
            if (!src || isAdUrl(src)) return;
            if (/\\.m3u8(\\?|$)/i.test(src)) push(res.m3u8s, src);
            else if (/\\.mp4(\\?|$)/i.test(src)) {
                const inPlayer = !!(v.closest('#player,.player,[id*=player],[class*=player]'));
                (inPlayer ? res.pref : res.mp4s).push(src);
            }
        });
        return res;
    }""")

    m3u8s = list(dict.fromkeys(cand["m3u8s"]))
    # 合并顺序: 专键优先 > 泛键/元素/performance(兜底含广告)
    mp4s = list(dict.fromkeys(cand["pref"] + cand["mp4s"]))
    cookies = await ctx.cookies()
    jar = "; ".join(f"{c['name']}={c['value']}" for c in cookies
                    if _cookie_keep(page_url, c.get('domain','')))
    referer = f"https://{page_url.rstrip('/').split('://')[-1].split('/')[0]}/"
    title = ""
    try:
        title = (await page.title()) or ""
    except Exception:
        pass

    if m3u8s:
        master = m3u8s[0]
        # 页面上下文 fetch master, 解析最高画质子流
        info = await page.evaluate("""async (master) => {
            const r = await fetch(master, {headers:{'Referer':location.origin+'/'}});
            if(!r.ok) return {err:'fetch fail '+r.status};
            const txt = await r.text();
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
        return {"source_type": "hls", "master": master, "info": info,
                "cookie": jar, "referer": referer, "title": title}
    if mp4s:
        return {"source_type": "mp4", "direct_url": mp4s[0], "candidates": mp4s,
                "cookie": jar, "referer": referer, "title": title}
    return {"error": "未捕获 m3u8 也无 mp4 直链", "cookie": jar,
            "referer": referer, "title": title}


async def extract_highest(page_url):
    """打开页面, 提取视频源 URL + 会话cookie + referer。CLI 兼容入口。"""
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        ctx = await browser.new_context(user_agent=UA, viewport={"width":1280,"height":800})
        page = await ctx.new_page()
        status = None
        try:
            resp = await page.goto(page_url, wait_until="domcontentloaded", timeout=45000)
            status = resp.status if resp else None
        except Exception:
            pass
        for _ in range(40):
            try:
                t = await page.title()
                if t and "Just a moment" not in t and t.strip():
                    break
            except Exception:
                pass
            await page.wait_for_timeout(1000)
        await page.wait_for_timeout(3000)
        src = await extract_from_page(page, ctx, page_url)
        src["http_status"] = status
        await browser.close()
        return src

def probe(url, referer, cookie, timeout=90):
    import json as J
    cmd = ["ffprobe","-v","quiet","-print_format","json"]
    if referer:   # 仅远程探测需要 http 选项(本地文件加 -headers/-user_agent 会报错)
        cmd += ["-headers", f"Referer: {referer}" + (f"\r\nCookie: {cookie}" if cookie else ""),
                "-user_agent", UA]
    cmd += ["-show_format", "-show_streams", url]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {}
    if r.returncode != 0: return {}
    try: info = J.loads(r.stdout)
    except Exception: return {}
    dur = float(info.get("format",{}).get("duration",0))
    w=h=None
    for s in info.get("streams",[]):
        if s.get("codec_type")=="video": w,h=s.get("width"),s.get("height"); break
    return {"duration_sec": round(dur,1), "resolution": f"{w}x{h}" if w else None}

def download(url, referer, cookie, out, max_dur=None, progress=None, total_dur=None,
             prog_interval=1.0, stall_timeout=60, verbose=False):
    """ffmpeg 下载。progress 可选回调 progress(pct, cur_sec, total_sec, size_mb=None)。

    进度实现: ffmpeg -progress 写到独立临时文件(写管道会被 AVIO 块缓冲、退出才 flush,
    导致进度一次性到达), Python 每 prog_interval 秒轮询文件解析 out_time_us/ms,
    以首个 out_time 为基准(流时间戳可能非 0 起跳)算相对进度。
    - 时长已知: 每 >=1% 回调一次(附已写入大小)
    - 时长未知: 每 2s 回调 progress(-1, 0, 0, size_mb), 用文件大小提供反馈(避免"无输出卡住")
    - 停滞检测: 输出文件大小 stall_timeout 秒无增长 → 判为挂起, 杀进程返回 False(配合重试)
    """
    import tempfile
    out = Path(out)
    tmp_prog = Path(tempfile.mktemp(suffix=".prog"))
    tmp_err = Path(tempfile.mktemp(suffix=".err"))
    cmd = ["ffmpeg","-y"]
    if referer:   # 远程下载需要 http 选项(本地文件加会报 Option not found)
        cmd += ["-headers", f"Referer: {referer}" + (f"\r\nCookie: {cookie}" if cookie else ""),
                "-user_agent", UA,
                # HTTP 读写/连接超时(默认无限! CDN 响应异常时 ffmpeg 会永久挂起)
                "-rw_timeout", "30000000",   # 30s 无数据读写超时(微秒)
                "-timeout", "20000000"]      # 20s 连接超时(微秒)
    cmd += ["-i", url, "-progress", str(tmp_prog)]
    if max_dur: cmd += ["-t", str(max_dur)]
    cmd += ["-c","copy","-movflags","+faststart", str(out)]
    errf = open(tmp_err, "w")
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=errf)
    # 时长未知时: 下载前 HEAD 探测总大小(用于"已写MB/总MB"百分比); 失败/过小则退回仅报大小
    total_size = None
    if not total_dur and referer:
        total_size = _probe_content_length(url, referer, cookie)
        if total_size and total_size >= 1:   # <1MB 视为探测无效(如 0.0MB 假值)
            if verbose:
                print(f"    [size] 探测到总大小 {total_size:.1f}MB, 按大小显示进度", flush=True)
        else:
            total_size = None
    last = -1.0
    base_t = None
    t_last_report = 0.0
    last_activity = time.time()
    last_size = -1
    try:
        while proc.poll() is None:
            time.sleep(prog_interval)
            # 停滞检测: 输出文件大小长时间无增长 → 判挂起(CDN 卡死/无数据), 杀进程走重试
            try:
                cur_size = out.stat().st_size
            except OSError:
                cur_size = -1
            if cur_size != last_size:
                last_size = cur_size
                last_activity = time.time()
            elif time.time() - last_activity > stall_timeout:
                print(f"    ✗ 下载停滞({stall_timeout:.0f}s 无进展, 已写 {max(last_size,0)//1024//1024}MB), "
                      f"终止进程", flush=True)
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except Exception:
                    pass
                break
            if not progress:
                continue
            size_mb = None
            try:
                size_mb = out.stat().st_size / 1e6
            except Exception:
                pass
            if total_dur:
                t = _read_progress_time(tmp_prog)
                if t is None:
                    continue
                if base_t is None:
                    base_t = t
                    continue
                dt = max(t - base_t, 0.0)                # 相对已写入时长(时间戳偏移修正)
                pct = min(dt / total_dur * 100, 100.0)
                if pct - last >= 1.0:                    # 每 >=1% 回调一次, 避免刷屏
                    last = pct
                    progress(pct, dt, total_dur, size_mb, "time")
            elif total_size and size_mb is not None:
                pct = min(size_mb / total_size * 100, 100.0)   # 大小百分比(时长未知场景)
                if pct - last >= 1.0:
                    last = pct
                    progress(pct, size_mb, total_size, None, "size")
            else:
                now = time.time()                        # 全未知: 每 2s 报已写入大小
                if now - t_last_report >= 2.0:
                    t_last_report = now
                    progress(-1.0, 0.0, 0.0, size_mb)
    finally:
        errf.close()
        try:
            tmp_prog.unlink(missing_ok=True)
        except Exception:
            pass
    ok = proc.returncode == 0 and out.exists() and out.stat().st_size > 0
    if not ok:
        try:
            err = tmp_err.read_text(errors="ignore")[-300:].strip()
        except Exception:
            err = ""
        print(f"    ✗ ffmpeg 失败(exit={proc.returncode}): {err[-200:]}", flush=True)
    try:
        tmp_err.unlink(missing_ok=True)
    except Exception:
        pass
    # 收尾强制 100% 仅在成功时(失败时保持实际进度, 避免"100% 却失败"误导)
    if ok and progress and last < 100.0:
        try:
            sz = out.stat().st_size / 1e6
        except Exception:
            sz = None
        if total_dur:
            progress(100.0, total_dur, total_dur, sz, "time")
        elif total_size:
            progress(100.0, total_size, total_size, None, "size")
    return ok


def _probe_content_length(url, referer, cookie, timeout=10):
    """HEAD 请求探测远程文件总大小(MB)。失败/不支持返回 None。

    用于时长未知(探测失败回退)场景下显示"已写MB/总MB"百分比进度。
    """
    try:
        import urllib.request
        req = urllib.request.Request(url, method="HEAD",
                                     headers={"User-Agent": UA,
                                              "Referer": referer,
                                              "Cookie": cookie or ""})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            n = r.headers.get("Content-Length")
            return int(n) / 1e6 if n and n.isdigit() else None
    except Exception:
        return None


def download_range_parallel(url, referer, cookie, out, progress=None,
                            connections=8, min_chunk_mb=8, stall_timeout=60,
                            socket_timeout=30, verbose=False):
    """多连接 Range 并发下载 mp4 直链(绕过 CDN 单连接限速, 显著提速, 对标 Downie)。

    前置条件: HEAD 能取 Content-Length 且服务器支持 Range(206 验证)。
    不满足/任一块失败 → 返回 False, 调用方回退 ffmpeg 单连接。
    progress: progress(pct, cur_mb, total_mb, None, "size")。
    """
    import urllib.request
    import threading
    import concurrent.futures
    out = Path(out)
    total_mb = _probe_content_length(url, referer, cookie)
    if not total_mb or total_mb < min_chunk_mb:
        return False
    total = int(total_mb * 1e6)
    # Range 支持验证(请求末 1 字节, 期望 206)
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": UA, "Referer": referer, "Cookie": cookie or "",
            "Range": f"bytes={total-1}-{total-1}"})
        with urllib.request.urlopen(req, timeout=15) as r:
            if r.status != 206:
                if verbose:
                    print("    [并发] 服务器不支持 Range(非 206), 回退单连接", flush=True)
                return False
    except Exception as e:
        if verbose:
            print(f"    [并发] Range 探测失败: {e}, 回退单连接", flush=True)
        return False
    n = max(1, min(connections, total // int(min_chunk_mb * 1e6) + 1))
    if n < 2:
        return False
    chunk = total // n
    parts = [out.with_name(f".{out.stem}.part{i}") for i in range(n)]
    done = [0] * n
    failed = [False] * n
    abort = threading.Event()

    def _fetch(i):
        start = i * chunk
        end = (i + 1) * chunk - 1 if i < n - 1 else total - 1
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Referer": referer, "Cookie": cookie or "",
                "Range": f"bytes={start}-{end}"})
            with urllib.request.urlopen(req, timeout=socket_timeout) as r, \
                    open(parts[i], "wb") as f:
                while True:
                    if abort.is_set():
                        return
                    b = r.read(256 * 1024)
                    if not b:
                        break
                    f.write(b)
                    done[i] += len(b)
        except Exception:
            failed[i] = True

    if verbose:
        print(f"    [并发] {n} 连接分块下载 {total_mb:.1f}MB (每块 ~{chunk/1e6:.0f}MB)", flush=True)
    last = -1.0
    last_total = -1
    last_activity = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as ex:
        futs = [ex.submit(_fetch, i) for i in range(n)]
        while any(not f.done() for f in futs):
            time.sleep(1)
            tdone = sum(done)
            if tdone != last_total:
                last_total = tdone
                last_activity = time.time()
            elif time.time() - last_activity > stall_timeout:
                print(f"    ✗ 并发下载停滞({stall_timeout:.0f}s 无进展), 中止", flush=True)
                abort.set()
                break
            if progress:
                pct = min(tdone / total * 100, 100.0)
                if pct - last >= 1.0:
                    last = pct
                    progress(pct, tdone / 1e6, total_mb, None, "size")
        for f in futs:
            try:
                f.result(timeout=socket_timeout + 10)
            except Exception:
                pass
    if abort.is_set() or any(failed):
        for p in parts:
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass
        return False
    # 按序拼接
    try:
        with open(out, "wb") as o:
            for p in parts:
                o.write(p.read_bytes())
    finally:
        for p in parts:
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass
    ok = out.exists() and out.stat().st_size == total
    if ok and progress:
        progress(100.0, total_mb, total_mb, None, "size")
    return ok


def _read_progress_time(f: Path):
    """从 ffmpeg -progress 文件中取最新的 out_time(秒)。优先 us, 兼容 ms。"""
    try:
        txt = f.read_text(errors="ignore")
    except Exception:
        return None
    m = re.findall(r"out_time_us=(\d+)", txt)
    if m:
        return int(m[-1]) / 1e6
    m = re.findall(r"out_time_ms=(\d+)", txt)
    if m:
        return int(m[-1]) / 1e3
    return None


def pick_best_mp4(candidates, referer, cookie, max_probe=6, min_dur=20, probe_timeout=15,
                  verbose=False):
    """从候选 mp4 直链中选最可能的主视频: 逐个 ffprobe 探测时长, 取最长者。

    背景: 91porn 详情页嵌有 10s 级广告 mp4, 直接取第一个会下到广告;
    主视频通常远长于广告(几十秒~数小时), 选时长最长即可排除广告。
    探测细节仅 verbose 时打印; 选中/回退结果始终打印一行。
    返回 (url, meta); 全部探测失败返回 (None, {})。
    """
    best_url, best_meta, best_dur = None, {}, 0
    for i, u in enumerate(candidates[:max_probe]):
        if verbose:
            print(f"    探测候选[{i}]: {u[:90]}", flush=True)
        try:
            meta = probe(u, referer, cookie, timeout=probe_timeout)
        except Exception as e:
            if verbose:
                print(f"      ✗ 探测异常(跳过): {e}", flush=True)
            meta = {}
        dur = meta.get("duration_sec") or 0
        if verbose:
            print(f"      → {dur}s {meta.get('resolution','?')}", flush=True)
        if dur > best_dur:
            best_url, best_meta, best_dur = u, meta, dur
    if best_url and best_dur >= min_dur:
        print(f"    → 选中: {best_url[:100]} ({best_dur}s, 候选中最长)", flush=True)
        return best_url, best_meta
    if best_url and best_dur > 0:
        # 有可读候选但偏短(< min_dur): 可能本身就是短视频, 用最长的
        print(f"    → 选中(时长偏短 {best_dur}s): {best_url[:90]}", flush=True)
        return best_url, best_meta
    if candidates:
        # 全部探测失败(0s): 回退第一个候选下载, 下载后由调用方校验
        # (ffprobe 读不到时长≠ffmpeg 下载失败, 后者顺序读更宽容)
        fb = candidates[0]
        print(f"  !! 全部候选探测失败(0s), 回退下载第一个候选(下载后校验): {fb[:90]}", flush=True)
        return fb, {}
    return None, {}

def main(page_url, out_path=None, max_dur=None):
    vid = page_url.rstrip("/").split("/")[-1]
    out = Path(out_path) if out_path else Path.cwd()/f"{vid}_full.mp4"
    print(f"[1] 打开目标并在会话内提取源: {page_url}")
    src = asyncio.run(extract_highest(page_url))
    if src.get("error"):
        print(f"  !! {src['error']}"); return
    stype = src.get("source_type", "hls")

    if stype == "mp4":
        # ---- mp4 直链: 候选逐个探测, 选最长(排除广告短片)后 ffmpeg 下载 ----
        cands = src.get("candidates") or [src["direct_url"]]
        print(f"[1] 源形态: mp4 直链, {len(cands)} 个候选(逐个探测选最长)")
        url, meta = pick_best_mp4(cands, src["referer"], src["cookie"])
        if not url:
            print("  !! 所有候选探测失败"); return
        print(f"    选定: {url}")
        print(f"    时长={meta.get('duration_sec')}s 分辨率={meta.get('resolution')}")
        print(f"[3] 下载 -> {out}" + (f" (限时{max_dur}s)" if max_dur else " (完整)"))
        t0 = time.time()
        ok = download(url, src["referer"], src["cookie"], out, max_dur)
        if ok:
            size_mb = round(out.stat().st_size/1e6,2)
            print(f"[ok] 完成: {size_mb}MB, 用时{time.time()-t0:.0f}s")
            print(json.dumps({"id":vid,"http_status":src["http_status"],"source":url,
                "source_type":"mp4","meta":meta,"out_path":str(out),"size_mb":size_mb},
                ensure_ascii=False, indent=2))
        else:
            print("[!!] 下载失败")
        return

    # ---- HLS(m3u8): 现有流程 ----
    info = src.get("info",{})
    if not info.get("ok"):
        print(f"  !! 解析主播放列表失败: {info}"); return
    best = info["best"]
    print(f"[1] 源形态: HLS(m3u8)")
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
            "resolution":best.get('res'),"source_type":"hls","meta":meta,"out_path":str(out),"size_mb":size_mb},
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
