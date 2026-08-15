#!/bin/bash
#
# MoveFiles.sh — 文件扁平化移动 + 重复视频识别 (优化版)
#
# 用法:
#   ./MoveFiles.sh <源目录> [目标目录]
#     第一个参数: 需要整理文件的目录 fold_path
#     第二个参数: 文件移动的目标目录 target_fold, 不传则默认为源目录 (扁平化)
#
# 可选环境变量 (重复视频识别):
#   DEDUP_MODE   none | hash | meta | time   默认 hash
#     none  只做同名重命名, 不检测内容重复 (原脚本行为)
#     hash  先比文件大小、再比采样指纹, 识别"字节内容完全相同"的重复 (快)
#     meta  用 ffprobe 对比时长/分辨率, 提示"转码副本"级疑似重复
#     time  按视频时长均匀取 5 个时间点抽帧解码, 对齐时间戳对比像素 MD5,
#           可跨容器/封装识别同一内容 (如 mp4→mkv remux); 有损转码像素有差, 判不出 (MD5 边界)
#   DEDUP_ACTION skip | rename | trash     默认 skip
#     skip   重复文件不移动; 若某目录最终只剩被跳过的重复文件, 该"壳目录"会
#           连同文件整体移入废纸篓 (~/.Trash, 可恢复)。目录中只要还有任何
#           其他文件(非视频等)就保留, 不会误删。
#     rename 重复文件也移动, 并重命名保留 (原脚本行为)
#     trash  重复文件直接移入 ~/.Trash 废纸篓 (谨慎!)
#   TRASH_OTHERS 0 | 1                      默认 1
#     非视频文件(如 txt/图片/说明文档)移入废纸篓, 目标目录只保留纯视频;
#     设 TRASH_OTHERS=0 恢复旧行为 (非视频文件平级移动到目标目录)
#
# 依赖: md5 / md5sum (系统自带); meta/time 模式额外需要 ffmpeg + ffprobe
#
# 说明: 重复识别只针对视频文件 (mp4/mov/mkv 等), 其他文件仍按原逻辑处理。
#       三步走: ①按"文件大小"分组 (秒级) → ②采样指纹比对, 只读头/尾各 1MB (毫秒级)
#       → ③采样相同才做全量 MD5 确认 (秒级, 极少触发)。大视频不再全量哈希。

set -u   # 变量未定义时报错, 帮助发现拼写问题

# ============ 配置 ============
DEDUP_MODE="${DEDUP_MODE:-hash}"
DEDUP_ACTION="${DEDUP_ACTION:-skip}"
TRASH_OTHERS="${TRASH_OTHERS:-1}"   # 非视频文件移入废纸篓, 目标目录只留纯视频
VIDEO_EXTS="mp4 m4v mov avi mkv webm flv wmv ts mpg mpeg 3gp rmvb rm"
SAMPLE_POINTS=5       # time 模式: 按时长均匀抽帧的个数
DUR_TOLERANCE=0.5     # time 模式: 时长匹配容差(秒), 兼容不同容器的时间基误差

if [ $# -lt 1 ]; then
    echo "用法: $0 <源目录> [目标目录]"
    echo "环境变量: DEDUP_MODE=none|hash|meta  DEDUP_ACTION=skip|rename|trash"
    exit 1
fi

fold_path="$1"
if [ -z "${2:-}" ]; then
    target_fold="$1"
else
    target_fold="$2"
fi

if [ ! -d "$fold_path" ]; then
    echo "!! 源目录不存在: $fold_path"
    exit 1
fi

echo "==> 源目录 : $fold_path"
echo "==> 目标目录: $target_fold"
echo "==> 去重模式: $DEDUP_MODE (重复文件处理: $DEDUP_ACTION)"

# ============ 工具函数 ============

# 判断文件是否为视频 (按扩展名, 忽略大小写)
is_video() {
    local name="$1" ext
    ext="${name##*.}"
    ext=$(echo "$ext" | tr '[:upper:]' '[:lower:]')
    case " $VIDEO_EXTS " in
        *" $ext "*) return 0 ;;
        *)        return 1 ;;
    esac
}

# 获取文件字节大小 (兼容 macOS stat -f 与 Linux stat -c)
file_size() {
    stat -f %z "$1" 2>/dev/null || stat -c %s "$1" 2>/dev/null || echo "0"
}

