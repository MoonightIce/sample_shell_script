#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""低速富化：间隔 8s + 400 退避 30s + token 自动续期，测 C-Pop 榜真实命中率"""
import argparse
import json
import sys
import time
import urllib.request
import websocket

CDP = "http://localhost:9222"
INTERVAL = 8.0
RATE_BACKOFF = 30.0


def _http_json(url, method="GET"):
    req = urllib.request.Request(url, method=method)
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

    def reload_wait_token(self, max_wait=45):
        print("  [token] reload 页面续 token...")
        self.evaluate("location.reload(); true", False)
        t0 = time.time()
        while time.time() - t0 < max_wait:
            time.sleep(3)
            if self.token():
                print("  [token] 就绪")
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


def pick_best(results, artist, title):
    if not isinstance(results, list) or not results:
        return None
    an, tn = (artist or "").lower(), (title or "").lower()

    def score(r):
        ra, rt = (r.get("artist") or "").lower(), (r.get("title") or "").lower()
        s = 0
        if an and (ra == an or ra.startswith(an) or an.startswith(ra) or an in ra or ra in an):
            s += 4
        if tn and rt == tn:
            s += 3
        elif tn and (tn in rt or rt in tn):
            s += 1
        return s

    best = max(results, key=score)
    return best if score(best) > 0 else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--charts", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--interval", type=float, default=INTERVAL)
    args = ap.parse_args()

    songs = json.loads(open(args.charts).read())
    src_path = args.charts
    out_path = args.out or src_path.replace(".json", ".prefetched.json")
    import pathlib
    out_path = str(pathlib.Path(src_path).with_name(pathlib.Path(src_path).stem + ".prefetched.json")) if not args.out else args.out

    tabs = _http_json(f"{CDP}/json")
    tab = next((t for t in tabs if "1music" in t.get("url", "")), None)
    if not tab:
        print("no 1music tab"); sys.exit(1)
    page = CdpPage(tab["webSocketDebuggerUrl"])
    if not page.token():
        page.reload_wait_token()
    print(f"[*] 开始低速富化 {len(songs)} 首（间隔 {args.interval}s）")

    ok = fail = 0
    for i, s in enumerate(songs, 1):
        title, artist = s.get("title", ""), s.get("artist", "")
        if s.get("song_hash") and s.get("exp"):
            ok += 1
            continue
        best = None
        tried = 0
        for q in ([title, f"{title} {artist}"][:2] if artist else [title]):
            tried += 1
            r = search_in_page(page, q)
            st = r.get("st") if isinstance(r, dict) else "?"
            d = r.get("d") if isinstance(r, dict) else r
            if st == 400:
                print(f"  [{i}] 限流(400)，退避 {RATE_BACKOFF:.0f}s ...")
                time.sleep(RATE_BACKOFF)
                if not page.token():
                    page.reload_wait_token()
                r = search_in_page(page, q)
                st = r.get("st") if isinstance(r, dict) else "?"
                d = r.get("d") if isinstance(r, dict) else r
            if st == 400:
                print(f"  [{i}] 仍 400: {title} — {artist}")
                fail += 1
                break
            best = pick_best(d, artist, title)
            if best:
                break
            if st == 200 and isinstance(d, list) and len(d) == 0:
                # 有货判定放宽：200 空可能真没货；但 q 为 title+artist 失败则再试纯 title
                pass
            time.sleep(0.5)
        if best and best.get("song_hash") and best.get("exp"):
            for k in ("song_hash", "videoId", "exp"):
                s[k] = best[k]
            if not s.get("album") and best.get("album"):
                s["album"] = best["album"]
            s["_match"] = f"{best.get('title')} — {best.get('artist')}"
            ok += 1
            print(f"  OK  {title} — {artist}  =>  {best.get('title')} | {best.get('artist')}")
        else:
            if best is None and tried >= 2:
                pass
            fail += 1
            print(f"  MISS {title} — {artist}")
        time.sleep(args.interval)
        if i % 5 == 0:
            json.dump(songs, open(out_path, "w"), ensure_ascii=False, indent=2)
    json.dump(songs, open(out_path, "w"), ensure_ascii=False, indent=2)
    print(f"\n完成 {ok}/{len(songs)} 命中 {fail} 失败 -> {out_path}")


if __name__ == "__main__":
    main()
