#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
download_91porn.py — 一键批量下载 91porn 分类列表页的全部视频。

用法(默认即可跑):
  python download_91porn.py                          # category=rf, 第1-5页, 输出 /Users/moonightice/Movies/短片
  python download_91porn.py --category top           # 只下 top 分类
  python download_91porn.py --category top,rf,hot    # 一次下多个分类(跨分类自动去重)
  python download_91porn.py --category top,rf,hot --per-cat 2   # 每分类只下2个(测试)
  python download_91porn.py --by-page                            # 逐页模式: 下完第1页24个再翻第2页
  python download_91porn.py --pages 3                # 只下第1-3页
  python download_91porn.py --start 2 --end 4        # 指定页码区间
  python download_91porn.py --category top           # 其他分类(rf/top/hot/...)
  python download_91porn.py --out /path/to/dir       # 自定义输出目录
  python download_91porn.py --once 2                 # 只下前2个视频(试跑)

流程(单浏览器会话, 避免重复开浏览器):
  1. 逐页打开 v.php?category=<cat>&viewtype=basic&page=N, 滚动触发懒加载,
     提取每页视频卡片(viewkey)链接, 跨页去重; 已下载过的 viewkey 自动跳过
  2. 对每个视频: 同会话内打开详情页 → 复用 download_full_video.extract_from_page
     提取源(HLS m3u8 或 mp4 直链) → ffmpeg 带 cookie+Referer 下载
  3. 命名: 91porn_<viewkey>_<标题前30字>.mp4; 文件已存在则跳过(断点续传)