# 获取文件 MD5 (兼容 macOS md5 与 Linux md5sum)
file_md5() {
    if command -v md5 >/dev/null 2>&1; then
        md5 -q "$1" 2>/dev/null
    else
        md5sum "$1" 2>/dev/null | awk '{print $1}'
    fi
}

# 对 stdin 做 MD5 (供采样用)
hash_stdin() {
    if command -v md5 >/dev/null 2>&1; then
        md5 -q 2>/dev/null
    else
        md5sum 2>/dev/null | awk '{print $1}'
    fi
}

# 快速指纹: 大小 + 头/尾各 1MB 的哈希。
#   大视频全量 MD5 要读整个文件 (1GB 约 1~3 秒), 采样只读 2MB (毫秒级),
#   只有采样指纹相同的文件才值得做全量 MD5 确认, 速度提升约百倍。
#   小文件 (<= 2MB) 直接全量, 省去采样开销。
SAMPLE_BYTES=1048576

file_fast_fp() {
    local f="$1" size head_hash tail_hash
    size=$(file_size "$f")
    if [ "$size" -le $((SAMPLE_BYTES * 2)) ]; then
        echo "$size|$(file_md5 "$f")"
        return 0
    fi
    head_hash=$(dd if="$f" bs=1048576 count=1 2>/dev/null | hash_stdin)
    # 注: 用 dd 定位读尾部而非 tail -c —— macOS 的 tail -c 实现较慢 (实测慢 5 倍)
    tail_hash=$(dd if="$f" bs=1048576 skip=$(((size - 1) / 1048576)) count=1 2>/dev/null | hash_stdin)
    echo "$size|$head_hash|$tail_hash"
}

# 视频元数据指纹: 时长,宽,高 (识别"转码副本"用, 需 ffprobe)
#   注: ffprobe 只读容器头部 (mp4 的 moov atom), 不是全文件扫描,
#   1GB 视频通常也只要几十毫秒, 不是性能瓶颈。
file_meta_fp() {
    command -v ffprobe >/dev/null 2>&1 || return 1
    ffprobe -v error -select_streams v:0 \
        -show_entries stream=duration,width,height \
        -of csv=p=0 "$1" 2>/dev/null
}

# 视频时长 (ffprobe, time 模式粗筛用)
video_dur() {
    command -v ffprobe >/dev/null 2>&1 || return 1
    ffprobe -v error -show_entries format=duration -of csv=p=0 "$1" 2>/dev/null
}

# 按时间戳均匀抽 SAMPLE_POINTS 帧, 输出每帧像素的 MD5 (以 | 连接)。
#   时间点取 dur/(n+1), 2*dur/(n+1), ..., n*dur/(n+1), 首尾各留一档边距。
#   思路: 视频是 VBR 编码, 字节偏移与播放时间非线性对应,
#   要"对齐两个视频的时间戳"必须按时间点抽帧解码, 再对像素做 MD5。
#   坑 (实测): 小数秒时间戳在 mp4/mkv 间会因容器时间基 (edit list) 差 1 帧,
#   因此采样点四舍五入到"整数秒"; 短视频去重后不足 2 点则放弃判定 (保守)。
#   注: 抽的是解码后的像素字节, 能跨容器/封装对齐 (如 mp4→mkv remux);
#   但对有损转码 (x264 重压) 会因量化误差导致像素不同, MD5 判不出来 —— 这是 MD5 的边界。
video_frames_fp() {
    command -v ffmpeg >/dev/null 2>&1 || return 1
    local f="$1" dur out="" h i t prev="" pts=0
    dur=$(video_dur "$f")
    [ -n "$dur" ] || return 1
    for i in $(seq 1 "$SAMPLE_POINTS"); do
        t=$(awk -v d="$dur" -v n="$SAMPLE_POINTS" -v i="$i" \
            'BEGIN{ x = d*i/(n+1); y = int(x+0.5); if (y < 1) y = 1; if (y > d-1) y = int(d-1); print y }')
        [ "$t" != "$prev" ] || continue
        prev="$t"
        h=$(ffmpeg -v error -ss "$t" -i "$f" -frames:v 1 -f rawvideo -pix_fmt gray - 2>/dev/null | hash_stdin)
        [ -n "$h" ] || return 1
        out="${out:+$out|}$h"
        pts=$((pts + 1))
    done
    [ "$pts" -ge 2 ] || return 1
    echo "$out"
}

