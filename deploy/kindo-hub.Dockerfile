# syntax=docker/dockerfile:1
# kindo-hub：Python FastAPI 模块化单体 + Web Admin 静态资源 + FFmpeg 系统依赖（技术方案 §1）
# 依赖由 requirements.lock 全量锁定（uv pip compile --universal），构建可复现
FROM python:3.11-slim-bookworm

ARG ADMIN_DIST=admin_dist
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# 依赖层先行：源码改动不失效依赖缓存
COPY requirements.lock ./requirements.lock
RUN pip install --no-cache-dir -r requirements.lock
COPY pyproject.toml alembic.ini ./
COPY alembic ./alembic
COPY src ./src
# Web Admin 构建产物（apps/kindo-admin npm run build 的输出）
COPY ${ADMIN_DIST} ./admin_dist
RUN pip install --no-cache-dir --no-deps . \
    && useradd --system --uid 10001 --no-create-home kindo \
    && chown -R kindo:kindo /app

ENV KINDO_CONFIG=/config/kindo.yaml
EXPOSE 8090
USER kindo
# /health/ready 含 DB/迁移/挂载探测（§16.2）；就绪前不接流量
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8090/health/ready', timeout=4).status == 200 else 1)"
CMD ["python", "-u", "-m", "kindo"]
