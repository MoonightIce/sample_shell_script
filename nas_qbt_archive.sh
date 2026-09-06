#!/bin/bash
#
# nas_qbt_archive.sh — qBittorrent 下载完成钩子 → NAS 自动归档
#
# 适用: Synology DSM (bash 3.2+ 兼容), qBittorrent 套件版或 Docker 版
#
# ── qBittorrent 配置 (设置 → 下载 → Run external program on torrent completion) ──
#   /volume1/video/scripts/nas_qbt_archive.sh "%F" "%N" "%L" "%I"
#     %F = 内容路径 (单文件种子=文件路径, 多文件种子=目录)   ← 必传, 第一个参数
#     %N = 种子名    %L = 分类    %I = InfoHash (后三个可选, 仅日志用)
#   注意: 钩子由 qBittorrent 进程经 shell 执行, 路径含空格没问题 (参数已加引号);
#         文件名若含单引号/双引号会被 shell 拆坏, 属 qBittorrent 钩子固有限制。
#
# ── 功能 ──
#   1. 种子完成 → 视频归档到 ARCHIVE_BASE
#        SORT_MODE=flat   全部平级到一个目录 (默认, 配合 hash 去重, 同 MoveFiles.sh 心智)
#        SORT_MODE=code   按番号归类 ARCHIVE_BASE/<番号>/, 无番号进 ARCHIVE_BASE/其他/
#   2. 去重 (DEDUP_MODE=hash, 默认): 大小 → 头尾各 1MB 采样指纹 → 全量 MD5 三级确认,
#      算法与 MoveFiles.sh 完全一致; 重复则跳过 (或 rename 保留, 见 DEDUP_ACTION)
#   3. 同 stem 字幕/封面等 sidekick 文件跟随视频归档
#   4. 幂等: 重复触发 (recheck / 重新下载完成) 不会重复归档
#
# ── 用法 ──
#   nas_qbt_archive.sh <内容路径> [种子名] [分类] [infohash]
#   nas_qbt_archive.sh --dry-run <内容路径>     # 演练, 只打印不执行
#
# ── 关键取舍 ──
#   MOVE_MODE=copy (默认): 复制归档, 下载目录原文件保留 → 种子继续做种不受影响,
#                          代价是占用双倍空间 (下载完可手动删种子)
#   MOVE_MODE=move : 移动归档, 种子将失去做种文件 (做种会失败/触发 recheck),
#                    只适合"下完即看、不需要做种"的场景
#   下载目录与 ARCHIVE_BASE 尽量同一存储卷: 同卷 mv 是瞬间改名, 跨卷会退化为整文件复制
#
# 依赖: 仅 bash + coreutils (stat/dd/md5sum, DSM 自带); 不依赖 ffprobe

set -u

# ============ 配置 (可用环境变量覆盖, 便于测试) ============
ARCHIVE_BASE="${ARCHIVE_BASE:-/volume1/video/movies}"   # 归档根目录
SORT_MODE="${SORT_MODE:-flat}"                          # flat | code (按番号归类)
MOVE_MODE="${MOVE_MODE:-copy}"                          # copy | move
DEDUP_MODE="${DEDUP_MODE:-hash}"                        # none | hash
DEDUP_ACTION="${DEDUP_ACTION:-skip}"                    # skip | rename
KEEP_SIDEKICK="${KEEP_SIDEKICK:-1}"                     # 1=字幕/封面跟随视频归档, 0=不处理
LOG_DIR="${LOG_DIR:-$(cd "$(dirname "$0")" && pwd)/logs}"
LOG_FILE="$LOG_DIR/archive.log"
LOCK_DIR="$(dirname "$LOG_DIR")/.qbt_archive.lock"

VIDEO_EXTS="mp4 m4v mov avi mkv webm flv wmv ts mpg mpeg 3gp rmvb rm iso"
SIDEKICK_EXTS="srt ass vtt sub idx sup jpg jpeg png webp nfo"
SAMPLE_BYTES=1048576        # 采样: 头/尾各 1MB
CODE_REGEX='[A-Za-z]{2,8}-[0-9]{2,6}|FC2-PPV-[0-9]+'     # 番号正则 (grep -oE)
OTHER_NAME="其他"