# ============ 重复识别 ============

# 目标目录索引: 并行数组记录 "文件大小" 与 "文件路径"。
#   只索引视频文件; 大小相同的文件才有可能是重复,
#   这一步避免了对所有文件做 MD5 (大视频全量哈希很慢)。
# 注: 不用关联数组, 保证兼容 macOS 自带的 bash 3.2。
index_sizes=()
index_paths=()

# meta 模式额外维护一套 "元数据指纹 -> 路径" 索引 (不依赖大小,
# 因为转码副本的字节大小必然不同)
meta_fps=()
meta_paths=()

# time 模式: 并行数组记录每个目标视频的时长 (先按时长粗筛, 避免对所有文件抽帧)
durs=()

# 哈希缓存: 同一个文件只算一次, 避免多源文件与同一目标反复比较时重复计算
fp_cache_paths=()
fp_cache_vals=()
md5_cache_paths=()
md5_cache_vals=()
frames_cache_paths=()
frames_cache_vals=()

cached_fast_fp() {
    local f="$1" j v
    for j in "${!fp_cache_paths[@]}"; do
        [ "${fp_cache_paths[$j]}" = "$f" ] && { echo "${fp_cache_vals[$j]}"; return 0; }
    done
    v=$(file_fast_fp "$f")
    fp_cache_paths+=("$f")
    fp_cache_vals+=("$v")
    echo "$v"
}

cached_md5() {
    local f="$1" j v
    for j in "${!md5_cache_paths[@]}"; do
        [ "${md5_cache_paths[$j]}" = "$f" ] && { echo "${md5_cache_vals[$j]}"; return 0; }
    done
    v=$(file_md5 "$f")
    md5_cache_paths+=("$f")
    md5_cache_vals+=("$v")
    echo "$v"
}

cached_frames_fp() {
    local f="$1" j v
    for j in "${!frames_cache_paths[@]}"; do
        [ "${frames_cache_paths[$j]}" = "$f" ] && { echo "${frames_cache_vals[$j]}"; return 0; }
    done
    v=$(video_frames_fp "$f")
    frames_cache_paths+=("$f")
    frames_cache_vals+=("$v")
    echo "$v"
}

# 把视频文件加入索引
add_to_index() {
    local f="$1" size fp
    is_video "$f" || return 0
    size=$(file_size "$f")
    index_sizes+=("$size")
    index_paths+=("$f")
    if [ "$DEDUP_MODE" = "meta" ]; then
        fp=$(file_meta_fp "$f")
        meta_fps+=("$fp")
        meta_paths+=("$f")
    fi
    if [ "$DEDUP_MODE" = "time" ]; then
        durs+=("$(video_dur "$f")")
    fi
}

# 构建目标目录的初始索引 (扫描已有文件)
build_index() {
    local dir="$1" f
    [ -d "$dir" ] || return 0
    while IFS= read -r -d '' f; do
        add_to_index "$f"
    done < <(find "$dir" -type f -print0)
}

# 内容级重复: 大小相同 → 快速指纹相同 → 全量 MD5 确认 → 确定重复
# 成功时输出与之重复的目标文件路径
is_duplicate() {
    local f="$1" size fp_f md5_f t i
    size=$(file_size "$f")
    fp_f=$(file_fast_fp "$f")   # 只读 1MB 头 + 1MB 尾
    md5_f=""                    # 懒计算: 只有候选通过采样筛选才做全量哈希
    for i in "${!index_sizes[@]}"; do
        [ "${index_sizes[$i]}" = "$size" ] || continue            # ① 比大小 (微秒)
        t="${index_paths[$i]}"
        [ "$f" = "$t" ] && continue
        [ "$(cached_fast_fp "$t")" = "$fp_f" ] || continue        # ② 采样指纹 (毫秒)
        [ -n "$md5_f" ] || md5_f=$(file_md5 "$f")                 # ③ 全量确认 (秒级, 极少触发)
        if [ "$(cached_md5 "$t")" = "$md5_f" ]; then
            echo "$t"
            return 0
        fi
    done
    return 1
}

