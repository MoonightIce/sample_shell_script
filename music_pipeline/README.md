# 音乐批处理与流程调度

这里集中维护原先散落在仓库根目录的音乐脚本。以下命令从仓库根目录运行；相对输入/输出路径仍相对当前工作目录，已有数据不搬迁。仓库根目录运行产生的 `data/` 沿用忽略规则；从其他目录执行时请显式指定输出位置。

## 入口

| 脚本（均在 `scripts/`） | 用途 |
|---|---|
| `run_pipeline.py` | 调用相邻 `music_search_cdp/` 的搜索、下载、LyricFlow 归档流程 |
| `chart_fetch.py` | Apple Music 中文及粤语榜单采集；与 `music_charts/` 多平台 JSON 格式不同 |
| `song_pipeline.py` | 单曲搜索、下载、转码与歌词 |
| `chart_download.py`、`chart_download_v2.py` | 两个现有批量下载入口，保留各自行为和参数 |
| `prefetch_hashes.py`、`prefetch_via_cdp.py` | token/CDP 方式预取下载信息 |
| `prefetch_slow.py`、`prefetch_reliable.py` | 原有低速与带重试的预取变体 |
| `filter_original.py` | 离线过滤预取 JSON 中的非原歌手匹配 |
| `backfill_thumbnail.py` | 补齐预取结果封面 |
| `lyrics_batch.py` | 对本地音乐目录批量补歌词 |

## 环境与路径

Python 3.10+；基础依赖：`python3 -m pip install -r music_pipeline/requirements.txt`。
繁简归一可选安装 `opencc-python-reimplemented`。浏览器下载可能需要 Playwright；音频转码需要 FFmpeg；歌词依赖见 [LyricFlow](../lyricflow/README.zh-CN.md)。CDP 流程使用 Node.js 与 [浏览器模块](../music_search_cdp/SKILL.md) 的依赖。

```sh
python3 music_pipeline/scripts/chart_fetch.py --help
python3 music_pipeline/scripts/song_pipeline.py --help
python3 music_pipeline/scripts/chart_download.py --help
python3 music_pipeline/scripts/run_pipeline.py --help
```

LyricFlow 默认路径从脚本真实位置定位到仓库的 `lyricflow/`，不依赖启动目录；支持 `--lyricflow-dir` 的入口仍允许覆盖。旧根目录命令通过符号链接兼容。

脚本仍保留原有下载、筛选和输出默认值。运行前核对 `--out`、`--root` 和选曲参数；本次整理没有执行下载或修改音乐库。
