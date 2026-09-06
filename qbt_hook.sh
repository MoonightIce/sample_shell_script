#!/bin/sh
# qBittorrent 完成钩子 wrapper — 按分类动态映射归档目录 (容器内视角)
# 调用: /config/scripts/qbt_hook.sh "%F" "%N" "%L" "%I"
#   $1=%F 内容路径  $2=%N 种子名  $3=%L 分类  $4=%I InfoHash
# 归档目录 = qBittorrent 分类保存路径 (来自 /config/qBittorrent/categories.json),
# 无分类/未知分类 fallback 到 /video/movies。

CAT_FILE=/config/qBittorrent/categories.json
LOG_DIR=/config/logs

cat_path=""
if [ -n "${3:-}" ] && [ -f "$CAT_FILE" ]; then
    cat_path=$(awk -v cat="$3" '
        $0 ~ "\"" cat "\"" {found=1; next}
        found && /save_path/ {gsub(/.*save_path"[[:space:]]*:[[:space:]]*"/, ""); gsub(/".*$/, ""); print; exit}
    ' "$CAT_FILE" 2>/dev/null)
fi

export ARCHIVE_BASE="${cat_path:-/downloads}"
export LOG_DIR
exec /config/scripts/nas_qbt_archive.sh "$@"
