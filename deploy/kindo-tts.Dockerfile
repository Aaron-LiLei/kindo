# syntax=docker/dockerfile:1
# kindo-tts：本地克隆 TTS Provider（sherpa-onnx ZipVoice-Distill 零样本，纯 CPU）。
# 模型目录 model/ 需在构建前放置（下载方式见 apps/kindo-tts/README.md）；
# 未放置时服务以 no_model 降级启动（/health ready:false），Hub 自动回退系统 TTS。
# 依赖（含 linux 专用 onnxruntime，已 pin）由 requirements.lock 全量锁定
FROM python:3.11-slim-bookworm

WORKDIR /app
COPY requirements.lock ./requirements.lock
RUN pip install --no-cache-dir -r requirements.lock
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir --no-deps . \
    && useradd --system --uid 10001 --no-create-home kindo \
    && chown -R kindo:kindo /app

# 模型目录需在构建前放置（与 kindo-asr 同约定；未放置时构建即失败，见 README）
COPY model ./model
RUN chown -R kindo:kindo /app/model

EXPOSE 8092
USER kindo
ENV KINDO_TTS_MODEL_DIR=/app/model
# 未配置模型时 /health 返回 ready:false（no_model 降级）——只探测进程存活语义
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8092/health', timeout=4); sys.exit(0)"
CMD ["python", "-m", "uvicorn", "kindo_tts.service:app", "--host", "0.0.0.0", "--port", "8092", "--log-level", "warning"]
