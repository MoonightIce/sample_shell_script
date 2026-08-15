# video_search — 视频语义/画面检索系统

按技术方案 `video_semantic_search_design.md` 实现的视频检索系统。

**当前状态：骨架已在本机跑通**（采集→抽帧→特征入库→检索全链路）。
受限于本机沙箱网络下载限制（≈10MB 封顶，无法安装 torch/CLIP/opencv），
当前以 **pHash 画面指纹后端** 运行；真正的 **CLIP 语义匹配后端已预留**，
换到能下载 torch 的环境后一条命令切换即可。

## 目录结构

```
video_search/
├── scripts/
│   ├── feature_extract.py   # 特征提取(统一接口, pHash/clip 后端可切换)
│   ├── gen_test_videos.py   # 生成测试视频素材(PIL 简笔场景)
│   ├── extract_frames.py    # 视频抽帧(ffmpeg, 按时间间隔)
│   ├── build_index.py       # 特征提取 + 入库
│   ├── search.py            # 检索(以图搜图/颜色检索/clip语义)
│   └── run_pipeline.py      # 一键端到端跑通
└── data/
    ├── videos/   # 原始视频
    ├── frames/   # 抽出的帧
    └── index/    # 特征索引(records.json + meta.json)
```

## 快速开始

```bash
# 环境: 需要 python3 + ffmpeg + pillow
cd video_search

# 一键跑通全流程(生成素材→抽帧→入库→检索演示)
python scripts/run_pipeline.py

# 单独检索
python scripts/search.py --backend phash --color 蓝色              # 颜色检索
python scripts/search.py --backend phash --image data/frames/beach/frame_00001.jpg  # 以图搜图
```

## 后端说明

| 后端 | 能力 | 依赖 | 状态 |
|------|------|------|------|
| `phash` | 画面指纹检索: 相似画面 + 颜色 | 纯 Python + PIL | ✅ 本机已跑通 |
| `clip` | 语义匹配: 文本↔画面(猫/海边/会议室) | torch + sentence-transformers | ⏳ 预留, 需换环境 |

### 升级到 CLIP 语义匹配

在有 torch 的环境执行:

```bash
pip install sentence-transformers faiss-cpu
# 重新建库(用clip后端)
python scripts/build_index.py --frames data/frames --index-dir data/index --backend clip
# 语义检索
python scripts/search.py --backend clip --query "海边"
```

代码已按 `feature_extract.py` 统一接口设计，切换后端只改 `--backend` 参数，
无需改动抽帧/入库/检索主流程。

## 本机实测性能 (i5-7267U / 8GB)

| 环节 | 耗时 |
|------|------|
| 抽帧 (ffmpeg) | 3 视频 72s → 108 帧, 秒级 |
| 特征入库 (pHash) | 0.002s/帧, 108 帧 0.2s |
| 检索 | 毫秒级 |

> 注意: pHash 后端做的是"相似画面/颜色"匹配, 不具备 CLIP 的抽象语义理解
> (如"猫" "海边"这种概念)。需要语义匹配请切换到 clip 后端。
