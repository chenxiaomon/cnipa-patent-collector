#!/bin/bash
set -e

# 启动 Xvfb 虚拟显示器（持久后台进程）
# -ac 禁用访问控制，让容器内所有进程都能连接
Xvfb :99 -screen 0 "${VIRTUAL_DISPLAY_WIDTH:-1920}x${VIRTUAL_DISPLAY_HEIGHT:-1080}x24" -ac &

export DISPLAY=:99
export XVFB_EXTERNAL=true   # 通知采集脚本 Xvfb 已由外部管理，不要重复启动

# 等待 Xvfb 就绪（每 0.5 秒检查一次，最多 5 秒）
for i in $(seq 1 10); do
    xdpyinfo -display :99 >/dev/null 2>&1 && break
    sleep 0.5
done

echo "[entrypoint] Xvfb :99 已就绪 (${VIRTUAL_DISPLAY_WIDTH:-1920}x${VIRTUAL_DISPLAY_HEIGHT:-1080})"

# 启动 Dashboard（exec 使 Dashboard 成为 PID 1，信号能正确传递）
exec python3 web_dashboard.py
