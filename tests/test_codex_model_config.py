from workflow.unattended.producer import (
    CODEX_MODEL_ENV, DEFAULT_CODEX_MODEL, CodexModel, ProduceOptions,
)


def test_default_unchanged(monkeypatch):
    monkeypatch.delenv(CODEX_MODEL_ENV, raising=False)
    assert DEFAULT_CODEX_MODEL == "gpt-5.6-sol"
    assert CodexModel.resolve() == "gpt-5.6-sol"
    assert ProduceOptions().codex_model == "gpt-5.6-sol"


def test_env_override(monkeypatch):
    monkeypatch.setenv(CODEX_MODEL_ENV, " gpt-5.6-astra ")
    assert CodexModel.resolve() == "gpt-5.6-astra"
    assert ProduceOptions().codex_model == "gpt-5.6-astra"


def test_blank_env_falls_back(monkeypatch):
    monkeypatch.setenv(CODEX_MODEL_ENV, "  ")
    assert CodexModel.resolve() == "gpt-5.6-sol"


def test_explicit_beats_env(monkeypatch):
    monkeypatch.setenv(CODEX_MODEL_ENV, "gpt-5.6-astra")
    assert CodexModel.resolve("my-model") == "my-model"
    assert CodexModel.resolve(None) == "gpt-5.6-astra"