# 疑似重复: 元数据指纹 (时长,宽,高) 相同, 可能是转码、压缩后的副本
is_likely_duplicate() {
    command -v ffprobe >/dev/null 2>&1 || return 1
    local f="$1" fp_f t i
    fp_f=$(file_meta_fp "$f")
    [ -n "$fp_f" ] || return 1

    for i in "${!meta_fps[@]}"; do
        [ "${meta_fps[$i]}" = "$fp_f" ] || continue
        t="${meta_paths[$i]}"
        [ "$f" = "$t" ] && continue
        echo "$t"
        return 0
    done
    return 1
}

# time 模式: 按时长粗筛 (容差 DUR_TOLERANCE), 同时长候选抽 5 帧
#   对齐时间戳对比像素 MD5, 全同 → 内容相同
is_time_duplicate() {
    command -v ffmpeg >/dev/null 2>&1 || return 1
    local f="$1" dur fp_f t i ok
    dur=$(video_dur "$f")
    [ -n "$dur" ] || return 1
    fp_f=$(video_frames_fp "$f")     # 源文件抽帧 (懒计算, 只有此文件一次)
    [ -n "$fp_f" ] || return 1

    for i in "${!durs[@]}"; do
        ok=$(awk -v a="${durs[$i]}" -v b="$dur" -v tol="$DUR_TOLERANCE" \
            'BEGIN{ if (a=="" || b=="") print 0; else print (a-b<tol && b-a<tol) ? 1 : 0 }')
        [ "$ok" = "1" ] || continue
        t="${index_paths[$i]}"
        [ "$f" = "$t" ] && continue
        [ "$(cached_frames_fp "$t")" = "$fp_f" ] || continue   # 5 个时间点像素全同
        echo "$t"
        return 0
    done
    return 1
}

# ============ 移动逻辑 ============

move_count=0
dup_count=0
likely_count=0
other_count=0
skipped_paths=()   # skip 模式下被判定为重复而留下的文件, 供"壳目录"清理判断

# 移入废纸篓 (同名冲突自动加 _1/_2 序号, 避免覆盖/失败)
# $1 文件或目录路径  $2 建议名
trash_to() {
    local src="$1" name="$2" dest="$HOME/.Trash/$2" n=1
    while [ -e "$dest" ]; do
        dest="$HOME/.Trash/${name}_$n"
        n=$((n + 1))
    done
    mv "$src" "$dest" 2>/dev/null
}

# 处理"判定为重复"的文件 (hash/time 模式共用)
# $1 源路径  $2 源文件名  $3 与之重复的目标路径  $4 提示标签
handle_duplicate() {
    local src_path="$1" src_name="$2" dup_to="$3" tag="$4"
    local dest_path="$target_fold/$src_name"
    dup_count=$((dup_count + 1))
    case "$DEDUP_ACTION" in
        trash)
            echo "[$tag] $src_name 与 [$dup_to] 内容相同 → 移入废纸篓"
            trash_to "$src_path" "$src_name" \
                || echo "  !! 移入废纸篓失败, 文件保留: $src_path"
            ;;
        rename)
            echo "[$tag] $src_name 与 [$dup_to] 内容相同 → 仍移动并重命名"
            mv "$src_path" "${dest_path}_dup"
            add_to_index "${dest_path}_dup"
            ;;
        *)
            echo "[$tag] $src_name 与 [$dup_to] 内容相同 → 跳过 (目录清理时连同壳目录一并处理)"
            skipped_paths+=("$src_path")
            ;;
    esac
}

