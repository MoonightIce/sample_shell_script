#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
prefetch_via_cdp.py — 通过 CDP 在真实 Chrome 页面上下文批量富化榜单
====================================================================
为什么需要它：1music.cc search API 强制 Turnstile token 且与浏览器会话 cookie 绑定，
curl/独立 http client 即使带 token 也返回空数组。唯一可靠路径 = 在已通过验证的
1music.cc 页面上下文里 fetch 搜索（自动携带 cookie + token）。

依赖：本机已用信任 profile 启动 Chrome 调试实例：
  "/Applications/Google Chrome.app/.../Google Chrome" --remote-debugging-port=9222 \
    --remote-allow-origins=* --user-data-dir=/tmp/chrome_user_profile
（profile 需含访问过 1music.cc 的历史，Turnstile 才会自动放行）

用法：
  python3 prefetch_via_cdp.py --charts data/charts/cantopop_charts_2026-09-02.json
输出：<charts 前名>.prefetched.json（song_hash/exp/videoId 已写入，chart_download.py 可直接消费）
"""
import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import websocket

CDP = "http://localhost:9222"
INTERVAL = 1.2          # 每首间隔，防限流
TOKEN_CHECK = 30        # 每 N 首检查一次 token 是否仍有效


def _http_json(url, method="GET"):
    req = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def new_tab(url):
    return _http_json(f"{CDP}/json/new?{urllib.parse.quote(url, safe='')}", "PUT")


def list_tabs():
    return _http_json(f"{CDP}/json")


class CdpPage:
    def __init__(self, ws_url):
        self.ws_url = ws_url
        self._id = 0

    def evaluate(self, expr, await_promise=True):
        ws = websocket.create_connection(self.ws_url, timeout=120)
        self._id += 1
        ws.send(json.dumps({"id": self._id, "method": "Runtime.evaluate",
                            "params": {"expression": expr, "returnByValue": True,
                                       "awaitPromise": await_promise}}))
        try:
            while True:
                msg = json.loads(ws.recv())
                if msg.get("id") == self._id:
                    res = msg.get("result", {}).get("result", {})
                    if res.get("exceptionDetails"):
                        return {"__err__": str(res["exceptionDetails"].get("text", ""))[:200]}
                    return res.get("value")
        finally:
            ws.close()

    def token(self):
        v = self.evaluate("(window.turnstile && window.turnstile.getResponse) ? window.turnstile.getResponse() : null", False)
        return v if v and len(str(v)) > 20 else None

    def reload_and_wait(self, max_wait=40):
        self.evaluate("location.reload(); true", False)
        deadline = time.time() + max_wait
        while time.time() < deadline:
            time.sleep(3)
            if self.token():
                print("  [token] 页面重新验证通过")
                return True
        return False


def search_in_page(page: CdpPage, query: str):
    """在页面上下文 fetch 搜索，返回结果 list（[] 表示无结果）"""
    expr = f"""(async () => {{
      try {{
        const tok = (window.turnstile && window.turnstile.getResponse) ? window.turnstile.getResponse() : '';
        const r = await fetch('https://api.1music.cc/search?songs=' + encodeURIComponent({json.dumps(query)}) + '&token=' + encodeURIComponent(tok || ''), {{headers: {{'Accept': 'application/json'}}}});
        if (!r.ok) return {{__status__: r.status}};
        const d = await r.json();
        return Array.isArray(d) ? d : {{__bad__: String(d).slice(0,120)}};
      }} catch (e) {{ return {{__err__: String(e).slice(0,200)}}; }}
    }})()"""
    return page.evaluate(expr)


def pick_best(results, artist, title):
    """从结果中挑最佳：artist 优先（宽容包含/首词），其次 title 精确"""
    if not isinstance(results, list) or not results:
        return None
    an, tn = (artist or "").lower(), (title or "").lower()

    def score(r):
        ra, rt = (r.get("artist") or "").lower(), (r.get("title") or "").lower()
        s = 0
        if an and (ra == an or ra.startswith(an) or an.startswith(ra) or an in ra or ra in an):
            s += 4          # artist 命中（宽容繁简/连写差异）
        if tn and (rt == tn):
            s += 3
        elif tn and (tn in rt or rt in tn):
            s += 1
        return s

    best = max(results, key=score)
    return best if score(best) > 0 else None


def main():
    ap = argparse.ArgumentParser(description="CDP 页面上下文批量富化榜单 hash")
    ap.add_argument("--charts", required=True, help="榜单 JSON")
    ap.add_argument("--out", default="", help="输出 JSON（默认 <charts 前名>.prefetched.json）")
    args = ap.parse_args()

    src = Path(args.charts)
    songs = json.loads(src.read_text(encoding="utf-8"))
    out_path = Path(args.out) if args.out else src.with_name(src.stem + ".prefetched.json")
    if not songs:
        print("[!] 榜单为空")
        sys.exit(1)

    # 定位/创建页面
    tab = next((t for t in list_tabs() if "1music" in t.get("url", "")), None)
    if not tab:
        print("[1] 打开 1music.cc ...")
        tab = new_tab("https://1music.cc/zh-CN")
        time.sleep(8)
    page = CdpPage(tab["webSocketDebuggerUrl"])
    print(f"[1] 页面: {tab.get('url', '')[:60]}")

    if not page.token():
        print("[2] 等待 Turnstile ...")
        if not page.reload_and_wait():
            print("[!] Turnstile 未能通过（profile 信任失效？）")
            sys.exit(2)
    print("[2] Turnstile 通过")

    already = sum(1 for s in songs if s.get("song_hash") and s.get("exp") and s.get("videoId"))
    ok = fail = 0
    fails = []
    t0 = time.time()
    print(f"[3] 开始富化 {len(songs)} 首（已含 {already}）...")

    for i, s in enumerate(songs, 1):
        title, artist = s.get("title", ""), s.get("artist", "")
        if s.get("song_hash") and s.get("exp") and s.get("videoId"):
            ok += 1
            continue
        if i % TOKEN_CHECK == 0 and not page.token():
            print(f"  [token] 失效，重新验证页面 ...")
            page.reload_and_wait()

        best = None
        for q in ([title, f"{title} {artist}"][:2] if artist else [title]):
            results = search_in_page(page, q)
            if isinstance(results, dict):
                if results.get("__status__") == 403 or (results.get("__err__") and "token" in str(results.get("__err__", "")).lower()):
                    print(f"  [{i}] 接口拒绝，重新验证页面 ...")
                    page.reload_and_wait()
                    results = search_in_page(page, q)
                else:
                    print(f"  [{i}] 异常响应: {json.dumps(results)[:120]}")
                    continue
            best = pick_best(results, artist, title)
            if best:
                break
            time.sleep(0.5)

        if best and best.get("song_hash") and best.get("exp") and best.get("videoId"):
            for k in ("song_hash", "videoId", "exp"):
                s[k] = best[k]
            if not s.get("album") and best.get("album"):
                s["album"] = best["album"]
            ok += 1
        else:
            print(f"  [{i}] 无匹配: {title} — {artist}")
            fail += 1
            fails.append({"rank": s.get("rank"), "title": title, "artist": artist})
        time.sleep(INTERVAL)

        if i % 10 == 0:
            print(f"  ... 进度 {i}/{len(songs)} 成功 {ok} 失败 {fail} ({time.time()-t0:.0f}s)")
            Path(out_path).write_text(json.dumps(songs, ensure_ascii=False, indent=2), encoding="utf-8")  # 中途落盘防丢失

    Path(out_path).write_text(json.dumps(songs, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n完成：富化 {ok}/{len(songs)} 首，失败 {fail}，耗时 {time.time()-t0:.0f}s -> {out_path}")
    if fails:
        print("失败清单：")
        for f in fails:
            print(f"  #{f['rank']} {f['title']} — {f['artist']}")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
