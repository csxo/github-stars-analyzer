"""Config loader tests."""

from __future__ import annotations

from pathlib import Path

from github_stars_analyzer.config import load_config


def test_defaults(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GSA_CONFIG", raising=False)
    cfg = load_config()
    assert cfg.pipeline.batch_size > 0
    assert cfg.llm.analysis_version == "1.0.0"


def test_yaml_overrides_defaults(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    yaml_path = tmp_path / "c.yaml"
    yaml_path.write_text(
        "pipeline:\n  batch_size: 7\nllm:\n  model: qwen-test\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GSA_CONFIG", str(yaml_path))
    cfg = load_config()
    assert cfg.pipeline.batch_size == 7
    assert cfg.llm.model == "qwen-test"


def test_env_overrides_yaml(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    yaml_path = tmp_path / "c.yaml"
    yaml_path.write_text("llm:\n  model: from-yaml\n", encoding="utf-8")
    monkeypatch.setenv("GSA_CONFIG", str(yaml_path))
    monkeypatch.setenv("LLM_MODEL", "from-env")
    cfg = load_config()
    assert cfg.llm.model == "from-env"