#!/usr/bin/env bash
# ============================================================
# 依赖安装（Ubuntu/Debian）
# 原则：默认只检测不改动；凡是要往某个 Python 环境装包、或新建
# conda/venv 环境，都会先说明并获得确认（--yes 可跳过询问）。
# 用法:
#   bash install.sh            交互模式（推荐）
#   bash install.sh --yes      非交互，自动安装到当前 python3
#   bash install.sh --conda    询问并创建/使用独立 conda 环境
# ============================================================
set -u
cd "$(dirname "$0")"
YES=0; WANT_CONDA=0
for a in "$@"; do
    case "$a" in
        --yes) YES=1 ;;
        --conda) WANT_CONDA=1 ;;
    esac
done

ask() {   # ask "问题" 默认值(y/n) → 返回 0 表示同意
    if [ "$YES" = 1 ]; then echo "  (—自动同意) $1"; return 0; fi
    read -r -p "$1 [$2] " r
    [ -z "$r" ] && r="$2"
    [ "$r" = "y" ] || [ "$r" = "Y" ]
}

echo "=== 手写笔记生成 - 依赖安装 ==="
PY="python3"
echo "[1/4] 当前 Python 环境"
$PY -c "import sys; print('  解释器:', sys.executable); print('  版本:', sys.version.split()[0])"
if [ -n "${CONDA_DEFAULT_ENV:-}" ]; then
    echo "  ⚠ 这是 conda 环境: $CONDA_DEFAULT_ENV"
fi
if $PY -c "import sys; sys.exit(0 if sys.prefix != sys.base_prefix else 1)"; then
    echo "  ⚠ 这是 venv 虚拟环境"
fi

echo "[2/4] wkhtmltopdf（PDF 渲染引擎）"
if command -v wkhtmltopdf >/dev/null 2>&1; then
    echo "  已安装: $(command -v wkhtmltopdf)"
else
    echo "  缺少。安装需要 sudo：sudo apt install wkhtmltopdf"
    if ask "现在执行 sudo apt install wkhtmltopdf ?" y && sudo apt install -y wkhtmltopdf; then
        echo "  已安装"
    else
        echo "  跳过。之后请手动安装，否则无法生成 PDF。"
    fi
fi

echo "[3/4] Python 包（pypdf reportlab pillow pyyaml markdown）"
if $PY -c "import pypdf, reportlab, PIL, yaml, markdown" 2>/dev/null; then
    echo "  已全部就绪"
else
    missing=$($PY -c "
for m in ('pypdf','reportlab','PIL','yaml','markdown'):
    try: __import__(m)
    except ImportError: print(m, end=' ')")
    echo "  缺少: $missing"
    if [ "$WANT_CONDA" = 1 ] && command -v conda >/dev/null 2>&1; then
        echo "  检测到 conda。建议装进独立环境 handwrite-notes（不污染现有环境）。"
        if ask "创建并使用 conda 环境 handwrite-notes (python3.11) ?" y; then
            conda create -n handwrite-notes python=3.11 -y
            PY="$(conda info --base)/envs/handwrite-notes/bin/python"
        fi
    else
        echo "  将安装到上面显示的当前 Python 环境（不改其他环境）。"
    fi
    if [ "$WANT_CONDA" = 1 ] && [ "$PY" = "python3" ]; then
        echo "  未找到 conda 命令，回退当前环境。"
    fi
    if ask "确认把 $missing 安装到 ${PY} 所在环境 ?" y; then
        if ! $PY -m pip install -q -i https://pypi.tuna.tsinghua.edu.cn/simple $missing \
           && ! $PY -m pip install -q --break-system-packages -i https://pypi.tuna.tsinghua.edu.cn/simple $missing \
           && ! $PY -m pip install -q $missing; then
            echo "  安装失败。请手动执行: $PY -m pip install $missing"
        else
            echo "  已安装"
        fi
    else
        echo "  跳过。可稍后手动: $PY -m pip install $missing"
    fi
fi

echo "[4/4] 注册字体到用户字体目录（可选，供其他软件预览）"
if ask "复制 fonts/*.ttf 到 ~/.local/share/fonts ?" y; then
    mkdir -p ~/.local/share/fonts
    cp fonts/*.ttf ~/.local/share/fonts/ 2>/dev/null || true
    fc-cache -f ~/.local/share/fonts >/dev/null 2>&1 || true
    echo "  已注册"
fi

$PY -c "import pypdf, reportlab, PIL, yaml, markdown" 2>/dev/null && command -v wkhtmltopdf >/dev/null 2>&1 \
    && echo "=== 全部就绪。生成: python3 md2handwrite.py 笔记.md -o 输出目录 ===" \
    || echo "=== 尚有缺失项，按上面提示处理后重跑 bash install.sh ==="
