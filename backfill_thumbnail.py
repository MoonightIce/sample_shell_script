#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""补 thumbnail：对 prefetched JSON 中带 hash 但缺 thumbnail 的条目重新 search 补齐。
search 返回 hash 与存储一致 → 只补 thumbnail；不一致 → 按原版 artist 匹配取整套新字段。
用法: python backfill_thumbnail.py <prefetched.json> [--interval 12]
"""
import argparse
import json
import sys
import time
import urllib.request
import websocket

CDP = "http://localhost:9222"

try:
    import opencc
    _CC = opencc.OpenCC("t2s")
except Exception:
    _CC = None

_ARTIST_ALIASES = {
    "陈奕迅": ["eason chan", "eason"],
    "莫文蔚": ["karen mok"],
    "张敬轩": ["hins"],
}


def _norm(s):
    s = (s or "").lower().strip()
    return _CC.convert(s) if _CC else s


def artist_match(an, ra):
    if not an or not ra:
        return False
    if ra == an or an.startswith(ra) or ra.startswith(an) or an in ra or ra in an:
        return True
    for alias in _ARTIST_ALIASES.get(an, []):
        if ra == alias or ra in alias or alias in ra:
            return True
    return False


def _http_json(url):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


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
                m = json.loads(ws.recv())
                if m.get("id") == self._id:
                    res = m.get("result", {}).get("result", {})
                    if res.get("exceptionDetails"):
                        return {"__err__": str(res["exceptionDetails"].get("text", ""))[:200]}
                    return res.get("value")
        finally:
            ws.close()

    def token(self):
        v = self.evaluate("(window.turnstile && window.turnstile.getResponse) ? window.turnstile.getResponse() : null", False)
        return v if v and len(str(v)) > 20 else None

    def reload_wait_token(self, max_wait=45):
        print("  [token] reload...", flush=True)
        self.evaluate("location.reload(); true", False)
        t0 = time.time()
        while time.time() - t0 < max_wait:
            time.sleep(3)
            if self.token():
                print("  [token] 就绪", flush=True)
                return True
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("json_path")
    ap.add_argument("--interval", type=float, default=12.0)
    args = ap.parse_args()

    songs = json.load(open(args.json_path))
    need = [s for s in songs if s.get("song_hash") and not s.get("thumbnail")]
    if not need:
        print("无需补 thumbnail（全部已带）"); return
    print(f"需补 {len(need)}/{len(songs)} 首")

    tabs = _http_json(f"{CDP}/json")
    tab = next((t for t in tabs if "1music" in t.get("url", "")), None)
    if not tab:
        print("no 1music tab"); sys.exit(1)
    page = CdpPage(tab["webSocketDebuggerUrl"])
    print("[*] reload 拿 token...", flush=True)
    page.reload_wait_token()

    done = 0
    for i, s in enumerate(need, 1):
        title, artist = s.get("title", ""), s.get("artist", "")
        old_hash = s.get("song_hash", "")
        best = None
        for qi, q in enumerate([title, f"{title} {artist}"][:2] if artist else [title]):
            r = search_in_page(page, q)
            st = r.get("st") if isinstance(r, dict) else "?"
            d = r.get("d") if isinstance(r, dict) else r
            if st == 400 or (st == 200 and isinstance(d, list) and not d):
                page.reload_wait_token()
                time.sleep(8)
                r = search_in_page(page, q)
                st = r.get("st") if isinstance(r, dict) else "?"
                d = r.get("d") if isinstance(r, dict) else r
            if not (st == 200 and isinstance(d, list) and d):
                print(f"  [{i}] 搜索异常 st={st}", flush=True)
                continue
            # 1) hash 一致直取；2) 否则按原版 artist
            cand = next((x for x in d if x.get("song_hash") == old_hash), None)
            if cand:
                best = cand
                break
            an = _norm(artist)
            orig = [x for x in d if artist_match(an, _norm(x.get("artist")))]
            if orig:
                best = orig[0]
                break
            if qi == 0:
                print(f"  [{i}] q1 无原版，试 q2", flush=True)
            time.sleep(3)
        if not best or not best.get("thumbnail"):
            print(f"  [{i}] 未补到: {title} — {artist}", flush=True)
            time.sleep(args.interval)
            continue
        if best.get("song_hash") != old_hash:
            print(f"  [{i}] hash 更新(旧:{old_hash[:16]}…)", flush=True)
        for k in ("song_hash", "videoId", "exp", "thumbnail"):
            if best.get(k):
                s[k] = best[k]
        s["_match"] = f"{best.get('title')} — {best.get('artist')}"
        done += 1
        print(f"  [{i}] OK {title} — {artist} (thumb {'✓' if s.get('thumbnail') else '✗'})", flush=True)
        time.sleep(args.interval)
        if i % 5 == 0:
            json.dump(songs, open(args.json_path, "w"), ensure_ascii=False, indent=2)
    json.dump(songs, open(args.json_path, "w"), ensure_ascii=False, indent=2)
    print(f"\n完成 {done}/{len(need)} -> {args.json_path}", flush=True)


if __name__ == "__main__":
    main()
