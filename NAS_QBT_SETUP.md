# NAS + qBittorrent 下载自动归档

`nas_qbt_archive.sh` — Synology DSM 上 qBittorrent 下载完成 → 视频自动归档到 NAS 指定目录，附带与 `MoveFiles.sh` 同算法的字节级去重。

## 工作原理

```
qBittorrent 种子下载完成
        │  (qBittorrent "Run external program" 完成钩子, 传入 %F %N %L %I)
        ▼
nas_qbt_archive.sh "%F" "%N" "%L" "%I"
        │  ① 内容路径 → 找出其中所有视频文件
        │  ② 去重: 与归档目录已有视频比对 (大小 → 头/尾1MB采样 → 全量MD5)
        │  ③ 归档: flat 平级 或 code 按番号分类
        │  ④ 同 stem 字幕/封面跟随视频归档
        ▼
    ARCHIVE_BASE (/volume1/video/movies)
```

## 一、安装

```bash
# 1. 把脚本拷到 NAS (以 /volume1/video/scripts 为例)
cp nas_qbt_archive.sh /volume1/video/scripts/
chmod +x /volume1/video/scripts/nas_qbt_archive.sh

# 2. 确认归档目录 (脚本默认 /volume1/video/movies, 可改脚本头部配置)
mkdir -p /volume1/video/movies
```

## 二、配置 qBittorrent 完成钩子

qBittorrent **设置 → 下载 → Run external program on torrent completion** 填：

```
/volume1/video/scripts/nas_qbt_archive.sh "%F" "%N" "%L" "%I"
```

| 参数 | qBittorrent 变量 | 用途 |
|---|---|---|
| `%F` | 内容路径 | 单文件种子=文件路径；多文件种子=种子目录（**必传**） |
| `%N` | 种子名 | 仅日志 |
| `%L` | 分类 | 仅日志 |
| `%I` | InfoHash | 仅日志，用于区分日志里同名的种子 |

> 钩子由 qBittorrent 进程经 shell 执行。路径含空格没问题（已加引号）；文件名若含 `"` 或 `'` 会被 shell 拆坏，这是 qBittorrent 钩子的固有限制，改名即可。

### Docker 版（linuxserver/qbittorrent 等）

钩子脚本必须放在**容器内可见**的路径，且 `ARCHIVE_BASE` 也要写成容器内路径。例如：

```
-v /volume1/video/qbt-config:/config
-v /volume1/downloads:/downloads
-v /volume1/video/movies:/movies
```

钩子配置填：`/config/scripts/nas_qbt_archive.sh "%F" "%N" "%L" "%I"`，脚本头 `ARCHIVE_BASE=/movies`。即脚本和归档目录都要挂载进容器，qBittorrent 才能执行脚本、脚本才能写归档目录。

## 三、测试

```bash
# 演练模式: 只打印将要执行的动作, 不动任何文件
/volume1/video/scripts/nas_qbt_archive.sh --dry-run "/volume1/downloads/某种子目录"

# 真跑: 复制归档
/volume1/video/scripts/nas_qbt_archive.sh "/volume1/downloads/某种子目录" "种子名" "分类" "TESTHASH"

# 查日志
tail -f /volume1/video/scripts/logs/archive.log
```

## 四、配置项

| 变量 | 默认值 | 说明 |
|---|---|---|
| `ARCHIVE_BASE` | `/volume1/video/movies` | 归档根目录 |
| `SORT_MODE` | `flat` | `flat`=全部平级进一个目录（配合去重）；`code`=按番号归类 `ARCHIVE_BASE/<番号>/`，无番号进 `其他/` |
| `MOVE_MODE` | `copy` | `copy`=复制归档（**做种不受影响，推荐**）；`move`=移动归档（种子失去做种文件，仅"下完即看"场景） |
| `DEDUP_MODE` | `hash` | 字节级去重：大小 → 头/尾各 1MB 采样指纹 → 全量 MD5 三级确认，与 MoveFiles.sh 同算法 |
| `DEDUP_ACTION` | `skip` | 重复文件 `skip`=跳过 / `rename`=加序号保留 |
| `KEEP_SIDEKICK` | `1` | 同 stem 字幕/封面（srt/ass/vtt/sub/jpg/nfo 等）跟随视频归档 |

> 所有配置都支持环境变量覆盖（如 `SORT_MODE=code MOVE_MODE=move /volume1/video/scripts/nas_qbt_archive.sh "%F"`），钩子配置里也可直接加前缀。

## 五、常见问题

**Q1: 下载目录和归档目录不在同一存储卷？**
同卷 `mv` 是瞬间改名；跨卷 `mv`/`cp` 会退化为整文件复制，慢且占双倍 IO。建议下载目录与 `ARCHIVE_BASE` 放同一卷（如都在 `/volume1`）。

**Q2: 用 `MOVE_MODE=move` 后 qBittorrent 做种报错/触发 recheck？**
正常现象——文件被移走，种子失去做种文件。要么换 `copy`（默认），要么在 qBittorrent 里关闭该种子的做种（或设置分享率/做种时间到即停）。

**Q3: 种子 recheck / 重新下载完成，钩子会重复触发吗？**
会，但脚本幂等：已归档文件再次归档时（同目录判断 / 去重判断）自动跳过，不会产生重复。

**Q4: 多个种子同时完成，钩子并发跑会不会冲突？**
不会。脚本有 mkdir 原子锁，后到的实例会直接跳过（日志有记录）。

**Q5: 为什么只做视频去重，不处理"疑似转码副本"？**
`MoveFiles.sh` 的 `meta`/`time` 模式需要 ffprobe/ffmpeg，DSM 默认不装。本脚本只依赖系统自带的 coreutils，保证装完即用。需要深度去重的种子可先归档，再定期用 Mac 上的 `MoveFiles.sh` 对 `/volume1/video/movies` 跑一遍 `meta`/`time` 模式。

**Q6: 日志在哪？**
`<脚本目录>/logs/archive.log`，每行含时间、InfoHash、动作与路径。
