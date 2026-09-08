"""环境变量覆盖（2026-09-08，NAS 应用商店一键安装前置）：
无配置文件场景下经 KINDO_ASR_ENDPOINT/KINDO_TTS_ENDPOINT/KINDO_TIMEZONE 打通
服务发现与本地化；层级不变：环境变量 > 配置文件 > 内置默认值（技术方案 §12.1）。"""
from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_config_module():
    """以独立模块加载 kindo.config（避免依赖全局环境状态）。"""
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "kindo_config_under_test", root / "src" / "kindo" / "config.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_env_overrides_without_config_file(monkeypatch, tmp_path):
    mod = _load_config_module()
    monkeypatch.setenv("KINDO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("KINDO_ASR_ENDPOINT", "http://kindo-asr:8081")
    monkeypatch.setenv("KINDO_TTS_ENDPOINT", "http://kindo-tts:8092")
    monkeypatch.setenv("KINDO_TIMEZONE", "America/New_York")
    monkeypatch.delenv("KINDO_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)  # 无 kindo.yaml、无 /config

    cfg = mod.load_config()
    assert cfg.asr_endpoint == "http://kindo-asr:8081"
    assert cfg.tts_endpoint == "http://kindo-tts:8092"
    assert cfg.timezone == "America/New_York"
    assert cfg.port == 8090  # 默认值不受影响


def test_missing_config_file_falls_back_to_defaults(monkeypatch, tmp_path):
    """无 KINDO_CONFIG 且无配置文件 → 内置默认值（NAS 一键安装形态）。"""
    mod = _load_config_module()
    monkeypatch.setenv("KINDO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("KINDO_CONFIG", raising=False)
    monkeypatch.delenv("KINDO_ASR_ENDPOINT", raising=False)
    monkeypatch.chdir(tmp_path)

    cfg = mod.load_config()
    assert cfg.asr_endpoint == ""  # 空 = 语音不可用但不阻塞启动（页面/环境可后配）
    assert cfg.tts_endpoint == ""