export PATH=/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/bin:$PATH

# ============ 参数解析 ============
DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && { DRY_RUN=1; shift; }
if [ $# -lt 1 ]; then
    echo "用法: $0 [--dry-run] <内容路径> [种子名] [分类] [infohash]"
    exit 1
fi
CONTENT_PATH="$1"
TORRENT_NAME="${2:-}"
CATEGORY="${3:-}"
INFO_HASH="${4:-}"

[ -e "$CONTENT_PATH" ] || { echo "!! 内容路径不存在: $CONTENT_PATH"; exit 1; }

mkdir -p "$LOG_DIR" 2>/dev/null

# ============ 工具函数 ============
log() {
    local line="$(date '+%F %T') ${INFO_HASH:+[$INFO_HASH] }$*"
    echo "$line" | tee -a "$LOG_FILE"
}

# 兼容 macOS (md5) 与 Linux/DSM (md5sum)
file_md5() {
    if command -v md5 >/dev/null 2>&1 && [ "$(uname)" = "Darwin" ]; then
        md5 -q "$1" 2>/dev/null
    else
        md5sum "$1" 2>/dev/null | awk '{print $1}'
    fi
}
hash_stdin() {
    if command -v md5 >/dev/null 2>&1 && [ "$(uname)" = "Darwin" ]; then
        md5 -q 2>/dev/null
    else
        md5sum 2>/dev/null | awk '{print $1}'
    fi
}
file_size() {
    # macOS BSD stat 用 -f %z; Linux/busybox stat 用 -c %s (busybox 的 -f 是文件系统标志, 不能混用)
    case "$(uname -s 2>/dev/null)" in
        Darwin) stat -f %z "$1" 2>/dev/null || echo "0" ;;
        *)      stat -c %s "$1" 2>/dev/null || echo "0" ;;
    esac
}

is_video() {
    local ext="${1##*.}"
    ext=$(echo "$ext" | tr '[:upper:]' '[:lower:]')
    case " $VIDEO_EXTS " in *" $ext "*) return 0 ;; *) return 1 ;; esac
}
is_sidekick() {
    local ext="${1##*.}"
    ext=$(echo "$ext" | tr '[:upper:]' '[:lower:]')
    case " $SIDEKICK_EXTS " in *" $ext "*) return 0 ;; *) return 1 ;; esac
}

# 快速指纹: 大小|头1MB hash|尾1MB hash; 小文件直接全量
file_fast_fp() {
    local f="$1" size head tail
    size=$(file_size "$f")
    if [ "$size" -le $((SAMPLE_BYTES * 2)) ]; then
        echo "$size|$(file_md5 "$f")"; return 0
    fi
    head=$(dd if="$f" bs=1048576 count=1 2>/dev/null | hash_stdin)
    tail=$(dd if="$f" bs=1048576 skip=$(((size - 1) / 1048576)) count=1 2>/dev/null | hash_stdin)
    echo "$size|$head|$tail"
}

# 提取番号 (code 模式用), 取第一个匹配转大写; 没有则输出 OTHER_NAME
extract_code() {
    local name="$1" m
    m=$(echo "$name" | grep -oE "$CODE_REGEX" | head -1 | tr '[:lower:]' '[:upper:]')
    [ -n "$m" ] && echo "$m" || echo "$OTHER_NAME"
}

# 目标文件名 (copy/move 后归档路径), 带重名处理
# $1 源文件 $2 目标目录 输出唯一可用目标路径 (不带动作)
resolve_dest() {
    local src="$1" dir="$2" base dest n=1
    base=$(basename "$src")
    dest="$dir/$base"
    while [ -e "$dest" ]; do
        dest="$dir/${base%.*}_$n.${base##*.}"
        n=$((n + 1))
    done
    echo "$dest"
}

