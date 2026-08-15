#!/usr/bin/env python3
"""
语义/画面检索模块（技术方案第6-10步，后端可切换）。

--backend phash (当前可用):
    画面指纹检索。两种查询方式:
      1) --image <path>  以图搜图(相似画面)
      2) --color 蓝色    按主色检索(通过颜色直方图最近邻)
    说明: pHash 做的是"相似画面/颜色"匹配, 不具 CLIP 的抽象语义理解。

--backend clip (预留, 需 torch):
    真正的"文本↔画面"语义匹配, 输入关键词返回语义相关画面。

用法:
    python search.py --backend phash --image /path/to/query.png
    python search.py --backend phash --color 蓝色
    python search.py --backend clip  --query "海边"
"""
import argparse
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DEFAULT_INDEX = BASE / "data/index"
sys.path.insert(0, str(Path(__file__).resolve().parent))
import feature_extract as fe

# 常用中文颜色 → RGB 近似（用于颜色检索）
COLOR_MAP = {
    "红": (220, 40, 40), "红色": (220, 40, 40), "蓝": (40, 90, 220), "蓝色": (40, 90, 220),
    "绿": (40, 160, 60), "绿色": (40, 160, 60), "黄": (240, 210, 40), "黄色": (240, 210, 40),
    "白": (245, 245, 245), "白色": (245, 245, 245), "黑": (30, 30, 30), "黑色": (30, 30, 30),
    "橙": (240, 130, 40), "橙色": (240, 130, 40), "紫": (130, 60, 200), "紫色": (130, 60, 200),
    "棕": (130, 85, 50), "棕色": (130, 85, 50), "粉": (240, 130, 180), "粉色": (240, 130, 180),
    "灰": (140, 140, 140), "灰色": (140, 140, 140), "青": (60, 180, 200), "青色": (60, 180, 200),
}


def load_index(index_dir: Path):
    records = json.loads((index_dir / "records.json").read_text())
    meta = json.loads((index_dir / "meta.json").read_text())
    print(f"[index] 加载 {meta['count']} 帧记录, 后端={meta['backend']}")
    return records, meta


def fmt_ts(sec):
    m, s = divmod(int(sec), 60)
    return f"{m:02d}:{s:02d}"


def agg_report(results, query_desc):
    """按视频聚合打分并输出"""
    if not results:
        print(f"\n「{query_desc}」: 未找到匹配帧")
        return
    agg = {}
    for rec, score in results:
        vid = rec["video_id"]
        if vid not in agg:
            agg[vid] = {"max": score, "count": 0, "ts": []}
        agg[vid]["max"] = max(agg[vid]["max"], score)
        agg[vid]["count"] += 1
        agg[vid]["ts"].append(rec["timestamp"])
    ranked = sorted(agg.items(), key=lambda kv: (kv[1]["max"], kv[1]["count"]), reverse=True)
    print(f"\n===== 「{query_desc}」检索结果 =====")
    for i, (vid, info) in enumerate(ranked, 1):
        print(f"{i}. 视频[{vid}]  相似度{info['max']:.3f}  命中{info['count']}帧")
        loc = " → ".join(fmt_ts(t) for t in sorted(set(info['ts']))[:6])
        if len(set(info['ts'])) > 6:
            loc += f" ...(共{len(set(info['ts']))}个位置)"
        print(f"    命中位置: {loc}")


def search_phash_image(records, query_img_path, top_k=20, thresh=10):
    """以图搜图: 计算查询图与所有帧的 phash 汉明距离 + 颜色距离"""
    q = fe.extract(query_img_path, "phash")
    scored = []
    for rec in records:
        f = rec["feat"]
        if f.get("backend") != "phash":
            continue
        hd = fe.hamming(q["phash"], f["phash"]) / 64.0        # 0~1, 越小越像
        cd = fe.color_dist(q["color"], f["color"]) / 3.0       # 归一化颜色距离
        score = 1.0 - (0.7 * hd + 0.3 * min(cd, 1.0))          # 综合相似度 0~1
        scored.append((rec, score))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]


def search_phash_color(records, color_name, top_k=20):
    """颜色检索: 统计每帧中目标颜色的像素占比"""
    rgb = COLOR_MAP.get(color_name)
    if not rgb:
        return None
    from PIL import Image
    scored = []
    for rec in records:
        f = rec["feat"]
        if f.get("backend") != "phash":
            continue
        frame_path = rec["frame_path"]
        img = Image.open(frame_path)
        ratio = fe.dominant_color_ratio(img, rgb)
        if ratio > 0:
            scored.append((rec, ratio))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]


def search_clip(records, query, top_k=20, threshold=0.2):
    """CLIP 语义检索(预留): 需 torch + sentence-transformers"""
    import numpy as np
    model = fe._load_clip()
    q_vec = np.array(model.encode([query], show_progress_bar=False)[0])
    scored = []
    for rec in records:
        f = rec["feat"]
        if f.get("backend") != "clip":
            continue
        v = np.array(f["clip_vec"])
        score = float(np.dot(q_vec, v) / (np.linalg.norm(q_vec) * np.linalg.norm(v) + 1e-9))
        scored.append((rec, score))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [x for x in scored[:top_k] if x[1] >= threshold]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", type=str, default="phash", choices=["phash", "clip"])
    ap.add_argument("--index-dir", type=str, default=str(DEFAULT_INDEX))
    ap.add_argument("--image", type=str, help="查询图像路径(以图搜图)")
    ap.add_argument("--color", type=str, help="颜色关键词, 如 蓝色/红色/绿色")
    ap.add_argument("--query", type=str, help="语义查询词(仅clip后端)")
    ap.add_argument("--top-k", type=int, default=20)
    args = ap.parse_args()

    records, meta = load_index(Path(args.index_dir))

    if meta.get("backend") != args.backend:
        print(f"!! 索引后端({meta.get('backend')})与请求({args.backend})不一致，请重新建库")
        sys.exit(1)

    if args.backend == "phash":
        if args.image:
            agg_report(search_phash_image(records, args.image, args.top_k),
                       f"以图搜图: {Path(args.image).name}")
        elif args.color:
            res = search_phash_color(records, args.color, args.top_k)
            if res is None:
                print(f"未知颜色: {args.color}，可用: {', '.join(COLOR_MAP)}")
            else:
                agg_report(res, f"颜色: {args.color}")
        else:
            print("phash 后端请用 --image 或 --color 指定查询方式")
    else:
        if args.query:
            agg_report(search_clip(records, args.query, args.top_k), args.query)
        else:
            print("clip 后端请用 --query 指定查询词")


if __name__ == "__main__":
    main()
