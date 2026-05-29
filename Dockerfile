FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    DISPLAY=:99 \
    USE_VIRTUAL_DISPLAY=true \
    VIRTUAL_DISPLAY_WIDTH=1920 \
    VIRTUAL_DISPLAY_HEIGHT=1080 \
    XVFB_EXTERNAL=true \
    USE_MITM_PROXY=true \
    MITM_HOST=127.0.0.1 \
    MITM_PORT=8083 \
    PYAUTOGUI_PAUSE=0.03 \
    PYAUTOGUI_FAILSAFE=false

# 系统依赖：Python、Xvfb、X11 库、中文字体、git、curl
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-dev \
        xvfb x11-utils \
        libx11-6 libxss1 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
        libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libdbus-1-3 \
        libgbm1 libasound2 libnspr4 libnss3 libpango-1.0-0 libpangocairo-1.0-0 \
        libxkbcommon0 libgtk-3-0 \
        fonts-liberation fonts-noto-cjk \
        wget gnupg ca-certificates curl git \
    && rm -rf /var/lib/apt/lists/*

# Google Chrome（undetected_chromedriver 与 chromium 兼容性差，必须用官方 Chrome）
RUN wget -qO- https://dl.google.com/linux/linux_signing_key.pub \
        | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] \
        http://dl.google.com/linux/chrome/deb/ stable main" \
        > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先复制依赖声明，利用 Docker 层缓存
COPY requirements.txt pyproject.toml ./
RUN pip3 install --no-cache-dir -e .

# 复制应用代码
COPY . .

# 初始化数据目录结构（实际数据由卷挂载提供）
RUN mkdir -p data/results data/raw_responses data/raw_searches

# entrypoint：启动 Xvfb 后再启动 Dashboard
RUN chmod +x docker-entrypoint.sh

EXPOSE 8765

VOLUME ["/app/data"]

ENTRYPOINT ["./docker-entrypoint.sh"]