# ============ 并发锁 (mkdir 原子 + pid 存活检查, bash 3.2 兼容) ============
acquire_lock() {
    if mkdir "$LOCK_DIR" 2>/dev/null; then
        echo $$ > "$LOCK_DIR/pid"
        return 0
    fi
    if [ -f "$LOCK_DIR/pid" ]; then
        local pid
        pid=$(cat "$LOCK_DIR/pid" 2>/dev/null)
        if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
            rm -rf "$LOCK_DIR" 2>/dev/null
            mkdir "$LOCK_DIR" 2>/dev/null && echo $$ > "$LOCK_DIR/pid" && return 0
        fi
    fi
    return 1
}
release_lock() { rm -rf "$LOCK_DIR" 2>/dev/null; }

# ============ 去重索引 (并行数组, 兼容 bash 3.2) ============
# 只索引视频; 大小相同的才可能是重复 → 采样指纹 → 全量 MD5, 与 MoveFiles.sh 同算法
index_sizes=()
index_paths=()
fp_cache_paths=()
fp_cache_vals=()
md5_cache_paths=()
md5_cache_vals=()

cached_fp() {
    local f="$1" j v
    for j in "${!fp_cache_paths[@]}"; do
        [ "${fp_cache_paths[$j]}" = "$f" ] && { echo "${fp_cache_vals[$j]}"; return 0; }
    done
    v=$(file_fast_fp "$f")
    fp_cache_paths+=("$f"); fp_cache_vals+=("$v")
    echo "$v"
}
cached_md5() {
    local f="$1" j v
    for j in "${!md5_cache_paths[@]}"; do
        [ "${md5_cache_paths[$j]}" = "$f" ] && { echo "${md5_cache_vals[$j]}"; return 0; }
    done
    v=$(file_md5 "$f")
    md5_cache_paths+=("$f"); md5_cache_vals+=("$v")
    echo "$v"
}

add_to_index() {
    local f="$1"
    is_video "$f" || return 0
    index_sizes+=("$(file_size "$f")")
    index_paths+=("$f")
}

build_index() {
    local dir="$1" f
    [ -d "$dir" ] || return 0
    while IFS= read -r -d '' f; do
        add_to_index "$f"
    done < <(find "$dir" -type f -print0 2>/dev/null)
}

# 输出与 $1 内容重复的已归档目标路径; 无则返回 1
find_duplicate() {
    [ "$DEDUP_MODE" = "hash" ] || return 1
    local f="$1" size fp_f md5_f t i
    size=$(file_size "$f")
    fp_f=$(file_fast_fp "$f")
    md5_f=""
    for i in "${!index_sizes[@]}"; do
        [ "${index_sizes[$i]}" = "$size" ] || continue
        t="${index_paths[$i]}"
        [ "$f" = "$t" ] && continue
        [ "$(cached_fp "$t")" = "$fp_f" ] || continue
        [ -n "$md5_f" ] || md5_f=$(file_md5 "$f")
        if [ "$(cached_md5 "$t")" = "$md5_f" ]; then
            echo "$t"; return 0
        fi
    done
    return 1
}

# ============ 归档动作 ============
# $1 源视频路径 $2 目标目录
archive_video() {
    local src="$1" tdir="$2" base dup dest
    base=$(basename "$src")

    # 已在目标目录 → 跳过 (幂等)
    if [ "$(dirname "$src")" = "$tdir" ]; then
        log "[跳过] $base 已在归档目录"
        return 0
    fi

    # 去重: 重复且 action=skip → 返回 1, 调用方不再跟随 sidekick
    if dup=$(find_duplicate "$src"); then
        case "$DEDUP_ACTION" in
            rename) log "[重复→保留] $base 与 [$dup] 内容相同, 仍归档" ;;
            *)      log "[重复→跳过] $base 与 [$dup] 内容相同"; return 1 ;;
        esac
    fi

    dest=$(resolve_dest "$src" "$tdir")
    if [ "$DRY_RUN" = "1" ]; then
        log "[演练] $([ "$MOVE_MODE" = "move" ] && echo 移动 || echo 复制) $src → $dest"
    elif [ "$MOVE_MODE" = "move" ]; then
        if mv "$src" "$dest" 2>>"$LOG_FILE"; then
            log "[移动] $src → $dest"
            add_to_index "$dest"
        else
            log "[!!] 移动失败: $src"
        fi
    else
        if cp -n "$src" "$dest" 2>>"$LOG_FILE"; then
            log "[复制] $src → $dest"
            add_to_index "$dest"
        else
            log "[!!] 复制失败: $src"
        fi
    fi
}

