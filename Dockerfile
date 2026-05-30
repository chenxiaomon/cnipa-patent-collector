FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    MITM_HOST=127.0.0.1 \
    MITM_PORT=8083

# 系统依赖：Python、字体、git、curl
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-dev \
        fonts-liberation fonts-noto-cjk \
        wget gnupg ca-certificates curl git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先复制依赖声明，利用 Docker 层缓存
COPY requirements.txt pyproject.toml ./
RUN pip3 install --no-cache-dir -e .

# 复制应用代码
COPY . .

# 初始化数据目录结构（实际数据由卷挂载提供）
RUN mkdir -p data/results data/raw_responses data/raw_searches

EXPOSE 8765

VOLUME ["/app/data"]

# Dashboard 仅提供 Web UI 和数据管理，采集任务在宿主机运行
CMD ["python3", "web_dashboard.py"]
