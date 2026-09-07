# syntax=docker/dockerfile:1
# kindo-asr：本地 ASR Provider（sherpa-onnx Paraformer）。
# 模型不进镜像（T-20260902-003-06）：启动时 entrypoint 对模型卷做
# 检查→下载→校验→持久化（deploy/models/fetch_model.py）；离线部署可直接
# 预置模型文件到卷中，脚本校验通过即跳过下载。依赖（含 linux 专用
# onnxruntime，已 pin）由 requirements.lock 全量锁定。
# 构建上下文 = 仓库根：docker build -f deploy/kindo-asr.Dockerfile .
FROM python:3.11-slim-bookworm

WORKDIR /app
COPY apps/kindo-asr/requirements.lock ./requirements.lock
RUN pip install --no-cache-dir -r requirements.lock
COPY apps/kindo-asr/pyproject.toml ./
COPY apps/kindo-asr/src ./src
RUN pip install --no-cache-dir --no-deps . \
    && useradd --system --uid 10001 --no-create-home kindo \
    && chown -R kindo:kindo /app

# 模型自动准备：脚本+清单进镜像，模型卷 /models 运行时挂载
COPY deploy/models/fetch_model.py deploy/models/asr-model.manifest /opt/kindo/
RUN chmod +x /opt/kindo/fetch_model.py

ENV KINDO_ASR_MODEL_DIR=/models \
    MODEL_DIR=/models \
    MODEL_VERSION=asr-paraformer-chuan-v1 \
    MODEL_MANIFEST=/opt/kindo/asr-model.manifest \
    MODEL_REQUIRED="model.int8.onnx tokens.txt"

EXPOSE 8081
USER kindo
# 未配置模型时 /health 返回 ready:false（no_model 降级）——只探测进程存活语义
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8081/health', timeout=4); sys.exit(0)"
CMD ["sh", "-c", "python /opt/kindo/fetch_model.py && exec python -m uvicorn kindo_asr.service:app --host 0.0.0.0 --port 8081 --log-level warning"]