# sidekick 跟随: 同目录下与视频同 stem 的字幕/封面等 → 归档到视频同目录
# $1 视频路径 $2 视频目标目录
archive_sidekicks() {
    [ "$KEEP_SIDEKICK" = "1" ] || return 0
    local v="$1" tdir="$2" vdir vbase stem f base cand1 cand2 dest
    vdir=$(dirname "$v")
    vbase=$(basename "$v")
    stem="${vbase%.*}"
    [ "$stem" = "$vbase" ] && return 0
    for cand1 in "$vdir/$stem."* "$vdir/$vbase."*; do
        [ -f "$cand1" ] || continue
        base=$(basename "$cand1")
        is_sidekick "$base" || continue
        [ "$cand1" = "$v" ] && continue
        # 精确同 stem 或同全名 (如 movie.mp4.srt), 排除 movie_extra.srt 这类
        case "$base" in
            "$stem".*) ;;
            "$vbase".*) ;;
            *) continue ;;
        esac
        dest=$(resolve_dest "$cand1" "$tdir")
        if [ "$DRY_RUN" = "1" ]; then
            log "[演练] sidekick $cand1 → $dest"
        elif [ "$MOVE_MODE" = "move" ]; then
            mv "$cand1" "$dest" 2>>"$LOG_FILE" && log "[sidekick] $cand1 → $dest"
        else
            cp -n "$cand1" "$dest" 2>>"$LOG_FILE" && log "[sidekick] $cand1 → $dest"
        fi
    done
}

# ============ 主流程 ============
acquire_lock || { log "[!!] 已有实例在运行, 本次跳过"; exit 0; }
trap release_lock EXIT

mkdir -p "$ARCHIVE_BASE"
[ -d "$ARCHIVE_BASE" ] || { log "[!!] 无法创建归档目录 $ARCHIVE_BASE"; exit 1; }

log "== 开始归档: $CONTENT_PATH ${TORRENT_NAME:+($TORRENT_NAME)}${CATEGORY:+ [$CATEGORY]} mode=${MOVE_MODE}/${SORT_MODE}/${DEDUP_MODE}"

# 收集待归档视频列表
video_srcs=()
if [ -d "$CONTENT_PATH" ]; then
    while IFS= read -r -d '' f; do
        is_video "$(basename "$f")" && video_srcs+=("$f")
    done < <(find "$CONTENT_PATH" -type f -print0 2>/dev/null)
else
    is_video "$(basename "$CONTENT_PATH")" && video_srcs+=("$CONTENT_PATH")
fi

if [ ${#video_srcs[@]} -eq 0 ]; then
    log "== 没有找到视频文件, 跳过"
    exit 0
fi

build_index "$ARCHIVE_BASE"

for v in "${video_srcs[@]}"; do
    if [ "$SORT_MODE" = "code" ]; then
        sub=$(extract_code "$(basename "$v")")
        tdir="$ARCHIVE_BASE/$sub"
    else
        tdir="$ARCHIVE_BASE"
    fi
    mkdir -p "$tdir" 2>/dev/null
    archive_video "$v" "$tdir" || continue   # 重复/失败 → 不跟随 sidekick
    archive_sidekicks "$v" "$tdir"
done

log "== 归档完成 (${#video_srcs[@]} 个视频)"
