#!/usr/bin/env python3
"""
生成测试视频（合成画面）用于验证"画面级语义匹配"全流程。
用 PIL 绘制带真实物体特征的简笔场景图（猫/海/会议），再合成视频，
使 CLIP 能真正对齐"猫/海边/会议室"等语义概念。
"""
import subprocess
import os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

VIDEO_DIR = Path("/Users/moonightice/GitHub/sample_shell_script/video_search/data/videos")
VIDEO_DIR.mkdir(parents=True, exist_ok=True)
FRAME_TMP = Path("/tmp/vs_frames")
FRAME_TMP.mkdir(parents=True, exist_ok=True)

V = 24
W, H = 640, 360


def draw_cat(d: ImageDraw, x, y, scale=1.0, color="orange"):
    """画一只简笔猫（圆圈头+耳朵+身体+尾巴）"""
    s = scale
    # 身体
    d.ellipse([x, y + 30 * s, x + 90 * s, y + 90 * s], fill=color)
    # 头
    d.ellipse([x + 55 * s, y, x + 105 * s, y + 50 * s], fill=color)
    # 耳朵
    d.polygon([(x + 58 * s, y + 12 * s), (x + 72 * s, y - 8 * s), (x + 82 * s, y + 16 * s)], fill=color)
    d.polygon([(x + 78 * s, y + 10 * s), (x + 92 * s, y - 6 * s), (x + 100 * s, y + 18 * s)], fill=color)
    # 眼睛
    d.ellipse([x + 68 * s, y + 18 * s, x + 76 * s, y + 26 * s], fill="black")
    d.ellipse([x + 86 * s, y + 18 * s, x + 94 * s, y + 26 * s], fill="black")
    # 尾巴
    d.arc([x - 10 * s, y + 30 * s, x + 30 * s, y + 80 * s], 0, 160, fill=color, width=int(6 * s))


