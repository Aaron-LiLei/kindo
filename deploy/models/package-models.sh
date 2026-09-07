#!/usr/bin/sh
# 模型发行资产打包与上传（维护者一次性操作；模型文件不入库）。
#
# 产出两个 tar.gz（文件位于包根，与 deploy/models/*.manifest 对应）：
#   runtime/model-pkg/kindo-asr-paraformer-chuan-v1.tar.gz
#   runtime/model-pkg/kindo-tts-zipvoice-distill-v1.tar.gz
#
# 用法：
#   sh deploy/models/package-models.sh          # 打包并打印 SHA256
#   sh deploy/models/package-models.sh --upload # 打包后经 gh 上传到 model-assets-v1 Release
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
OUT="$ROOT/runtime/model-pkg"
mkdir -p "$OUT"

pack() {
    model_dir="$1"; shift
    out_name="$1"; shift
    (cd "$model_dir" && tar czf "$OUT/$out_name" "$@")
    echo "== $out_name"
    sha256sum "$OUT/$out_name"
}

pack "$ROOT/apps/kindo-asr/model" kindo-asr-paraformer-chuan-v1.tar.gz \
    model.int8.onnx tokens.txt
pack "$ROOT/apps/kindo-tts/model" kindo-tts-zipvoice-distill-v1.tar.gz \
    encoder.int8.onnx decoder.int8.onnx tokens.txt lexicon.txt vocos_24khz.onnx espeak-ng-data

if [ "${1:-}" = "--upload" ]; then
    TAG=model-assets-v1
    gh release create "$TAG" --title "Kindo 模型资产 v1" --notes "ASR/TTS 模型包（镜像 entrypoint 自动下载源；SHA256 见包名对应的 release 校验值）。" || true
    gh release upload "$TAG" \
        "$OUT/kindo-asr-paraformer-chuan-v1.tar.gz" \
        "$OUT/kindo-tts-zipvoice-distill-v1.tar.gz" --clobber
fi
