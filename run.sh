#!/usr/bin/env bash
# run.sh — 一键启动采集（MITM 代理 + 主程序）
#
# 用法：
#   ./run.sh                          # 正常采集（断点续传）
#   ./run.sh --test 5                 # 测试模式，仅采集前 5 条
#   ./run.sh --update-list data/retry_failed.txt   # 强制重采指定列表
#   ./run.sh collect-fwxx             # 发文补采（从 detection_log 筛选）
#   ./run.sh collect-fwxx --input data/retry_fwxx.txt  # 发文补采（指定列表）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

MITM_PORT="${MITM_PORT:-8082}"
MITM_LOG="$SCRIPT_DIR/.mitm.log"

# ── 清理函数：脚本退出时杀掉后台代理 ───────────────────────────
cleanup() {
    if [[ -n "${MITM_PID:-}" ]] && kill -0 "$MITM_PID" 2>/dev/null; then
        echo ""
        echo "[*] 停止 MITM 代理（PID ${MITM_PID}）..."
        kill "$MITM_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

# ── 启动 MITM 代理（后台）────────────────────────────────────────
echo "============================================================"
echo "▶  启动 MITM 代理（端口 ${MITM_PORT}）..."
echo "============================================================"

uv run python start_mitm_proxy.py > "$MITM_LOG" 2>&1 &
MITM_PID=$!

# 等待代理就绪（最多 10 秒）
for i in $(seq 1 10); do
    if grep -q "Serving on\|Proxy server listening\|proxy" "$MITM_LOG" 2>/dev/null; then
        echo "[✓] MITM 代理已就绪"
        break
    fi
    sleep 1
done

echo ""

# ── 派发子命令 ────────────────────────────────────────────────────
MODE="${1:-main}"

case "$MODE" in
    collect-fwxx)
        shift
        echo "▶  启动发文补采..."
        USE_MITM_PROXY=true uv run python collect_fwxx.py "$@"
        ;;
    --test|--update-list|main)
        echo "▶  启动主采集..."
        USE_MITM_PROXY=true uv run python main_automation.py "$@"
        ;;
    *)
        echo "▶  启动主采集..."
        USE_MITM_PROXY=true uv run python main_automation.py "$@"
        ;;
esac
