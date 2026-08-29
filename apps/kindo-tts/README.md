# kindo-tts — 本地克隆 TTS Provider

家长声音个性化（PRD TTS-005~007 / 技术方案 §6.7）：家长在管理后台朗读约 10 秒指定文本，
Hub 将样本推送至本容器，AI 回复以零样本克隆的家长声音合成（24kHz 单声道 WAV）。

引擎：[ZipVoice-Distill](https://github.com/k2-fsa/ZipVoice)（int8 ONNX，约 123M 参数，
中英双语，Apache-2.0）+ vocos 24kHz vocoder，经 sherpa-onnx 纯 CPU 推理，无需 GPU。

## 模型放置（构建镜像前）

模型文件不入库（同 kindo-asr）。构建前下载并解压到 `apps/kindo-tts/model/`：

```bash
cd apps/kindo-tts
mkdir -p model && tar xf - -C model  # 或手动解压
curl -LO https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/sherpa-onnx-zipvoice-distill-int8-zh-en-emilia.tar.bz2
tar xf sherpa-onnx-zipvoice-distill-int8-zh-en-emilia.tar.bz2
cp -r sherpa-onnx-zipvoice-distill-int8-zh-en-emilia/* model/
# vocoder（vocos 24kHz）
curl -LO https://github.com/k2-fsa/sherpa-onnx/releases/download/vocoder-models/vocos_24khz.onnx
cp vocos_24khz.onnx model/
```

解压后 `model/` 应包含：`encoder.int8.onnx`、`decoder.int8.onnx`、`tokens.txt`、
`lexicon.txt`、`espeak-ng-data/`、`vocos_24khz.onnx`。

## 本地运行（开发）

```bash
pip install -e .   # 或 pip install -r requirements.lock
KINDO_TTS_MODEL_DIR=$PWD/model python -m uvicorn kindo_tts.service:app --port 8092
```

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /health | `{status, model, ready, voice_loaded}`；模型缺失时 `status=no_model, ready=false` |
| PUT | /v1/voice | `{prompt_wav_base64, prompt_text}` → 204；PCM16 WAV，2~20s |
| DELETE | /v1/voice | 清除内存声纹 → 204 |
| POST | /v1/synthesis | `{tts_id, text}` → audio/wav（24kHz 单声道 PCM16） |

仅容器网络内可达（compose `expose`，不映射宿主端口）。参考音频与合成结果仅内存处理，
不落盘、不写日志（PRD TTS-007）。
