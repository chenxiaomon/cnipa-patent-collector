#!/bin/zsh
set -eu

DESKTOP_PROJECT_DIR="${0:A:h}"
DESKTOP_PYTHON="$DESKTOP_PROJECT_DIR/.venv/bin/python"
cd -- "$DESKTOP_PROJECT_DIR"

if [[ ! -x "$DESKTOP_PYTHON" ]]; then
    print -u2 -- "项目环境不存在，请先在项目目录运行：uv sync --python 3.11 --extra desktop"
    read -r "?按 Enter 关闭窗口。"
    exit 1
fi

exec "$DESKTOP_PYTHON" "$DESKTOP_PROJECT_DIR/desktop_dashboard.py" "$@"
