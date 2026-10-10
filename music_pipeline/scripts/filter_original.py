#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线过滤：只保留 artist 匹配原歌手的 prefetched 项（原版），翻唱项清除 hash 变回 MISS"""
import json, sys

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


def main(path):
    songs = json.load(open(path))
    keep = drop = 0
    out = []
    for s in songs:
        if not s.get("song_hash"):
            out.append(s)  # 未富化的保留（等重测）
            continue
        m = s.get("_match", "")
        # _match 形如 "{title} — {artist}"，artist 用 em dash 后段
        ra = m.rpartition("—")[2].strip() if "—" in m else ""
        if artist_match(_norm(s.get("artist", "")), _norm(ra)):
            keep += 1
            out.append(s)
            print(f"  原版保留  {s.get('title')} — {s.get('artist')}  => {m}")
        else:
            drop += 1  # 整条剔除，避免下载层误判为"待搜索"
            print(f"  翻唱剔除  {s.get('title')} — {s.get('artist')}  (库内为: {m or 'N/A'})")
    json.dump(out, open(path, "w"), ensure_ascii=False, indent=2)
    print(f"\n保留原版 {keep} 首（含未富化 {sum(1 for s in out if not s.get('song_hash'))} 首），剔除翻唱 {drop} 首 -> {path}")


if __name__ == "__main__":
    main(sys.argv[1])
