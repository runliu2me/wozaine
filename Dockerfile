FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    COMPANION_DATA_DIR=/app/data \
    LISTEN_HOST=0.0.0.0 \
    LISTEN_PORT=3093 \
    REALTIME_HOST=0.0.0.0 \
    REALTIME_PORT=3095 \
    XIAOZHI_OTA_HOST=0.0.0.0 \
    XIAOZHI_OTA_PORT=3096 \
    XIAOZHI_WS_HOST=0.0.0.0 \
    XIAOZHI_WS_PORT=3097 \
    XIAOZHI_INTERCOM_HOST=0.0.0.0 \
    XIAOZHI_INTERCOM_PORT=3098

RUN apt-get update \
    && apt-get install -y --no-install-recommends libopus0 ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
ARG PIP_INDEX_URL=""
RUN if [ -n "$PIP_INDEX_URL" ]; then \
      pip install --no-cache-dir -i "$PIP_INDEX_URL" -r requirements.txt; \
    else \
      pip install --no-cache-dir -r requirements.txt; \
    fi

# 显式白名单拷贝应用文件，杜绝 .env / family.json / persona.md / data/ 意外打进镜像
COPY *.py story_seed.json family.example.json persona.example.md ./
COPY models ./models
COPY web ./web
COPY tools ./tools
COPY deploy/docker-entrypoint.sh ./deploy/docker-entrypoint.sh

RUN useradd -r -u 1000 -m wozaine \
    && mkdir -p /app/data /app/public \
    && chmod +x /app/deploy/docker-entrypoint.sh \
    && chown -R wozaine:wozaine /app/data /app/public

USER wozaine

EXPOSE 3093 3095 3096 3097 3098

ENTRYPOINT ["/app/deploy/docker-entrypoint.sh"]
CMD ["serve"]
