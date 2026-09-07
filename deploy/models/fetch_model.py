#!/usr/bin/env python3
"""模型自动准备（T-20260902-003-06）：检查 → 下载 → 校验 → 持久化。

kindo-asr / kindo-tts 容器启动时运行（entrypoint 链）：模型卷内文件完整且通过
校验 → 秒级通过并写完成标记；缺失/损坏 → 从 SOURCE_TARBALL_URL 下载模型包，
校验包体 SHA256 后原子落盘；已有完整模型（离线预置）→ 只校验不下载。
升级不重复下载：模型在独立卷中持久化，标记与清单一致即跳过。

环境变量（由镜像 ENV 提供默认，Compose 可覆盖）：
  MODEL_DIR              模型目录（卷挂载）
  MODEL_VERSION          模型版本标识（写入完成标记）
  MODEL_MANIFEST         清单文件路径（SHA256SUMS 格式："<sha256>  <相对路径>"，
                         目录行以 "/" 结尾=存在性检查）
  SOURCE_TARBALL_URL     模型包 URL（.tar.gz / .tar.bz2；文件在包内任意深度均可识别）
  SOURCE_TARBALL_SHA256  模型包预期 SHA256（空=仅靠清单校验内容）
  MODEL_REQUIRED         空格分隔的必需文件列表（manifest 之外的快速存在性检查）

用法：fetch_model.py [--check]（--check 只报告不落盘）
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

CHUNK = 1 << 20
RETRIES = 3


def log(msg: str) -> None:
    print(f"[model-init] {msg}", flush=True)


def die(msg: str) -> None:
    log(f"FATAL: {msg}")
    sys.exit(1)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(path: str) -> list[tuple[str, str]]:
    """返回 [(相对路径, 预期 sha256 或 ''=仅存在性)]。

    清单头注释可携带模型包默认源（单一事实来源，镜像 ENV 不再重复维护）：
      # tarball-url: <默认下载地址>
      # tarball-sha256: <包体 SHA256>
    """
    entries: list[tuple[str, str]] = []
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            digest, _, rel = line.partition("  ")
            rel = rel.strip().lstrip("./")
            if rel.endswith("/"):
                entries.append((rel, ""))
            else:
                entries.append((rel, digest))
    if not entries:
        die(f"清单为空: {path}")
    return entries


def manifest_default(path: str, key: str) -> str:
    """读取清单头的 `# <key>: <value>` 默认值。"""
    prefix = f"# {key}:"
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if line.startswith(prefix):
                return line[len(prefix):].strip()
    return ""


def env_or_manifest(key: str, manifest_path: str, manifest_key: str) -> str:
    """env 非空优先（内网/镜像覆盖）；否则回落清单默认。compose 常把未配置的
    环境变量渲染成空串，因此空串视同未设置。"""
    return os.environ.get(key) or manifest_default(manifest_path, manifest_key)


def verify(model_dir: Path, manifest: list[tuple[str, str]], *, quick: bool = False) -> str | None:
    """返回第一个问题的描述；全部通过返回 None。quick=True 只查存在性。"""
    required = os.environ.get("MODEL_REQUIRED", "").split()
    for rel in required:
        if not (model_dir / rel).is_file():
            return f"缺少必需文件 {rel}"
    for rel, expect in manifest:
        target = model_dir / rel
        if rel.endswith("/"):
            if not target.is_dir():
                return f"缺少目录 {rel}"
            continue
        if not target.is_file():
            return f"缺少 {rel}"
        if quick:
            continue
        actual = sha256_file(target)
        if actual != expect:
            return f"{rel} 校验不符（预期 {expect[:12]}… 实际 {actual[:12]}…）"
    return None


def download(url: str, dest: Path) -> None:
    last = "unknown"
    for attempt in range(1, RETRIES + 1):
        try:
            log(f"下载模型包（第 {attempt}/{RETRIES} 次）: {url}")
            req = urllib.request.Request(url, headers={"User-Agent": "kindo-model-init/1"})
            with urllib.request.urlopen(req, timeout=60) as resp, dest.open("wb") as f:
                while True:
                    chunk = resp.read(CHUNK)
                    if not chunk:
                        break
                    f.write(chunk)
            return
        except Exception as exc:  # noqa: BLE001
            last = str(exc)[:200]
            dest.unlink(missing_ok=True)
    die(f"下载失败: {last}")


def main() -> None:
    if "--check" in sys.argv:
        model_dir = Path(os.environ["MODEL_DIR"])
        manifest = load_manifest(os.environ["MODEL_MANIFEST"])
        problem = verify(model_dir, manifest)
        print("ok" if problem is None else f"incomplete: {problem}")
        return

    model_dir = Path(os.environ.get("MODEL_DIR", "")) if os.environ.get("MODEL_DIR") else die("未设置 MODEL_DIR")
    version = os.environ.get("MODEL_VERSION") or die("未设置 MODEL_VERSION")
    manifest_path = os.environ.get("MODEL_MANIFEST") or die("未设置 MODEL_MANIFEST")
    manifest = load_manifest(manifest_path)
    marker = model_dir / ".kindo-model-complete"

    model_dir.mkdir(parents=True, exist_ok=True)

    # 快路径：标记版本与清单一致 → 跳过（升级/重启不重复校验下载）
    if marker.is_file() and marker.read_text(encoding="utf-8").strip() == f"{version}:{_manifest_fingerprint(manifest_path)}":
        log(f"模型已就绪（v{version}）")
        return

    problem = verify(model_dir, manifest)
    if problem is None:
        # 离线预置或既有完整模型：校验通过即标记
        _write_marker(marker, version, manifest_path)
        log(f"模型校验通过（v{version}）")
        return
    log(f"模型不完整: {problem} → 进入下载流程")

    url = env_or_manifest("SOURCE_TARBALL_URL", manifest_path, "tarball-url")
    if not url:
        die("模型卷不完整，且未设置 SOURCE_TARBALL_URL / 清单 tarball-url；"
            "离线部署请将模型文件直接放入 MODEL_DIR 后重启")
    expect_tar = env_or_manifest("SOURCE_TARBALL_SHA256", manifest_path, "tarball-sha256")
    required = os.environ.get("MODEL_REQUIRED", "").split()

    with tempfile.TemporaryDirectory(prefix="kindo-model-") as td:
        tar_path = Path(td) / "model-pkg"
        download(url, tar_path)
        if expect_tar:
            actual = sha256_file(tar_path)
            if actual != expect_tar:
                die(f"模型包 SHA256 不符（预期 {expect_tar} 实际 {actual}）——拒绝使用")
            log("模型包 SHA256 校验通过")
        log("解压并校验模型文件…")
        extract_dir = Path(td) / "x"
        extract_dir.mkdir()
        if str(tar_path).endswith((".tar.bz2", ".tbz2")):
            mode = "r:bz2"
        else:
            mode = "r:gz"
        with tarfile.open(tar_path, mode) as tf:
            tf.extractall(extract_dir, filter="data")  # noqa: S202 校验过的自有包
        # 清单逐项从解压结果定位（兼容包内顶层目录）、校验后原子落盘
        for rel, expect in manifest:
            target = model_dir / rel
            if rel.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
                continue
            hits = [p for p in extract_dir.rglob(Path(rel).name)
                    if p.is_file() and expect and sha256_file(p) == expect]
            if not hits:
                die(f"包内未找到匹配清单的 {rel}")
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(target.suffix + ".part")
            shutil.copyfile(hits[0], tmp)
            tmp.replace(target)
        for rel in required:
            if not (model_dir / rel).is_file():
                die(f"下载的模型包缺少必需文件 {rel}")
    _write_marker(marker, version, manifest_path)
    log(f"模型准备完成（v{version}）")


def _manifest_fingerprint(path: str) -> str:
    return sha256_file(Path(path))


def _write_marker(marker: Path, version: str, manifest_path: str) -> None:
    marker.write_text(f"{version}:{_manifest_fingerprint(manifest_path)}", encoding="utf-8")


if __name__ == "__main__":
    main()