"""
import sys, os, json, re, asyncio, time, argparse
from pathlib import Path

os.environ.pop("NODE_OPTIONS", None)  # 沙箱 Playwright 需要

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

# 环境自举: 无需手动指定解释器/手动装依赖, 自动用项目 venv(video_search/.venv)运行
import env_check  # noqa: E402
env_check.ensure_playwright()
env_check.check_ffmpeg()
env_check.check_chrome()

import download_full_video as dfv  # noqa: E402

DEFAULT_OUT = Path("/Users/moonightice/Movies/短片")
LIST_TMPL = "https://91porn.com/v.php?category={cat}&viewtype=basic&page={page}"

VIEWKEY_RE = re.compile(r"[?&]viewkey=([a-z0-9]+)", re.I)

VERBOSE = False   # --verbose: 显示探测/过滤/并发等细节; 默认精简(目标/进度/完成失败)

# 广告过滤决策持久化文件(data/.91porn_filter.json): 已确认的选择下次运行直接沿用
FILTER_STATE_FILE = Path(__file__).resolve().parent.parent / "data" / ".91porn_filter.json"


def load_filter_decision():
    """读取持久化的广告过滤决策(None=未记录)。"""
    try:
        d = json.loads(FILTER_STATE_FILE.read_text())
        v = d.get("ad_filter")
        if v is True:
            return True
        if v is False:
            return False
    except Exception:
        pass
    return None


def save_filter_decision(v: bool):
    """持久化广告过滤决策。"""
    try:
        FILTER_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        FILTER_STATE_FILE.write_text(json.dumps({
            "ad_filter": v,
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }, ensure_ascii=False, indent=2))
    except Exception:
        pass


def maybe_filter_ads(cands, mode="ask"):
    """广告候选过滤: 决策优先级 显式参数(--filter yes/no) > 持久化记录 > 询问一次并保存。
    返回过滤后的候选列表。"""
    ads = [u for u in cands if dfv.is_ad_like(u)]
    if not ads:
        return cands
    decided = None
    if mode == "yes":
        decided = True
        save_filter_decision(True)      # 显式参数也持久化(记住最新选择)
    elif mode == "no":
        decided = False
        save_filter_decision(False)
    else:  # ask: 持久化决策优先, 未记录才询问并保存
        decided = load_filter_decision()
        if decided is None:
            doms = sorted({re.sub(r"^https?://([^/]+).*", r"\1", u) for u in ads})
            try:
                ans = input(f"[过滤] 检测到 {len(ads)} 个疑似广告候选({', '.join(doms[:3])}...), "
                            f"是否过滤? [Y/n]: ").strip().lower()
                decided = ans in ("", "y", "yes")
            except EOFError:
                decided = True
            save_filter_decision(decided)
    if decided:
        clean = [u for u in cands if not dfv.is_ad_like(u)]
        if clean:
            if VERBOSE:
                print(f"    [过滤] 剔除 {len(cands)-len(clean)} 个广告候选", flush=True)
        return clean or cands
    return cands


def sanitize(name: str) -> str:
    """文件名安全化: 替换路径非法字符。"""
    return re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", name or "").strip(" _")


def existing_viewkeys(out_dir: Path) -> set:
    """已下载且校验有效的 viewkey 集合, 用于断点续传跳过。

    纯 ffprobe 校验: 能探测到时长/分辨率 = 有效; 中断残留(探测无效)不算已下载, 重跑重下。
    """
    keys = set()
    if not out_dir.exists():
        return keys
    for f in sorted(out_dir.glob("91porn_*.mp4")):
        m = re.match(r"91porn_([a-z0-9]+)_", f.name, re.I)
        if not m:
            continue
        v = dfv.probe(str(f), "", "", timeout=10)
        if v.get("duration_sec") or v.get("resolution"):
            keys.add(m.group(1))
        elif VERBOSE:
            print(f"  [去重] 跳过残留(探测无效 {f.stat().st_size//1024//1024}MB): {f.name}, 将重新下载",
                  flush=True)
    return keys


async def wait_cf(page, max_wait=40):
    """等待 Cloudflare 挑战页通过(标题不再是 'Just a moment')。"""
    for _ in range(max_wait):
        try:
            t = await page.title()
            if t and "Just a moment" not in t and t.strip():
                return True
        except Exception:
            pass
        await page.wait_for_timeout(1000)
    return False


async def open_page(page, url):
    """打开页面并等待 CF 通过(失败不抛异常)。"""
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    except Exception:
        pass
    await wait_cf(page)
    await page.wait_for_timeout(2000)


async def collect_cards(page, list_url, seen, cards):
    """打开列表页, 收集 viewkey 详情链接。返回本页新增数量。"""
    await open_page(page, list_url)
    # 滚动触发懒加载
    try:
        for _ in range(5):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1200)
    except Exception:
        pass
    links = await page.eval_on_selector_all(
        "a[href*='viewkey'], a[href*='view_video']",
        "els => els.map(a => a.href)")
    added = 0
    for u in dict.fromkeys(links):
        m = VIEWKEY_RE.search(u)
        if not m:
            continue
        k = m.group(1).lower()
        if k not in seen:
            seen.add(k)
            cards.append(u)
            added += 1
    return added


async def process_video(page, ctx, video_url, out_dir, idx, total, filter_mode="ask",
                        max_retries=2, dl_retries=2, stall_timeout=60):
    """打开详情页 → 提取源 → 广告过滤 → 选最长 → 下载。返回 (status, viewkey, info)。
    status: ok / skip / failed; info 为标题或错误原因。"""
    m = VIEWKEY_RE.search(video_url)
    viewkey = m.group(1).lower() if m else "unknown"
    if total:
        print(f"  [{idx}/{total}] {video_url}", flush=True)
    else:
        print(f"  [{idx}] {video_url}", flush=True)   # 逐页模式: 总数未知

    for attempt in range(1, max_retries + 1):
        await open_page(page, video_url)
        # 容错: 点击可能的年龄确认/进入按钮(91porn 老式弹窗)
        try:
            await page.evaluate("""() => {
                document.querySelectorAll('a,button').forEach(el => {
                    const t = (el.textContent||'').trim().toLowerCase();
                    if (t.length < 20 && /confirm|enter|agree|进入|确认/i.test(t)) el.click();
                });
            }""")
            await page.wait_for_timeout(1500)
        except Exception:
            pass
        src = await dfv.extract_from_page(page, ctx, video_url)
        if not src.get("error"):
            break
        if attempt < max_retries:
            print(f"    提取失败({src['error']}), 重试 {attempt+1}/{max_retries}", flush=True)
            await page.wait_for_timeout(3000)
    if src.get("error"):
        return "failed", viewkey, src["error"]

    title = src.get("title") or viewkey
    name = f"91porn_{viewkey}_{sanitize(title)[:30]}.mp4"
    out = out_dir / name
    if out.exists() and out.stat().st_size > 0:
        # 二次防御: 已存在但校验无效(中断残留) → 删除重下
        v = dfv.probe(str(out), "", "", timeout=10)
        if v.get("duration_sec") or v.get("resolution"):
            return "skip", viewkey, f"已存在({out.name})"
        print(f"    ⚠ 已存在但校验无效({out.stat().st_size//1024//1024}MB, 疑似中断残留), 删除重下",
              flush=True)
        try:
            out.unlink(missing_ok=True)
        except Exception:
            pass

    if src["source_type"] == "hls":
        info = src.get("info") or {}
        if not info.get("ok"):
            return "failed", viewkey, "master 解析失败"
        url = info["best"]["url"]
        meta = dfv.probe(url, src["referer"], src["cookie"], timeout=15)
    else:
        # mp4 直链: 先广告过滤(可交互), 再候选逐个探测选最长(排除短预告), 最后下载
        cands = src.get("candidates") or [src["direct_url"]]
        cands = maybe_filter_ads(cands, filter_mode)
        url, meta = dfv.pick_best_mp4(cands, src["referer"], src["cookie"], verbose=VERBOSE)
        if not url:
            return "failed", viewkey, "候选探测失败"
    if VERBOSE:
        print(f"    源: {src['source_type']} | 时长 {meta.get('duration_sec','?')}s "
              f"| 分辨率 {meta.get('resolution','?')}", flush=True)
    # 实时下载进度(原地刷新, 不刷屏): 时长已知按时间%, 时长未知但有总大小按MB%,
    # 都未知按已写入大小
    total_dur = meta.get("duration_sec") or 0
    def _prog(pct, cur, total, size_mb=None, kind=None):
        if kind == "size":
            print(f"\r    下载中 [{pct:5.1f}%] {cur:.1f}MB/{total:.1f}MB ({max(total-cur,0):.1f}MB 剩余)",
                  end="", flush=True)
        elif pct >= 0:
            sz = f" · {size_mb:.1f}MB" if size_mb is not None else ""
            print(f"\r    下载中 [{pct:5.1f}%] {cur:.0f}s/{total:.0f}s ({max(total-cur,0):.0f}s 剩余){sz}",
                  end="", flush=True)
        else:
            sz = f" · {size_mb:.1f}MB" if size_mb is not None else ""
            print(f"\r    下载中 (时长未知){sz}", end="", flush=True)
    # 下载: mp4 直链优先多连接并发(对标 Downie 提速), 不可用/失败回退 ffmpeg 单连接重试
    ok = False
    if src["source_type"] == "mp4":
        ok = dfv.download_range_parallel(url, src["referer"], src["cookie"], out,
                                         progress=_prog, verbose=VERBOSE)
        if not ok:
            if VERBOSE:
                print("    [回退] 多连接不可用/失败, ffmpeg 单连接(带重试)", flush=True)
            try:
                out.unlink(missing_ok=True)
            except Exception:
                pass
    if not ok:
        for dl_attempt in range(1, dl_retries + 2):
            ok = dfv.download(url, src["referer"], src["cookie"], out,
                              progress=_prog, total_dur=total_dur,
                              stall_timeout=stall_timeout, verbose=VERBOSE)
            if ok:
                break
            if dl_attempt <= dl_retries:
                print(f"\n    ⚠ 下载失败, 重试 {dl_attempt}/{dl_retries} (3s 后, 清残留)...", flush=True)
                time.sleep(3)
                try:
                    out.unlink(missing_ok=True)
                except Exception:
                    pass
    if ok:
        print()  # 收尾进度行(换行)
        # 下载后本地校验: 回退下载场景时长未知, 确认不是广告/预览
        vmeta = dfv.probe(str(out), "", "", timeout=15)
        vdur = vmeta.get("duration_sec") or 0
        if VERBOSE and total_dur == 0 and 0 < vdur < 20:
            print(f"    ⚠ 下载后校验: 时长仅 {vdur}s, 疑似广告/预览!", flush=True)
        size_mb = round(out.stat().st_size / 1e6, 1)
        print(f"    ✓ 完成: {out.name} ({size_mb}MB"
              + (f", 校验时长 {vdur}s" if vdur and VERBOSE else "") + ")", flush=True)
        return "ok", viewkey, size_mb
    print()  # 失败也换行收尾
    return "failed", viewkey, "ffmpeg 下载失败"


def main():
    ap = argparse.ArgumentParser(description="批量下载 91porn 分类列表页视频")
    ap.add_argument("--category", default="rf",
                    help="分类, 逗号分隔支持多值: top,rf,hot 等; 如 --category top,rf,hot (默认 rf)")
    ap.add_argument("--start", type=int, default=1, help="起始页码, 默认 1")
    ap.add_argument("--end", type=int, default=5, help="结束页码, 默认 5")
    ap.add_argument("--pages", type=int, default=None, help="从第1页到第N页(覆盖 --end)")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="输出目录")
    ap.add_argument("--once", type=int, default=0,
                    help="只收集并下载前N个视频(试跑, 提前停止翻页; 如 --once 2 = 第一页前2个)")
    ap.add_argument("--per-cat", type=int, default=0,
                    help="每个分类最多收集N个(测试用; 如 --category top,rf,hot --per-cat 2 = 每分类2个共6个)")
    ap.add_argument("--filter", choices=("ask", "yes", "no"), default="ask",
                    help="广告候选过滤: ask=遇到时询问一次并记住(默认) / yes=直接过滤 / no=不过滤")
    ap.add_argument("--dl-retries", type=int, default=2, help="单个视频下载失败重试次数(默认 2)")
    ap.add_argument("--stall-timeout", type=int, default=60,
                    help="下载停滞判定秒数: 输出大小 N 秒无增长即杀进程重试(默认 60)")
    ap.add_argument("--by-page", action="store_true",
                    help="逐页模式: 收集一页立即下载该页, 再翻下一页(默认两阶段: 先全量收集再下载)")
    ap.add_argument("--verbose", action="store_true",
                    help="显示探测/过滤/并发等细节(默认精简: 只保留目标/进度/完成失败)")
    args = ap.parse_args()

    global VERBOSE
    VERBOSE = args.verbose

    cats = [c.strip() for c in args.category.split(",") if c.strip()]
    if not cats:
        print("!! --category 为空"); return
    end = args.pages if args.pages else args.end
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    done_keys = existing_viewkeys(out_dir)
    print(f"=== 91porn 批量下载: category={cats} 页 {args.start}-{end} "
          f"→ {out_dir} (已下载 {len(done_keys)} 个将跳过) ===", flush=True)

    async def run():
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
            )
            ctx = await browser.new_context(
                user_agent=dfv.UA, viewport={"width": 1280, "height": 900})
            page = await ctx.new_page()

            seen = set(done_keys)
            results = {"ok": [], "skip": [], "failed": []}
            if args.by_page:
                # ---- 逐页模式: 收集一页 → 立即下载该页 → 翻下一页 ----
                downloaded = 0
                for cat in cats:
                    cat_dl = 0
                    for n in range(args.start, end + 1):
                        list_url = LIST_TMPL.format(cat=cat, page=n)
                        print(f"[列表] {cat} 第{n}页: {list_url}", flush=True)
                        page_cards = []
                        added = await collect_cards(page, list_url, seen, page_cards)
                        print(f"  → {cat} 第{n}页 新增 {added} 个", flush=True)
                        for i, vurl in enumerate(page_cards, 1):
                            status, key, info = await process_video(
                                page, ctx, vurl, out_dir, downloaded + i, 0,
                                filter_mode=args.filter, dl_retries=args.dl_retries,
                                stall_timeout=args.stall_timeout)
                            results[status].append((key, info))
                            if status == "failed":
                                print(f"    ✗ 失败: {info}", flush=True)
                            downloaded += 1
                            cat_dl += 1
                            if args.once > 0 and downloaded >= args.once:
                                print(f"  (--once {args.once}: 已达上限, 停止)", flush=True)
                                break
                            if args.per_cat > 0 and cat_dl >= args.per_cat:
                                print(f"  (--per-category {args.per_cat}: {cat} 已够, 停止本分类)", flush=True)
                                break
                        print(f"  [页完成] {cat} 第{n}页: 累计 成功 {len(results['ok'])} "
                              f"/ 跳过 {len(results['skip'])} / 失败 {len(results['failed'])}", flush=True)
                        if args.once > 0 and downloaded >= args.once:
                            break
                        if args.per_cat > 0 and cat_dl >= args.per_cat:
                            break
                    if args.once > 0 and downloaded >= args.once:
                        break
                if not any(results.values()):
                    print("!! 未处理任何视频(可能风控或页面结构变化)")
            else:
                # ---- 两阶段模式: 先全量收集, 再统一下载 ----
                cards = []
                for cat in cats:
                    cat_cards = []   # 本分类新增(去重后), 用于 --per-category 每分类限额
                    for n in range(args.start, end + 1):
                        list_url = LIST_TMPL.format(cat=cat, page=n)
                        print(f"[列表] {cat} 第{n}页: {list_url}", flush=True)
                        added = await collect_cards(page, list_url, seen, cat_cards)
                        print(f"  → {cat} 新增 {added} 个 (累计 {len(cards)})", flush=True)
                        if args.per_cat > 0 and len(cat_cards) >= args.per_cat:
                            print(f"  (--per-category {args.per_cat}: {cat} 已收集够, 进入下一分类)", flush=True)
                            break
                        if args.once > 0 and len(cards) + len(cat_cards) >= args.once:
                            break
                    if args.per_cat > 0:
                        cat_cards = cat_cards[:args.per_cat]
                    cards.extend(cat_cards)
                    if args.once > 0 and len(cards) >= args.once:
                        cards = cards[:args.once]
                        print(f"  (--once {args.once}: 已收集足够, 停止)", flush=True)
                        break
                if not cards:
                    print("!! 列表页未提取到任何视频链接(可能风控或页面结构变化)")
                    await browser.close()
                    return
                print(f"\n[下载] 共 {len(cards)} 个视频", flush=True)
                for i, vurl in enumerate(cards, 1):
                    status, key, info = await process_video(
                        page, ctx, vurl, out_dir, i, len(cards),
                        filter_mode=args.filter, dl_retries=args.dl_retries,
                        stall_timeout=args.stall_timeout)
                    results[status].append((key, info))
                    if status == "failed":
                        print(f"    ✗ 失败: {info}", flush=True)

            await browser.close()

            # ---- 汇总 ----
            print(f"\n=== 汇总: 成功 {len(results['ok'])} / 跳过 {len(results['skip'])} "
                  f"/ 失败 {len(results['failed'])} ===", flush=True)
            if results["failed"]:
                print("失败列表:")
                for k, e in results["failed"]:
                    print(f"  {k}: {e}", flush=True)
            # 简单记录一份本次运行 JSON(供后续排查)
            run_log = {
                "categories": cats, "pages": [args.start, end],
                "out_dir": str(out_dir), "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "results": results,
            }
            (out_dir / ".91porn_run.json").write_text(
                json.dumps(run_log, ensure_ascii=False, indent=2))

    asyncio.run(run())


if __name__ == "__main__":
    main()
