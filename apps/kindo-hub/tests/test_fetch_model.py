"""deploy/models/fetch_model.py 回归测试（脚本随 asr/tts 镜像 entrypoint 运行）。

以文件路径直接加载脚本（不入 hub 包），覆盖目录条目修复（2026-09-08：此前只
mkdir 不复制内容，TTS espeak-ng-data 下载后为空目录且校验被误判通过）与完整
生命周期：校验拒绝 → 下载落盘 → 完成标记快路径 → 清单指纹失效自愈 → 坏包拒绝。
"""
from __future__ import annotations

import hashlib
import importlib.util
import tarfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _REPO_ROOT / "deploy" / "models" / "fetch_model.py"
_ZERO_SHA = "0" * 64


def _load_script():
    spec = importlib.util.spec_from_file_location("fetch_model_under_test", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def fm():
    return _load_script()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_tarball(root: Path, files: dict[str, bytes], *, subdir: str = "pkg-v1") -> tuple[Path, str]:
    src = root / "pkg-src" / subdir
    for rel, data in files.items():
        target = src / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    tar_path = root / f"{subdir}.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        tf.add(src, arcname=subdir)
    return tar_path, _sha(tar_path)


def _write_manifest(path: Path, entries: list[tuple[str, str | None]], *, header: str = "") -> None:
    lines = [f"{_ZERO_SHA if digest is None else digest}  {rel}" for rel, digest in entries]
    path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")


def _set_env(monkeypatch, tmp: Path, *, manifest: Path, tarball: Path | None, tar_sha: str = "") -> Path:
    model_dir = tmp / "models"
    model_dir.mkdir(parents=True, exist_ok=True)  # 真实部署中模型卷目录预先存在
    monkeypatch.setenv("MODEL_DIR", str(model_dir))
    monkeypatch.setenv("MODEL_VERSION", "test-v1")
    monkeypatch.setenv("MODEL_MANIFEST", str(manifest))
    monkeypatch.setenv("MODEL_REQUIRED", "tokens.txt")
    monkeypatch.setenv("SOURCE_TARBALL_SHA256", tar_sha)
    if tarball is not None:
        monkeypatch.setenv("SOURCE_TARBALL_URL", tarball.as_uri())
    else:
        monkeypatch.setenv("SOURCE_TARBALL_URL", "")
    return model_dir


def test_verify_rejects_empty_dir_entry(fm, tmp_path, monkeypatch):
    manifest = tmp_path / "m.manifest"
    _write_manifest(manifest, [("tokens.txt", _sha(_write_bytes(tmp_path, "t.txt", b"tok"))),
                               ("espeak-ng-data/", None)])
    model_dir = _set_env(monkeypatch, tmp_path, manifest=manifest, tarball=None)
    (model_dir / "tokens.txt").write_bytes(b"tok")
    (model_dir / "espeak-ng-data").mkdir()
    entries = fm.load_manifest(str(manifest))
    problem = fm.verify(model_dir, entries)
    assert problem is not None and "缺失或为空" in problem
    (model_dir / "espeak-ng-data" / "dict.txt").write_bytes(b"d")
    assert fm.verify(model_dir, entries) is None


def _write_bytes(root: Path, name: str, data: bytes) -> Path:
    p = root / "files" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def test_download_populates_dir_entry_and_marks_complete(fm, tmp_path, monkeypatch, capsys):
    tok = _write_bytes(tmp_path, "tokens.txt", b"tok")
    dict_data = _write_bytes(tmp_path, "dict.txt", b"dict-data").read_bytes()
    tarball, tar_sha = _make_tarball(
        tmp_path,
        {"tokens.txt": b"tok", "espeak-ng-data/dict.txt": b"dict-data"},
    )
    manifest = tmp_path / "m.manifest"
    _write_manifest(manifest, [("tokens.txt", _sha(tok)), ("espeak-ng-data/", None)])
    model_dir = _set_env(monkeypatch, tmp_path, manifest=manifest, tarball=tarball, tar_sha=tar_sha)

    fm.main()

    assert (model_dir / "espeak-ng-data" / "dict.txt").read_bytes() == dict_data
    marker = model_dir / ".kindo-model-complete"
    assert marker.read_text(encoding="utf-8") == f"test-v1:{_sha(manifest)}"
    assert "模型准备完成" in capsys.readouterr().out


def test_fast_path_skips_download_when_marker_matches(fm, tmp_path, monkeypatch, capsys):
    tok = _write_bytes(tmp_path, "tokens.txt", b"tok")
    tarball, tar_sha = _make_tarball(tmp_path, {"tokens.txt": b"tok"})
    manifest = tmp_path / "m.manifest"
    _write_manifest(manifest, [("tokens.txt", _sha(tok))])
    _set_env(monkeypatch, tmp_path, manifest=manifest, tarball=tarball, tar_sha=tar_sha)

    fm.main()
    capsys.readouterr()

    # 标记一致 → 不进入下载流程（此处 URL 已指向不存在的包，走到下载即失败）
    monkeypatch.setenv("SOURCE_TARBALL_URL", (tmp_path / "no-such-pkg.tar.gz").as_uri())
    fm.main()
    assert "模型已就绪" in capsys.readouterr().out


def test_manifest_change_heals_empty_dir_volume(fm, tmp_path, monkeypatch, capsys):
    """v0.1.0 遗留卷（目录条目为空、标记指向旧指纹）→ 清单更新后重新下载自愈。"""
    tok = _write_bytes(tmp_path, "tokens.txt", b"tok")
    tarball, tar_sha = _make_tarball(
        tmp_path,
        {"tokens.txt": b"tok", "espeak-ng-data/dict.txt": b"dict-data"},
        subdir="heal-pkg",
    )
    manifest = tmp_path / "m.manifest"
    _write_manifest(manifest, [("tokens.txt", _sha(tok)), ("espeak-ng-data/", None)])
    model_dir = _set_env(monkeypatch, tmp_path, manifest=manifest, tarball=tarball, tar_sha=tar_sha)

    # 旧缺陷产物：文件齐全 + 空目录 + 旧指纹标记
    (model_dir / "tokens.txt").write_bytes(b"tok")
    (model_dir / "espeak-ng-data").mkdir()
    (model_dir / ".kindo-model-complete").write_text("test-v1:stale-fingerprint", encoding="utf-8")

    fm.main()
    assert (model_dir / "espeak-ng-data" / "dict.txt").read_bytes() == b"dict-data"
    assert (model_dir / ".kindo-model-complete").read_text(encoding="utf-8") == f"test-v1:{_sha(manifest)}"


def test_bad_tarball_sha_rejected(fm, tmp_path, monkeypatch):
    tok = _write_bytes(tmp_path, "tokens.txt", b"tok")
    tarball, _ = _make_tarball(tmp_path, {"tokens.txt": b"tok"})
    manifest = tmp_path / "m.manifest"
    _write_manifest(manifest, [("tokens.txt", _sha(tok))])
    model_dir = _set_env(monkeypatch, tmp_path, manifest=manifest, tarball=tarball, tar_sha="f" * 64)

    with pytest.raises(SystemExit):
        fm.main()
    assert not (model_dir / ".kindo-model-complete").exists()
    assert not (model_dir / "tokens.txt").exists()
