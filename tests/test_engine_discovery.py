import os

from composerbench import engine


def _fake_binary(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


def test_env_var_wins(tmp_path, monkeypatch):
    cli = _fake_binary(tmp_path / "magda_cli")
    monkeypatch.setenv(engine.ENV_VAR, str(cli))
    assert engine.discover_cli() == cli


def test_linux_and_mac_build_dirs_are_searched(tmp_path, monkeypatch):
    monkeypatch.delenv(engine.ENV_VAR, raising=False)
    monkeypatch.setenv(engine.MAGDA_ROOT_ENV, str(tmp_path))
    assert engine.discover_cli() is None
    old = _fake_binary(tmp_path / "cmake-build-debug/magda/daw/magda_cli_artefacts/Debug/magda_cli")
    new = _fake_binary(tmp_path / "build-linux/magda/daw/magda_cli_artefacts/Release/magda_cli")
    os.utime(old, (1, 1))
    assert engine.discover_cli() == new          # the newest build wins


def test_explicit_path_and_require(tmp_path, monkeypatch):
    monkeypatch.delenv(engine.ENV_VAR, raising=False)
    monkeypatch.setenv(engine.MAGDA_ROOT_ENV, str(tmp_path))
    cli = _fake_binary(tmp_path / "custom" / "magda_cli")
    assert engine.discover_cli(cli) == cli
    try:
        engine.require_cli()
    except FileNotFoundError as e:
        assert "COMPOSERBENCH_CLI" in str(e)
    else:
        raise AssertionError("require_cli should fail without a binary")
