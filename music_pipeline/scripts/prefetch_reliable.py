#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""可靠版低速富化：
- 开头强制 reload 拿全新 token（旧 token 判断不可靠，getResponse 不 reload 不刷新）
- 22s 间隔规避软限流（~20s 内 3 发即被 200+[] 打空，非 400）
- 200+[] 自动区分 token 过期 vs 软限流：reload 续 token 重试 → 仍空则退避 30s 重试 → 再空才判 MISS
- 每 8 首强制 reload 一次，防 token 5min 生命期内静默过期
- 繁简归一匹配（opencc t2s）
"""
import argparse
import json
import sys
import time
import urllib.request
import websocket

CDP = "http://localhost:9222"
INTERVAL = 22.0
BACKOFF = 30.0

try:
    import opencc
    _CC = opencc.OpenCC("t2s")
except Exception:
    _CC = None


def _norm(s):
    s = (s or "").lower().strip()
    return _CC.convert(s) if _CC else s


def _http_json(url):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


class CdpPage:
    def __init__(self, ws_url):
        self.ws_url = ws_url
        self._id = 0

    def evaluate(self, expr, await_promise=True):
        """CDP Runtime.evaluate。页面 reload 后 Chrome 事件流灌入时 recv 永不超时、
        响应 id 永不到达 → 死等。这里加硬截止（90s）+ 建连超时（15s），异常一律
        返回 {"__err__": ...}，由调用方 rebind/reload 恢复，绝不裸崩、绝不无限等。
        """
        try:
            ws = websocket.create_connection(self.ws_url, timeout=15)
        except Exception as e:
            return {"__err__": f"connect: {str(e)[:100]}"}
        self._id += 1
        try:
            ws.send(json.dumps({"id": self._id, "method": "Runtime.evaluate",
                                "params": {"expression": expr, "returnByValue": True,
                                           "awaitPromise": await_promise}}))
            t0 = time.time()
            while time.time() - t0 < 90:
                try:
                    msg = json.loads(ws.recv())
                except Exception as e:
                    return {"__err__": f"recv: {str(e)[:100]}"}
                if msg.get("id") == self._id:
                    res = msg.get("result", {}).get("result", {})
                    if res.get("exceptionDetails"):
                        return {"__err__": str(res["exceptionDetails"].get("text", ""))[:200]}
                    return res.get("value")
            return {"__err__": "cdp-timeout-no-reply"}
        finally:
            try:
                ws.close()
            except Exception:
                pass

    def rebind(self):
        """CDP 会话僵死（reload 竞态）后，从 /json 重取当前 1music 页面 ws url"""
        try:
            tabs = _http_json(f"{CDP}/json")
            tab = next((t for t in tabs if "1music" in t.get("url", "") and t.get("type") == "page"), None)
            if not tab:
                return False
            self.ws_url = tab["webSocketDebuggerUrl"]
            return True
        except Exception:
            return False

    def token(self):
        v = self.evaluate("(window.turnstile && window.turnstile.getResponse) ? window.turnstile.getResponse() : null", False)
        if isinstance(v, dict) and "__err__" in v:
            return None
        return v if v and len(str(v)) > 20 else None

    def reload_wait_token(self, max_wait=50):
        print("    [token] reload...", flush=True)
        self.evaluate("location.reload(); true", False)
        t0 = time.time()
        while time.time() - t0 < max_wait:
            time.sleep(3)
            if self.token():
                print("    [token] 就绪", flush=True)
                return True
        print(f"    [token] {max_wait:.0f}s 未就绪", flush=True)
        return False


def search_in_page(page, query):
    expr = f"""(async () => {{
      try {{
        const tok = (window.turnstile && window.turnstile.getResponse) ? window.turnstile.getResponse() : '';
        const r = await fetch('https://api.1music.cc/search?songs=' + encodeURIComponent({json.dumps(query)}) + '&token=' + encodeURIComponent(tok || ''), {{headers: {{'Accept': 'application/json'}}}});
        return {{st: r.status, d: await r.json().catch(()=>null)}};
      }} catch (e) {{ return {{st: -1, err: String(e).slice(0,120)}}; }}
    }})()"""
    return page.evaluate(expr)


# 别名表：库内 artist 可能是英文名，归一后仍需映射回原歌手
_ARTIST_ALIASES = {
    "陈奕迅": ["eason chan", "eason"],
    "莫文蔚": ["karen mok"],
    "张敬轩": ["hins"],
}


def artist_match(an, ra):
    """原歌手名 an（可中英混合如 'Ian 陈卓贤'）与库内 artist ra 是否同一人"""
    if not an or not ra:
        return False
    if ra == an or an.startswith(ra) or ra.startswith(an) or an in ra or ra in an:
        return True
    for alias in _ARTIST_ALIASES.get(an, []):
        if ra == alias or ra in alias or alias in ra:
            return True
    return False


def pick_best(results, artist, title):
    """原版硬约束：仅接受 artist 匹配原歌手的条目，且 title 必须相关（全等=3 / 包含=1）。
    title 完全无关（0 分，如同名歌手另一首歌）→ 不收。无合格项返回 None——不收翻唱、不误收。
    """
    if not isinstance(results, list) or not results:
        return None
    an, tn = _norm(artist), _norm(title)
    pool = []
    for r in results:
        ra, rt = _norm(r.get("artist")), _norm(r.get("title"))
        if not artist_match(an, ra):
            continue
        s = 0
        if tn and rt == tn:
            s = 3
        elif tn and rt and (tn in rt or rt in tn):
            s = 1
        if s == 0:
            continue  # title 与榜单完全无关 → 同名歌手其他歌，误收来源，剔除
        pool.append((s, r))
    if not pool:
        return None
    pool.sort(key=lambda x: -x[0])
    return pool[0][1]


def robust_search(page, query, artist, title):
    """带 token 续期与限流退避的搜索；返回 (best_or_None, detail)
    2026-09-02 加固：
    - evaluate 返回 __err__（CDP reload 竞态死等已由 deadline 兜住）→ rebind+reload 后重试
    - 软限流只在首次空结果时 reload 一次做分类；判定软限流后纯退避 45s 重试，
      不再反复 reload（reload 对 IP/会话级限流无效，反而引爆 CDP 竞态）
    """
    soft = False
    for attempt in range(3):
        r = search_in_page(page, query)
        if isinstance(r, dict) and r.get("__err__"):
            print(f"      CDP 异常({r['__err__'][:70]}) → rebind + reload", flush=True)
            ok = page.rebind()
            print(f"      rebind={'ok' if ok else 'fail'}", flush=True)
            if ok:
                page.reload_wait_token()
            soft = False
            time.sleep(3)
            continue
        st = r.get("st") if isinstance(r, dict) else "?"
        d = r.get("d") if isinstance(r, dict) else r
        if st == 200 and isinstance(d, list) and len(d) > 0:
            return pick_best(d, artist, title), "hit"
        if st == 400:
            print(f"      400 → reload 续 token + 退避 {BACKOFF:.0f}s", flush=True)
            page.reload_wait_token()
            time.sleep(BACKOFF)
            soft = False
            continue
        if st == 200 and isinstance(d, list) and len(d) == 0:
            if not soft:
                soft = True  # 首次空：reload 续 token 重探一次分类 token 过期 vs 软限流
                page.reload_wait_token()
                r2 = search_in_page(page, query)
                if isinstance(r2, dict) and r2.get("__err__"):
                    print(f"      probe CDP 异常({r2['__err__'][:60]})", flush=True)
                    time.sleep(5)
                    continue
                st2 = r2.get("st") if isinstance(r2, dict) else "?"
                d2 = r2.get("d") if isinstance(r2, dict) else r2
                if st2 == 200 and isinstance(d2, list) and len(d2) > 0:
                    return pick_best(d2, artist, title), "hit(after-reload)"
                if st2 == 400:
                    print("      probe 400 → 退避", flush=True)
                    time.sleep(BACKOFF)
                    soft = False
                    continue
                print("      reload 后仍空 → 判定软限流", flush=True)
            print(f"      软限流退避 {BACKOFF + 15:.0f}s (attempt {attempt + 1}/3)", flush=True)
            time.sleep(BACKOFF + 15)
            continue
        print(f"      st={st}", flush=True)
        time.sleep(10)
    return None, "giveup"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--charts", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--interval", type=float, default=INTERVAL)
    args = ap.parse_args()

    songs = json.loads(open(args.charts).read())
    src_path = args.charts
    out_path = args.out or src_path.replace(".json", ".prefetched.json")

    tabs = _http_json(f"{CDP}/json")
    tab = next((t for t in tabs if "1music" in t.get("url", "")), None)
    if not tab:
        print("no 1music tab"); sys.exit(1)
    page = CdpPage(tab["webSocketDebuggerUrl"])

    print("[*] 强制 reload 拿全新 token...", flush=True)
    page.reload_wait_token()
    print(f"[*] 可靠富化 {len(songs)} 首（间隔 {args.interval}s）", flush=True)

    ok = fail = 0
    for i, s in enumerate(songs, 1):
        title, artist = s.get("title", ""), s.get("artist", "")
        if s.get("song_hash") and s.get("exp"):
            ok += 1
            continue
        best = None
        # query 顺序：纯 title → title+artist
        for qi, q in enumerate([title, f"{title} {artist}"][:2] if artist else [title]):
            best, how = robust_search(page, q, artist, title)
            tag = f"(q{qi + 1})" if qi else ""
            if best:
                print(f"  OK  {title} — {artist} {tag} => {best.get('title')} | {best.get('artist')} [{how}]", flush=True)
                break
            if qi == 0 and how == "giveup":
                print(f"  q1 giveup，试 q2", flush=True)
            elif qi == 0:
                pass
        if best and best.get("song_hash") and best.get("exp"):
            # thumbnail 必须存：download API 缺 thumbnail 一律 404
            for k in ("song_hash", "videoId", "exp", "thumbnail"):
                if best.get(k):
                    s[k] = best[k]
            if not s.get("album") and best.get("album"):
                s["album"] = best["album"]
            s["_match"] = f"{best.get('title')} — {best.get('artist')}"
            ok += 1
        else:
            fail += 1
            print(f"  MISS {title} — {artist}", flush=True)
        time.sleep(args.interval)
        if i % 8 == 0:
            print(f"  --- 进度 {i}/{len(songs)}，强制 reload 续 token ---", flush=True)
            page.reload_wait_token()
            json.dump(songs, open(out_path, "w"), ensure_ascii=False, indent=2)
    json.dump(songs, open(out_path, "w"), ensure_ascii=False, indent=2)
    print(f"\n完成 {ok}/{len(songs)} 命中 {fail} 失败 -> {out_path}", flush=True)


if __name__ == "__main__":
    main()