def draw_beach(d: ImageDraw, variant):
    """画海边场景：天空+海面+沙滩+太阳"""
    if variant == 0:  # 白天海边
        d.rectangle([0, 0, W, int(H * 0.4)], fill=(135, 206, 250))  # 天
        d.rectangle([0, int(H * 0.4), W, int(H * 0.75)], fill=(30, 144, 255))  # 海
        d.rectangle([0, int(H * 0.75), W, H], fill=(238, 214, 175))  # 沙滩
        d.ellipse([W - 110, 40, W - 50, 100], fill=(255, 220, 80))  # 太阳
        # 波浪线
        for i in range(6):
            d.arc([i * 60, int(H * 0.5), i * 60 + 80, int(H * 0.5) + 30], 180, 360,
                  fill=(255, 255, 255), width=2)
    elif variant == 1:  # 海景+船
        d.rectangle([0, 0, W, int(H * 0.35)], fill=(173, 216, 230))
        d.rectangle([0, int(H * 0.35), W, H], fill=(0, 105, 148))
        # 帆船
        d.polygon([(W // 2 - 40, int(H * 0.55)), (W // 2, int(H * 0.35)), (W // 2, int(H * 0.55))], fill="white")
        d.polygon([(W // 2, int(H * 0.35)), (W // 2 + 40, int(H * 0.55)), (W // 2, int(H * 0.55))], fill="white")
        d.polygon([(W // 2 - 45, int(H * 0.55)), (W // 2 + 45, int(H * 0.55)), (W // 2, int(H * 0.65))], fill=(139, 69, 19))
    else:  # 日落海边
        d.rectangle([0, 0, W, int(H * 0.5)], fill=(255, 140, 105))
        d.rectangle([0, int(H * 0.5), W, int(H * 0.8)], fill=(255, 99, 71))
        d.rectangle([0, int(H * 0.8), W, H], fill=(139, 90, 60))
        d.ellipse([W // 2 - 50, int(H * 0.25), W // 2 + 50, int(H * 0.35)], fill=(255, 220, 100))


def draw_meeting(d: ImageDraw, variant):
    """画会议室场景：房间+白板+桌椅+人物"""
    # 墙面
    d.rectangle([0, 0, W, H], fill=(200, 205, 210))
    # 地面
    d.rectangle([0, int(H * 0.7), W, H], fill=(150, 140, 130))
    # 白板
    d.rectangle([60, 60, 340, 220], fill="white", outline="gray", width=3)
    d.line([80, 100, 320, 100], fill="black", width=2)
    d.line([80, 140, 320, 140], fill="black", width=2)
    # 桌子
    d.rectangle([60, int(H * 0.72), 340, int(H * 0.8)], fill=(110, 85, 60))
    # 人物（简笔）
    d.ellipse([130, int(H * 0.55), 170, int(H * 0.72)], fill=(60, 70, 80))  # 头+身
    d.ellipse([230, int(H * 0.55), 270, int(H * 0.72)], fill=(80, 60, 60))
    if variant >= 1:
        # 投影仪/屏幕
        d.rectangle([380, 70, 600, 200], fill=(230, 240, 250), outline="gray", width=2)
        d.polygon([(400, 100), (580, 100), (560, 180), (420, 180)], fill=(70, 130, 180))
    if variant >= 2:
        # 更多人
        d.ellipse([330, int(H * 0.55), 370, int(H * 0.72)], fill=(90, 90, 90))


def render_scene(scene_type, variant):
    """渲染一帧真实感简笔场景图"""
    img = Image.new("RGB", (W, H), (240, 240, 240))
    d = ImageDraw.Draw(img)
    if scene_type == "beach":
        draw_beach(d, variant)
    elif scene_type == "cat":
        if variant == 0:
            draw_cat(d, 100, 150, 1.5)
            d.rectangle([0, 0, W, H], fill=None)
        elif variant == 1:
            draw_cat(d, 200, 120, 1.2, "gray")
            draw_cat(d, 400, 160, 0.9, "black")
        else:
            draw_cat(d, 150, 130, 1.4, "brown")
            d.rectangle([40, 40, 400, 320], outline="gray", width=3)  # 沙发
    elif scene_type == "meeting":
        draw_meeting(d, variant)
    return img


def make_video(name, scene_type, variants, dur_per_scene=4):
    """每个场景变体渲染 dur_per_scene 秒，轻微缩放模拟运动"""
    out = VIDEO_DIR / f"{name}.mp4"
    if out.exists():
        print(f"[skip] {out.name} 已存在")
        return out

    import tempfile
    scene_dir = FRAME_TMP / name
    scene_dir.mkdir(parents=True, exist_ok=True)
    for v in variants:
        img = render_scene(scene_type, v)
        # 轻微放大模拟运动（简单起见，每段存3帧渐近）
        for k in range(2):
            scale = 1.0 + k * 0.03
            nw, nh = int(W * scale), int(H * scale)
            cropped = img.resize((nw, nh)).crop(((nw - W) // 2, (nh - H) // 2, (nw - W) // 2 + W, (nh - H) // 2 + H))
            cropped.save(scene_dir / f"s{v}_f{k}.png")

    # 用 ffmpeg 将 PNG 序列拼成视频
    import glob
    pngs = sorted(glob.glob(str(scene_dir / "*.png")))
    # 生成每个 PNG 重复 dur*V/2 帧的 concat 列表
    ff_inputs = []
    for p in pngs:
        ff_inputs += ["-i", p]
    # 每个输入循环 dur_per_scene/2 秒 * V 帧
    frames_per_img = int(dur_per_scene * V / 2)
    filt = []
    for i in range(len(pngs)):
        filt.append(f"[{i}:v]loop=loop={frames_per_img - 1}:size=1:start=0,setpts=PTS+{i * dur_per_scene}/TB[v{i}]")
    filt.append("".join(f"[v{i}]" for i in range(len(pngs))) + f"concat=n={len(pngs)}:v=1:a=0,format=yuv420p[vout]")
    cmd = ["ffmpeg", "-y"] + ff_inputs + [
        "-filter_complex", ";".join(filt),
        "-map", "[vout]", "-r", str(V), "-c:v", "libx264", "-preset", "ultrafast", str(out),
    ]
    print(f"[gen] {out.name} ...")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  !! 失败: {r.stderr[-600:]}")
    else:
        print(f"  OK -> {os.path.getsize(out)//1024} KB")
    return out


def main():
    make_video("beach", "beach", [0, 1, 2])
    make_video("cat", "cat", [0, 1, 2])
    make_video("meeting", "meeting", [0, 1, 2])
    print("\n全部测试视频生成完毕 ->", VIDEO_DIR)


if __name__ == "__main__":
    main()
