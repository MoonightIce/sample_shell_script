#!/usr/bin/env python3
"""
特征提取模块（统一接口，后端可切换）。

当前可用后端:
  - pHash: 感知哈希(64bit) + 颜色直方图。纯 Python + PIL，无需深度学习依赖。
    可做"相似画面/图像"检索。这是受限环境下可运行的骨架。

预留后端(换到能下载 torch 的环境后启用):
  - clip: 用 sentence-transformers 的 CLIP 双塔编码，支持真正的"文本↔画面"语义匹配。

用法: 由 build_index.py / search.py 调用，通过 --backend 切换。
"""
import io
import hashlib
from pathlib import Path

from PIL import Image

# ============ 后端1: pHash（感知哈希） ============

def phash(image, hash_size=8):
    """
    差异哈希 (dHash): 缩放到 (hash_size+1) x hash_size 灰度图，
    对相邻像素比较生成 hash_size² 位哈希。复杂度低，速度快，适合快速检索骨架。
    """
    w = hash_size + 1
    img = image.convert("L").resize((w, hash_size), Image.Resampling.LANCZOS)
    pixels = list(img.getdata())  # 行优先, 共 w*hash_size 个
    bits = 0
    for r in range(hash_size):
        row = r * w
        for c in range(hash_size):
            bits = (bits << 1) | (1 if pixels[row + c] > pixels[row + c + 1] else 0)
    return bits


def hamming(a, b):
    """两个哈希的汉明距离(位不同的个数)"""
    return bin(a ^ b).count("1")


def color_hist(image, bins=16):
    """RGB 颜色直方图(3通道)，归一化后拉平，用于补充颜色特征"""
    img = image.resize((32, 32))
    hist = img.histogram()
    # histogram 返回 256*3 个值，按 bins 分桶压缩
    comp = []
    for ch in range(3):
        h = hist[ch * 256:(ch + 1) * 256]
        binned = [0.0] * bins
        for i, v in enumerate(h):
            binned[min(i * bins // 256, bins - 1)] += v
        s = sum(binned) or 1
        comp += [v / s for v in binned]
    return tuple(comp)


def color_dist(a, b):
    """颜色直方图欧氏距离"""
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def dominant_color_ratio(image, target_rgb, threshold=90):
    """
    统计画面中与目标颜色接近的像素占比(0~1)。
    将图缩小到 32x32，逐像素计算与 target_rgb 的欧氏距离，
    距离小于 threshold 视为"命中目标色"。
    """
    img = image.convert("RGB").resize((32, 32))
    px = list(img.get_flattened_data()) if hasattr(img, "get_flattened_data") else list(img.getdata())
    hit = 0
    for r, g, b in px:
        d = ((r - target_rgb[0]) ** 2 + (g - target_rgb[1]) ** 2 + (b - target_rgb[2]) ** 2) ** 0.5
        if d <= threshold:
            hit += 1
    return hit / len(px)


def extract_pHash(image_path):
    """提取一帧的 (pHash, color_hist) 特征"""
    img = Image.open(image_path).convert("RGB")
    return {
        "phash": phash(img),
        "color": color_hist(img),
    }


# ============ 后端2: CLIP（预留，需 torch + sentence-transformers） ============

_clip_model = None


def _load_clip(model_name="clip-ViT-B-32"):
    global _clip_model
    if _clip_model is None:
        from sentence_transformers import SentenceTransformer
        _clip_model = SentenceTransformer(model_name)
    return _clip_model


def extract_clip(image_path):
    """CLIP 图像编码（需 torch，受限环境装不上，换环境后可用）"""
    model = _load_clip()
    img = Image.open(image_path).convert("RGB")
    return {"clip_vec": model.encode([img], show_progress_bar=False)[0].tolist()}


# ============ 统一入口 ============

BACKENDS = {"phash": extract_pHash, "clip": extract_clip}


def extract(image_path, backend="phash"):
    fn = BACKENDS.get(backend)
    if fn is None:
        raise ValueError(f"未知后端: {backend}，可用: {list(BACKENDS)}")
    feats = fn(image_path)
    feats["backend"] = backend
    return feats


def dim(backend="phash"):
    """特征维度，供索引建立使用"""
    return 64 if backend == "phash" else (768 if backend == "clip" else 0)
