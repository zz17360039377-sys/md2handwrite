#!/usr/bin/env bash
# ============================================================
# 手写笔记一键生成 v2（健壮版）
# 用法:
#   ./generate.sh                     转换 笔记/ 目录下所有 .md
#   ./generate.sh 我的笔记.md         转换指定文件（可多个）
#   ./generate.sh --seed-salt v2      换一批随机笔误/弯曲/溅点
# 全部输出与日志同步写入 logs/generate_时间戳.log
# ============================================================
set -u
cd "$(dirname "$0")"

LOG_DIR=logs
mkdir -p "$LOG_DIR" 手写版
LOG="$LOG_DIR/generate_$(date +%Y%m%d_%H%M%S).log"

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

log "=== 手写笔记生成开始（v2 管线）==="

# ---------- 1) 依赖检查 ----------
fail=0
for cmd in python3 wkhtmltopdf; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        log "[错误] 缺少命令: $cmd"
        [ "$cmd" = wkhtmltopdf ] && log "       安装: sudo apt install wkhtmltopdf"
        fail=1
    fi
done
for mod in yaml markdown pypdf reportlab PIL; do
    if ! python3 -c "import $mod" >/dev/null 2>&1; then
        log "[错误] 缺少 Python 模块: $mod"
        log "       安装: pip3 install --user --break-system-packages $mod"
        fail=1
    fi
done
[ "$fail" = 1 ] && { log "依赖缺失，已中止。补装后重新运行本脚本"; exit 1; }
log "依赖检查通过（python3 / wkhtmltopdf / 全部 Python 模块）"

# ---------- 2) 资源检查 ----------
[ -f config.yaml ] || { log "[错误] 缺少 config.yaml"; exit 1; }
FONT_CFG=$(python3 -c "import yaml;print(yaml.safe_load(open('config.yaml'))['font']['path'])" 2>/dev/null || echo "")
if [ -n "$FONT_CFG" ] && [ ! -f "$FONT_CFG" ]; then
    log "[警告] 配置的字体不存在: $FONT_CFG（将回退到系统已装手写字体）"
fi
[ -f assets/cs_watermark.png ] && log "水印: assets/cs_watermark.png" \
                                 || log "[警告] 未找到水印 assets/cs_watermark.png（输出将无水印页码）"

# ---------- 3) 收集笔记 ----------
shopt -s nullglob
notes=()
if [ $# -gt 0 ]; then
    for f in "$@"; do
        if [ -f "$f" ]; then notes+=("$f")
        else log "[错误] 文件不存在: $f"; exit 1; fi
    done
else
    for f in 笔记/*.md; do notes+=("$f"); done
fi
if [ ${#notes[@]} = 0 ]; then
    log "没有待转换的 .md：把文件放进 笔记/ 目录，或 ./generate.sh 文件.md"
    exit 1
fi
log "待转换 ${#notes[@]} 份: ${notes[*]}"

# ---------- 4) 生成（stdout+stderr 全部落日志）----------
python3 -u md2handwrite.py "${notes[@]}" 2>&1 | tee -a "$LOG"
rc=${PIPESTATUS[0]}
if [ "$rc" != 0 ]; then
    log "[错误] 生成失败（退出码 $rc），完整日志: $LOG"
    exit "$rc"
fi

# ---------- 5) 结果 ----------
log "=== 完成 ==="
shopt -s nullglob
pdfs=(手写版/*.pdf)
log "输出 ${#pdfs[@]} 份 PDF:"
for f in "${pdfs[@]}"; do log "  $f"; done
log "完整日志: $LOG"
