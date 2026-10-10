---
name: music-pipeline
description: 使用仓库音乐批处理脚本采集 Apple Music 中文榜单、预取歌曲信息、批量下载和补歌词；需要交互选曲时使用相邻 music_search_cdp 工具。
---

# 音乐批处理

路径相对本 Skill 目录。先读 [入口与依赖](README.md)，按需求选择 `scripts/` 下的入口，通过 `--help` 核对参数（`filter_original.py` 直接接收 JSON 文件路径，没有帮助参数）。该工具依赖同一仓库的 `../lyricflow/`；`scripts/run_pipeline.py` 还依赖 `../music_search_cdp/`，不要只复制此 Skill 目录作为独立安装包。

- `scripts/chart_fetch.py` 的榜单格式供此工具批处理使用，不假定能直接消费 `../music_charts/` 的多平台结果。
- `scripts/prefetch_*.py` 是不同现有实现，按需要选一个，不并发操作同一浏览器或 JSON。
- 下载前核对歌手、版本、输出位置和用户指定范围；部分批处理入口会自动选择候选，存在歧义时使用 [交互搜索流程](../music_search_cdp/SKILL.md) 确认。
- 相对数据路径由当前工作目录决定；LyricFlow 从仓库位置定位。
- 交付区分本地测试、实际下载、歌词匹配和音乐库写入，不把 CLI 帮助检查视为完整运行成功。
