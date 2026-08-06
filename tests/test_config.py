"""Tests for standalone ene configuration loading."""

import importlib

import ene.config as config


def test_config_loads_only_home_ene_yaml(monkeypatch, tmp_path):
    home = tmp_path / "home"
    cwd = tmp_path / "project"
    home.mkdir()
    cwd.mkdir()
    (home / ".ene.yaml").write_text("openai:\n  home: {}\n", encoding="utf-8")
    (cwd / ".ene.yaml").write_text("openai:\n  local: {}\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(cwd)

    loaded = importlib.reload(config)

    assert loaded.CONFIG_PATH == home / ".ene.yaml"
    assert loaded.HOME_CONFIG_PATH == loaded.CONFIG_PATH
    assert loaded.conf == {"openai": {"home": {}}}
    monkeypatch.undo()
    importlib.reload(config)


def test_invalid_or_non_mapping_config_is_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".ene.yaml").write_text("- not\n- a mapping\n", encoding="utf-8")

    assert importlib.reload(config).conf == {}
    monkeypatch.undo()
    importlib.reload(config)