# $1 文件所在目录  $2 文件名
moveFile() {
    local src_dir="$1" src_name="$2"
    local src_path="$src_dir/$src_name"
    local dest_path="$target_fold/$src_name"

    # 文件已经在目标目录里, 无需移动
    [ "$src_dir" = "$target_fold" ] && return 0

    # ---- 非视频文件: 默认移入废纸篓 (目标目录只保留纯视频) ----
    if ! is_video "$src_name"; then
        if [ "$TRASH_OTHERS" = "1" ]; then
            echo "[清理] 非视频文件 $src_name → 移入废纸篓"
            if trash_to "$src_path" "$src_name"; then
                other_count=$((other_count + 1))
            else
                echo "  !! 移入废纸篓失败: $src_path"
            fi
        else
            # TRASH_OTHERS=0: 恢复旧行为, 平级移动到目标目录
            if [ -f "$dest_path" ]; then
                mv "$src_path" "${dest_path}_1" 2>/dev/null
            else
                mv "$src_path" "$target_fold" 2>/dev/null
            fi
            move_count=$((move_count + 1))
        fi
        return 0
    fi

    # ---- 重复视频识别 ----
    if [ "$DEDUP_MODE" != "none" ] && is_video "$src_name"; then
        local dup_to
        if [ "$DEDUP_MODE" = "time" ]; then
            if dup_to=$(is_time_duplicate "$src_path"); then
                handle_duplicate "$src_path" "$src_name" "$dup_to" "重复(帧采样)"
                return 0
            fi
        else
            if dup_to=$(is_duplicate "$src_path"); then
                handle_duplicate "$src_path" "$src_name" "$dup_to" "重复"
                return 0
            fi
            if [ "$DEDUP_MODE" = "meta" ]; then
                local likely_to
                if likely_to=$(is_likely_duplicate "$src_path"); then
                    likely_count=$((likely_count + 1))
                    echo "[疑似] $src_name 与 [$likely_to] 时长/分辨率相同, 可能是转码副本"
                    if [ "$DEDUP_ACTION" != "rename" ]; then
                    if [ "$DEDUP_ACTION" = "trash" ]; then
                        trash_to "$src_path" "$src_name" 2>/dev/null
                    fi
                    return 0
                    fi
                fi
            fi
        fi
    fi

    # ---- 同名文件处理 (原逻辑) ----
    if [ -f "$dest_path" ]; then
        echo "[重名] $src_name → ${src_name}_1"
        if mv "$src_path" "${dest_path}_1" 2>/dev/null; then
            add_to_index "${dest_path}_1"
            move_count=$((move_count + 1))
        else
            echo "  !! 移动失败: $src_path"
        fi
    else
        if mv "$src_path" "$target_fold" 2>/dev/null; then
            add_to_index "$dest_path"   # 用目标路径入索引, 供后续文件去重比对
            move_count=$((move_count + 1))
        else
            echo "  !! 移动失败: $src_path"
        fi
    fi
}

# ============ 目录遍历 ============

# 判断目录是否为"壳目录": 非隐藏文件全部是被跳过的重复文件。
#   满足条件 → 目录(连同其中仅存的重复文件)整体移入废纸篓。
#   保守规则: 只要还有任何其他文件(非视频/未判定的), 一律保留;
#   只含隐藏文件(.DS_Store 等)也不算壳目录。
is_shell_dir() {
    local dir="$1" f all_skipped=1 has_visible=0 j found
    while IFS= read -r f; do
        [ -z "$f" ] && continue
        has_visible=1
        found=0
        for j in "${!skipped_paths[@]}"; do
            [ "${skipped_paths[$j]}" = "$f" ] && { found=1; break; }
        done
        if [ "$found" = 0 ]; then
            all_skipped=0
            break
        fi
    done < <(find "$dir" -mindepth 1 -maxdepth 1 ! -name '.*' 2>/dev/null)
    [ "$all_skipped" = 1 ] && [ "$has_visible" = 1 ]
}

getAllDir() {
    local dir="$1" IFS=$'\n' file dir_or_file

    for file in $(ls "$dir"); do
        dir_or_file="$dir/$file"
        if [ -d "$dir_or_file" ]; then
            getAllDir "$dir_or_file"
        elif [ -f "$dir_or_file" ]; then
            moveFile "$dir" "$file"
        else
            echo "$dir_or_file 非文件非目录, 跳过"
        fi
    done

    # 目录清理: 根目录与目标目录除外
    if [ "$dir" != "$fold_path" ] && [ "$dir" != "$target_fold" ]; then
        if [ "$(find "$dir" -mindepth 1 2>/dev/null | wc -l)" -eq 0 ]; then
            echo "==> 空目录 $dir 移入废纸篓"
            trash_to "$dir" "$(basename "$dir")" || echo "  !! 移入废纸篓失败: $dir"
        elif is_shell_dir "$dir"; then
            echo "==> 目录仅剩重复文件, 连同文件整体移入废纸篓: $dir"
            trash_to "$dir" "$(basename "$dir")" || echo "  !! 移入废纸篓失败: $dir"
        fi
    fi
}

# ============ 执行 ============

build_index "$target_fold"
getAllDir "$fold_path"

echo
echo "==== 汇总 ===="
echo "已移动视频: $move_count 个 | 内容重复: $dup_count | 疑似转码重复: $likely_count | 清理非视频: $other_count"
