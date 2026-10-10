# sample_shell_script

按工具组织的本地自动化脚本。下面的入口路径均相对仓库根目录。

| 工具 | 目录与说明 | 主要入口 |
|---|---|---|
| 文件整理与去重 | [file_sorter](file_sorter/README.md) | `bash file_sorter/scripts/MoveFiles.sh <源目录> [目标目录]` |
| 视频下载与检索 | [video_search](video_search/README.md) | `video_search/scripts/run_pipeline.py`；简单下载示例为 `scripts/download_videos.py` |
| 多平台音乐榜单 | [music_charts Skill](music_charts/SKILL.md) | `music_charts/scripts/fetch_charts.py` |
| 浏览器音乐搜索与归档 | [music_search_cdp Skill](music_search_cdp/SKILL.md) | `music_search_cdp/scripts/search_1music.js` |
| 音乐批处理与流程调度 | [music_pipeline](music_pipeline/README.md) · [Skill](music_pipeline/SKILL.md) | `music_pipeline/scripts/run_pipeline.py`、`chart_download.py` |
| 歌词与音频标签 | [lyricflow](lyricflow/README.zh-CN.md) | 第三方工具，音乐流程按需调用 |
| NAS 下载自动归档 | [nas_archive](nas_archive/README.md) | `nas_archive/scripts/nas_qbt_archive.sh`、`qbt_hook.sh` |
| 波点 iPhone 自动化 | [bodian-iphone-xctest Skill](bodian-iphone-xctest/SKILL.md) | `python3 bodian-iphone-xctest/scripts/run.py --target-days 7` |

音乐下载分为浏览器交互和批处理两个模块，因此这里按目录列出八项；按用途可以合并为七类工具。

## 目录约定

- `scripts/` 放可执行脚本；`tests/` 放该工具测试。
- `references/` 或既有 `docs/` 放流程、设计和排障资料。
- 有 Agent 入口的工具保留 `SKILL.md`，其中相对路径以 Skill 所在目录为基准。
- `lyricflow/` 和波点的 `runtime/` 保留第三方结构、许可证及运行依赖。
- 根目录旧脚本和旧文档名是兼容符号链接，真实文件在工具目录。新命令使用表中的入口；不要把单个链接复制到其他机器，部署时复制实际脚本。
- 根目录 `docs/superpowers/` 是历史方案与计划；`tests/` 检查跨工具目录和兼容入口。

## 本地验证

```sh
python3 -m venv /tmp/sample-shell-tests
/tmp/sample-shell-tests/bin/pip install -r music_charts/requirements.txt -r music_pipeline/requirements.txt
/tmp/sample-shell-tests/bin/python -m pytest music_charts/tests file_sorter/tests tests -q
```

测试验证本地逻辑和目录迁移，不代表在线下载、NAS 部署或 iPhone 真机流程已经验收。

## 外部入口

目录整理保留了原有 Skill 目录和 NAS `/config/scripts/` 部署路径。独立安装的 Skill 副本不会随仓库自动更新。NAS 部署、定时任务和设备操作仍按各工具说明执行；本次目录整理不自动触发这些操作。
