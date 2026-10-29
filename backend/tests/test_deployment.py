"""Exercise the one-command deployment entry point without starting real services."""

import getpass
import json
import os
import runpy
import secrets
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("configuration", "external"),
    [
        ("", False),
        ('MODEL_CONFIG_JSON=\'[{"model":"test"}]\'\n', True),
        ("DEEPSEEK_OFFICIAL_API_KEY=test-only-key\n", True),
    ],
)
def test_start_selects_model_stack_waits_for_health_and_keeps_port_override(
    tmp_path, configuration, external
):
    (tmp_path / "scripts").mkdir()
    shutil.copy(ROOT / "scripts/docker-up.sh", tmp_path / "scripts/docker-up.sh")
    if configuration:
        (tmp_path / ".env").write_text(configuration + "DUET_PORT=9999\n")
    binary = tmp_path / "bin"
    binary.mkdir()
    docker = binary / "docker"
    docker.write_text(
        '#!/bin/sh\nif [ "$*" = "compose config --environment" ]; then\n'
        '  printf "%s\\n" "$COMPOSE_TEST_ENVIRONMENT"; exit 0\nfi\n'
        'printf "%s\\n" "$@" > "$DEPLOY_ARGS"\n'
    )
    docker.chmod(0o755)
    environment = {
        "PATH": str(binary) + os.pathsep + os.environ["PATH"],
        "HOME": str(tmp_path),
        "DUET_PORT": "18818",
        "DEPLOY_ARGS": str(tmp_path / "arguments"),
        "COMPOSE_TEST_ENVIRONMENT": configuration + "\nDUET_PORT=18818",
    }
    result = subprocess.run(
        ["bash", str(tmp_path / "scripts/docker-up.sh")],
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    arguments = (tmp_path / "arguments").read_text().splitlines()
    assert "compose.external.yaml" in arguments if external else "-f" not in arguments
    assert "--build" in arguments and "--wait" in arguments
    assert arguments[arguments.index("--wait-timeout") + 1] == "1800"
    assert "http://127.0.0.1:18818" in result.stdout
    assert "test-only-key" not in result.stdout + result.stderr


def test_example_uses_working_local_defaults_instead_of_placeholder_keys():
    # Copying the example without edits should select the local model stack.
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; test -z "$MODEL_CONFIG_JSON"; '
            'test -z "$PRIMARY_MODEL"; test -n "$OLLAMA_MODEL"',
            "check-example",
            str(ROOT / ".env.example"),
        ],
        env={"PATH": os.environ["PATH"]},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_first_deployment_requires_operator_credentials_and_keeps_them_private(
    tmp_path, monkeypatch
):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = scripts / "setup-employee.py"
    shutil.copy(ROOT / "scripts/setup-employee.py", script)

    refused = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True
    )
    assert refused.returncode != 0
    assert not (tmp_path / ".runtime/bootstrap/bootstrap-admin.json").exists()

    chosen_password = secrets.token_urlsafe(24)
    answers = iter([chosen_password, chosen_password])
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr("builtins.input", lambda _: "chosen-admin")
    monkeypatch.setattr(getpass, "getpass", lambda _: next(answers))
    runpy.run_path(str(script), run_name="__main__")

    private_file = tmp_path / ".runtime/bootstrap/bootstrap-admin.json"
    assert json.loads(private_file.read_text()) == {
        "login": "chosen-admin",
        "password": chosen_password,
    }
    assert stat.S_IMODE(private_file.stat().st_mode) == 0o600
    runpy.run_path(str(script), run_name="__main__")
    assert json.loads(private_file.read_text())["password"] == chosen_password


def test_voice_messages_use_prepared_model_without_another_download(tmp_path, monkeypatch):
    from app.adapters.speech import SpeechAdapter

    prepared = tmp_path / "faster-whisper-tiny"
    prepared.mkdir()
    (prepared / "model.bin").write_bytes(b"model fixture")
    paths = []

    def model(path, **options):
        paths.append(path)
        return object()

    monkeypatch.setenv("DUET_VOICE_MODELS", str(tmp_path))
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=model))
    SpeechAdapter.whisper_model.cache_clear()
    try:
        SpeechAdapter.whisper_model("tiny")
        assert paths == [str(prepared)]
    finally:
        SpeechAdapter.whisper_model.cache_clear()


@pytest.mark.parametrize("official", [False, True])
def test_model_pool_matches_the_stack_started_by_docker(monkeypatch, official):
    from app.agent.model_router import load_models

    for key in ["MODEL_CONFIG_JSON", "PRIMARY_MODEL", "DEEPSEEK_OFFICIAL_API_KEY"]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OLLAMA_MODEL", "deployment-check-model")
    if official:
        monkeypatch.setenv("DEEPSEEK_OFFICIAL_API_KEY", "test-only-key")
    models = load_models()
    assert len(models) == 1
    assert models[0].provider == ("openai" if official else "ollama")
    if not official:
        assert models[0].model == "deployment-check-model"
