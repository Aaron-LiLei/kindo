# kindo-tts：本地克隆 TTS Provider（sherpa-onnx ZipVoice-Distill 零样本，纯 CPU）。
# 模型不进镜像（同 kindo-asr，T-20260902-003-06）：entrypoint 对模型卷做
# 检查→下载→校验→持久化；缺失时服务以 no_model 降级启动（/health ready:false），
# Hub 自动回退 Android 系统 TTS。上游模型源见 apps/kindo-tts/README.md。
# 构建上下文 = 仓库根：docker build -f deploy/kindo-tts.Dockerfile .
FROM python:3.11-slim-bookworm

WORKDIR /app
COPY apps/kindo-tts/requirements.lock ./requirements.lock
RUN pip install --no-cache-dir -r requirements.lock
COPY apps/kindo-tts/pyproject.toml ./
COPY apps/kindo-tts/src ./src
RUN pip install --no-cache-dir --no-deps . \
    && useradd --system --uid 10001 --no-create-home kindo \
    && chown -R kindo:kindo /app \
    && install -d -o kindo -g kindo /models

# 模型自动准备：脚本+清单进镜像，模型卷 /models 运行时挂载
COPY deploy/models/fetch_model.py deploy/models/tts-model.manifest /opt/kindo/
RUN chmod +x /opt/kindo/fetch_model.py

ENV KINDO_TTS_MODEL_DIR=/models \
    MODEL_DIR=/models \
    MODEL_VERSION=tts-zipvoice-distill-v1 \
    MODEL_MANIFEST=/opt/kindo/tts-model.manifest \
    MODEL_REQUIRED="encoder.int8.onnx decoder.int8.onnx tokens.txt"

EXPOSE 8092
USER kindo
# 未配置模型时 /health 返回 ready:false（no_model 降级）——只探测进程存活语义
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8092/health', timeout=4); sys.exit(0)"
CMD ["sh", "-c", "python /opt/kindo/fetch_model.py && exec python -m uvicorn kindo_tts.service:app --host 0.0.0.0 --port 8092 --log-level warning"]
