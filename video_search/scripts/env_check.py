#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
env_check.py — 运行环境自检与自举(playwright / ffmpeg / Chrome)。

设计目标: 用户一条命令直接跑下载脚本, 无需手动指定某个解释器或手动装依赖。
所有依赖只装到项目虚拟环境 video_search/.venv, 不污染系统 Python, 也不依赖
WorkBuddy 的 ~/.workbuddy 目录。

自举顺序(ensure_playwright):
  1. 当前解释器能 import playwright → 直接返回, 继续执行
  2. 项目 venv(video_search/.venv) 存在且 playwright 可用 → os.execv 用 venv python 重启当前脚本
  3. auto_install=True 且能联网 → 创建项目 venv + pip install playwright → 重启
  4. 全部失败 → 打印明确安装指引

用法(在下载类脚本顶部):
  import env_check
  env_check.ensure_playwright()   # 自动重启自身直到 playwright 可用
  env_check.check_ffmpeg()        # 缺失时打印安装指引(不阻塞)
  env_check.check_chrome()        # 缺失时打印安装指引(不阻塞)
"""
import os
import sys
import subprocess
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent   # video_search/
VENV_DIR = PROJECT_ROOT / ".venv"
VENV_PY = VENV_DIR / "bin" / "python"                   # macOS/Linux 布局


def _venv_python():
    """返回项目 venv 的 python 绝对路径(存在则返回, 否则 None)。"""
    return str(VENV_PY) if VENV_PY.exists() else None


def _can_import_playwright(py: str) -> bool:
    """探测指定解释器能否 import playwright。"""
    try:
        r = subprocess.run([py, "-c", "import playwright"],
                           capture_output=True, timeout=30)
        return r.returncode == 0
    except Exception:
        return False


def _create_venv():
    """创建项目 venv 并 pip 安装 playwright。返回 venv python 路径或 None。"""
    if VENV_PY.exists():
        return str(VENV_PY)
    print(f"[env] 创建项目虚拟环境: {VENV_DIR} ...", flush=True)
    r = subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)],
                       capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        print(f"[env] venv 创建失败: {r.stderr[-300:]}", flush=True)
        return None
    py = _venv_python()
    if not py:
        return None
    print(f"[env] 安装 playwright(首次约 1-3 分钟, 视网速) ...", flush=True)
    r = subprocess.run([py, "-m", "pip", "install", "--quiet", "playwright"],
                       capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        print(f"[env] playwright 安装失败: {r.stderr[-400:]}", flush=True)
        return None
    if _can_import_playwright(py):
        return py
    print("[env] playwright 安装后仍不可用, 请检查网络/镜像源", flush=True)
    return None


def ensure_playwright(auto_install: bool = True):
    """自举: 确保 playwright 可用; 必要时创建项目 venv 并重启当前脚本。"""
    try:
        import playwright  # noqa: F401
        return
    except ImportError:
        pass

    # 1) 项目 venv 已就绪 → 用 venv python 重启当前脚本(无感切换)
    py = _venv_python()
    if py and _can_import_playwright(py):
        print(f"[env] 切换至项目虚拟环境: {py}", flush=True)
        os.execv(py, [py] + sys.argv)

    # 2) 自动创建并安装
    if auto_install:
        py = _create_venv()
        if py:
            print(f"[env] 环境就绪, 重启: {py}", flush=True)
            os.execv(py, [py] + sys.argv)

    # 3) 兜底提示
    print("[env] 未找到可用的 playwright, 请手动执行以下任一条后重试:", flush=True)
    print(f"    {sys.executable} -m pip install playwright", flush=True)
    print(f"    或首次运行: {Path(__file__).resolve()}", flush=True)
    sys.exit(1)


def check_ffmpeg():
    """检查 ffmpeg/ffprobe(下载/探测需要), 缺失时打印安装指引。"""
    for c in ("ffmpeg", "ffprobe"):
        if shutil.which(c):
            continue
        print(f"[env] 缺少 {c}, 请安装: brew install ffmpeg", flush=True)


def check_chrome():
    """检查真实 Chrome(绕过 Cloudflare 需要), 缺失时打印指引。"""
    chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if not chrome.exists():
        print("[env] 未找到 Chrome(/Applications/Google Chrome.app), "
              "请安装 Chrome 后重试(脚本需要真实浏览器绕过 CF)", flush=True)


if __name__ == "__main__":
    ensure_playwright()
    check_ffmpeg()
    check_chrome()
    print("[env] 环境自检通过", flush=True)
