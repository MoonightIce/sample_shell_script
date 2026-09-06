#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lyrics_batch：对 Music 下所有含 flac 的专辑目录补 .lrc（缺才补）。
用法: python lyrics_batch.py [--root /Users/moonightice/GitHub/Music] [--provider lrcapi]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import song_pipeline as sp

DEFAULT_ROOT = Path("/Users/moonightice/GitHub/Music")
LYRICFLOW_DIR = Path(__file__).resolve().parent / "lyricflow"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--provider", default="lrcapi", choices=["tunehub", "lrcapi"])
    args = ap.parse_args()

    root = Path(args.root)
    if not (LYRICFLOW_DIR / "src" / "main.py").exists():
        print("lyricflow 缺失:", LYRICFLOW_DIR)
        sys.exit(1)

    dirs = sorted({p.parent for p in root.rglob("*.flac")})
    todo, done = [], 0
    for d in dirs:
        flacs = list(d.glob("*.flac"))
        lrcs = {p.stem for p in d.glob("*.lrc")}
        missing = [f for f in flacs if f.stem not in lrcs]
        if missing:
            todo.append((d, missing))
    print(f"共 {len(dirs)} 个含 flac 目录；{len(todo)} 个缺歌词（缺 {sum(len(m) for _, m in todo)} 条）")
    for d, missing in todo:
        print(f"  → {d}  ({len(missing)} 缺: {', '.join(f.stem for f in missing[:3])}{'…' if len(missing) > 3 else ''})")
    for i, (d, missing) in enumerate(todo, 1):
        print(f"[{i}/{len(todo)}] lyricflow({args.provider}) → {d}", flush=True)
        try:
            sp.run_lyricflow(d, LYRICFLOW_DIR, args.provider)
            done += 1
        except Exception as e:
            print(f"    失败: {e}", flush=True)
    print(f"\n完成 {done}/{len(todo)} 目录歌词")


if __name__ == "__main__":
    main()
