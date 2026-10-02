#!/usr/bin/env bash
# 一键安装依赖（Ubuntu/Debian）
set -e
cd "$(dirname "$0")"

echo "[1/3] 检查 wkhtmltopdf..."
if ! command -v wkhtmltopdf >/dev/null 2>&1; then
    echo "  需要安装 wkhtmltopdf：sudo apt install wkhtmltopdf"
    echo "  （或手动安装后重跑本脚本）"
else
    echo "  已安装：$(command -v wkhtmltopdf)"
fi

echo "[2/3] 安装 Python 依赖 pypdf reportlab pillow pyyaml..."
if pip3 install --user -q -i https://pypi.tuna.tsinghua.edu.cn/simple pypdf reportlab pillow pyyaml 2>/dev/null \
   || pip3 install --user -q --break-system-packages -i https://pypi.tuna.tsinghua.edu.cn/simple pypdf reportlab pillow pyyaml 2>/dev/null \
   || pip3 install --user -q pypdf reportlab pillow pyyaml; then
    echo "  Python 依赖就绪"
else
    echo "  安装失败，请手动执行：pip3 install --user pypdf reportlab pillow pyyaml"
fi

echo "[3/3] 注册字体到系统（供 CSS 回退使用）..."
mkdir -p ~/.local/share/fonts
cp fonts/*.ttf ~/.local/share/fonts/ 2>/dev/null || true
fc-cache -f ~/.local/share/fonts >/dev/null 2>&1 || true

echo "依赖安装完成。把 .md 放进 笔记/ 目录，然后运行：bash 一键生成.sh"
