#!/usr/bin/env bash
# 一键生成手写笔记：把 .md 放进 笔记/ 目录后运行本脚本
# 也可以传参指定文件：bash 一键生成.sh 我的笔记.md
cd "$(dirname "$0")"

mkdir -p 笔记

files=()
if [ $# -gt 0 ]; then
    files=("$@")
else
    shopt -s nullglob
    for f in 笔记/*.md; do files+=("$f"); done
fi

if [ ${#files[@]} -eq 0 ]; then
    echo "没有找到笔记。请把 .md 文件放进 笔记/ 目录，或这样运行："
    echo "  bash 一键生成.sh 我的笔记.md"
    exit 1
fi

python3 md2handwrite.py "${files[@]}" || { echo "生成失败，请检查依赖：bash install.sh"; exit 1; }

echo "全部完成，输出在 手写版/ 目录"
xdg-open 手写版 2>/dev/null || open 手写版 2>/dev/null || true
