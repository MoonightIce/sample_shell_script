#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
search_exact.py — 番号精准匹配搜索 + 下载。

核心: 匹配对象是【番号字段】而非标题, 避免"标题包含"式模糊命中。
策略(用户确认):
  1) URL直连 + 搜索页番号字段比对 两层兼用
  2) 大小写不敏感(匹配前统一规范化: 去非字母数字+小写)
  3) 无精确结果时给出相似度最高的候选

用法:
  python search_exact.py <番号> [--download] [--out PATH] [--seg 120]
  python search_exact.py SKMJ-774            # 仅搜索匹配
  python search_exact.py skmj774 --download  # 匹配后触发分段下载
"""
import sys, re, json, asyncio, argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# 环境自举: 自动使用项目 venv(video_search/.venv)运行, 无需手动指定解释器/装依赖
import env_check  # noqa: E402
env_check.ensure_playwright()

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36"
BASE = Path(__file__).resolve().parent

def normalize(idn: str) -> str:
    """番号规范化: 保留【大写字母+数字】, 去掉空格/连字符等分隔符。
    SKMJ-774 / skmj-774 / SKMJ 774 → SKMJ774 (统一为大写字母+数字)"""
    return re.sub(r"[^A-Za-z0-9]", "", (idn or "").upper())

def similarity(a: str, b: str) -> float:
    """基于规范化字符串的相似度(Levenshtein 编辑距离比)。"""
    a, b = normalize(a), normalize(b)
    if not a or not b: return 0.0
    if a == b: return 1.0
    if len(a) > len(b): a, b = b, a
    prev = list(range(len(b)+1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0]*len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(cur[j-1]+1, prev[j]+1, prev[j-1]+(ca != cb))
        prev = cur
    return 1.0 - prev[-1] / max(len(a), len(b))

def extract_id_from_meta(title: str, url: str):
    """从标题/URL 提取番号(多种形态)。返回规范化后的番号或 None。"""
    # URL 路径: 兼容 /skmj-774、/dm26/fset-429、/mvsd696-chinese-subtitle(中文字幕版) 形态
    m = re.search(r"missav\.ws/(?:dm\d+/)?([A-Za-z0-9][A-Za-z0-9-]*\d+)(?:-chinese-subtitle)?(?:#|/|$)", url)
    if m:
        return normalize(m.group(1))
    # 标题开头的番号(如 「SKMJ-774」... 或 SKMJ-774 空格...)
    m = re.search(r"「?([A-Za-z]{2,}[A-Za-z0-9-]*[0-9]{2,})」?", title)
    if m:
        return normalize(m.group(1))
    return None

async def fetch_m3u8_and_matches(page_url):
    """打开详情页, 返回 (标题, 番号, 视频源URL)。供第一层URL直连与第二层比对用。"""
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.launch(headless=True,
            executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            args=["--no-sandbox","--disable-blink-features=AutomationControlled"])
        c = await b.new_context(user_agent=UA, viewport={"width":1280,"height":900})
        pg = await c.new_page()
        media = []
        pg.on("request", lambda r: media.append(r.url) if ".m3u8" in r.url else None)
        status = None
        try:
            resp = await pg.goto(page_url, wait_until="domcontentloaded", timeout=45000)
            status = resp.status if resp else None
        except Exception:
            pass
        title = ""
        for _ in range(40):
            try:
                t = await pg.title()
                if t and "Just a moment" not in t and t.strip():
                    title = t; break
            except Exception:
                pass
            await pg.wait_for_timeout(1000)
        try:
            await pg.wait_for_timeout(3000)
        except Exception:
            pass
        try:
            await pg.evaluate("""()=>{const v=document.querySelector('video');if(v){v.muted=true;v.play().catch(()=>{});}}""")
            await pg.wait_for_timeout(4000)
        except Exception: pass
        try:
            title = title or await pg.title()
        except Exception:
            title = title or ""
        # 抓取页面 h1/可能含番号的文本
        texts = await pg.eval_on_selector_all("h1, h2, .text-sm, [class*=text]",
            "els=>els.map(e=>e.textContent.trim()).filter(t=>t&&t.length<60)")
        m3u8s = [u for u in dict.fromkeys(media) if ".m3u8" in u]
        await b.close()
        return {"status": status, "title": title, "texts": texts,
                "url": page_url, "m3u8s": m3u8s, "final_url": page_url}

def layer1_direct(query_norm, result):
    """第一层: URL直连后, 提取页面番号与查询精确比对。"""
    cand = extract_id_from_meta(result.get("title",""), result.get("url",""))
    if cand and cand == query_norm:
        return True, cand
    return False, cand

def url_id(idn: str) -> str:
    """URL 用的小写形式: SKMJ-774 → skmj774 (missav URL 全小写)"""
    return re.sub(r"[^a-z0-9]", "", (idn or "").lower())

def run_layer1(query_norm, info):
    """尝试直连 missav.ws/<番号> 与 missav.ws/<番号>-chinese-subtitle(中文字幕版),
    返回 (命中与否, 详情)。中文字幕版优先, 未命中再试标准形态。"""
    uid = url_id(query_norm)
    candidates = [f"https://missav.ws/{uid}-chinese-subtitle",   # 字幕版优先
                  f"https://missav.ws/{uid}"]                    # 标准形态兜底
    for url in candidates:
        r = asyncio.run(fetch_m3u8_and_matches(url))
        if not r["status"] or r["status"] != 200:
            continue  # 该形态未返回200(风控/不存在), 尝试下一种
        hit, cand = layer1_direct(query_norm, r)
        if hit:
            return True, {**r, "candidate_id": cand}
        # 页面200但番号不匹配(可能是风控页/其他内容), 继续尝试
    return False, {"url": candidates[0], "reason": "直连未命中(可能风控/不存在)",
                   "tried": candidates}

def run_layer2(query_norm):
    """第二层: 搜索页 /search/<番号>, 提取每条番号字段精确比对。返回候选列表。"""
    from playwright.async_api import async_playwright
    async def _do():
        async with async_playwright() as p:
            b = await p.chromium.launch(headless=True,
                executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                args=["--no-sandbox","--disable-blink-features=AutomationControlled"])
            c = await b.new_context(user_agent=UA, viewport={"width":1280,"height":900})
            pg = await c.new_page()
            url = f"https://missav.ws/search/{url_id(query_norm)}"
            try:
                await pg.goto(url, wait_until="domcontentloaded", timeout=45000)
            except Exception: pass
            for _ in range(40):
                try:
                    t = await pg.title()
                    if t and "Just a moment" not in t: break
                except Exception: pass
                await pg.wait_for_timeout(1000)
            await pg.wait_for_timeout(4000)
            # 多次滚动触发懒加载(搜索结果分页懒加载)
            try:
                for _ in range(5):
                    await pg.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await pg.wait_for_timeout(1500)
            except Exception: pass
            # 抓取结果卡片: 用全部 a, JS里用 a.href(解析后绝对URL)过滤 missav.ws,
            # 文本含番号模式(字母+数字)。注意: 选择器不能限 [href*=missav.ws],
            # 因为结果卡片href是相对路径(/dm26/fset-429), 属性值不含域名!
            cards = await pg.eval_on_selector_all(
                "a",
                """els=>els.map(a=>({
                    href:a.href,
                    title:(a.getAttribute('title')||a.textContent||'').trim().slice(0,100)
                })).filter(x=>x.href.includes('missav.ws')
                    && /[A-Za-z]{2,}[A-Za-z0-9-]*[0-9]{2,}/.test(x.title)
                    && !/search|actress|genre|maker|vip|flag|\\/cn\\/|\\/en\\/|\\/ja\\/|\\/ko\\/|\\/ms\\/|\\/th\\/|\\/de\\/|\\/fr\\/|\\/vi\\//.test(x.href))""")
            seen = {}
            for x in cards:
                if x["href"] not in seen: seen[x["href"]] = x
            await b.close()
            return list(seen.values())
    try:
        return asyncio.run(_do())
    except Exception as e:
        return []

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("number", help="番号, 如 SKMJ-774 / skmj774")
    ap.add_argument("--download", action="store_true", help="命中后触发分段下载")
    ap.add_argument("--enqueue", action="store_true", help="命中后加入待下载队列(推荐, 与下载调度器配合)")
    ap.add_argument("--out", type=str, default=None, help="下载输出路径")
    ap.add_argument("--seg", type=int, default=120, help="分段下载每段秒数")
    args = ap.parse_args()

    query = args.number
    qn = normalize(query)
    print(f"番号: {query} → 规范化: {qn}")

    # ---- 第一层: URL直连 ----
    print("\n[第一层] URL直连 missav.ws/{qn}")
    hit1, info1 = run_layer1(qn, None)
    if hit1:
        print(f"  ✓ 精确命中: URL {info1['url']}, 标题: {info1['title'][:50]}")
        print(f"    视频源: {info1['m3u8s'][0] if info1['m3u8s'] else 'N/A'}")
        result_url = info1["url"]
    else:
        print(f"  直连未命中: {info1.get('reason','')} candidate_id={info1.get('candidate_id')}")

        # ---- 第二层: 搜索页 ----
        print("\n[第二层] 搜索页 /search/{qn} 番号字段精确比对")
        cards = run_layer2(qn)
        cands = []
        for c in cards:
            cid = extract_id_from_meta(c.get("title",""), c.get("href",""))
            if cid:
                cands.append({**c, "id": cid, "sim": similarity(qn, cid)})
        exact = [c for c in cands if c["id"] == qn]
        if exact:
            print(f"  ✓ 精确命中(搜索页番号字段): {exact[0]['href']}")
            result_url = exact[0]["href"]
        else:
            print("  无精确番号命中。相似候选:")
            for c in sorted(cands, key=lambda x:-x["sim"])[:5]:
                print(f"    {c['id']:15} sim={c['sim']:.2f}  {c['href']}")
            if not cands:
                print("    (搜索页未提取到任何番号字段)")
            print("\n[结果] 未找到精确匹配, 请核对番号或从候选中选择")
            return

    # ---- 下载触发 / 入队 ----
    if args.enqueue:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import download_queue as dq
        vid, is_new = dq.add_task(result_url, info1.get("title", "")[:80] if hit1 else "")
        if is_new:
            print(f"\n[入队] 已加入待下载队列: {vid}")
        else:
            print(f"\n[入队] {vid} 已在队列中(跳过重复)")
        print(f"运行 download_queue.py 自动下载队列中的任务")
    elif args.download:
        out = Path(args.out) if args.out else Path.cwd()/f"{normalize(result_url.split('/')[-1])}_full.mp4"
        print(f"\n[下载] 触发分段下载 -> {out}")
        import subprocess
        r = subprocess.run([sys.executable, str(BASE/"download_segmented.py"),
                            result_url, str(out), "--seg", str(args.seg)],
                           capture_output=False, timeout=3600)
        print(f"下载完成 exit={r.returncode}")
    else:
        print(f"\n[完成] 命中地址: {result_url}")
        print("加 --enqueue 可加入待下载队列, 或 --download 直接下载")

if __name__ == "__main__":
    main()
