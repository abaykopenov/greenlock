import json
import os
import sys
import subprocess
from pathlib import Path
import pytest
from unittest.mock import patch, MagicMock

from greenlock.adapters import detect_verifier
from greenlock.adapters.pytest_verifier import PytestVerifier
from greenlock.adapters.node_verifier import NodeVerifier
from greenlock.adapters.custom_verifier import CustomVerifier
from greenlock.adapters.docker_wrapper import is_docker_enabled, get_default_image, run_in_docker
from greenlock.gate import init_git_hook, main as gate_main


def test_detect_verifier_from_config_pytest(tmp_path):
    config_file = tmp_path / "greenlock.json"
    config_file.write_text(json.dumps({
        "verifier": "pytest",
        "test_command": "pytest --maxfail=1"
    }), encoding="utf-8")
    
    v = detect_verifier(tmp_path)
    assert isinstance(v, PytestVerifier)
    assert getattr(v, "test_command", None) == "pytest --maxfail=1"


def test_detect_verifier_from_config_custom(tmp_path):
    config_file = tmp_path / "greenlock.config.json"
    config_file.write_text(json.dumps({
        "verifier": "custom",
        "syntax_command": "python3 -m py_compile main.py",
        "test_command": "python3 test.py"
    }), encoding="utf-8")
    
    v = detect_verifier(tmp_path)
    assert isinstance(v, CustomVerifier)
    assert getattr(v, "syntax_command", None) == "python3 -m py_compile main.py"
    assert getattr(v, "test_command", None) == "python3 test.py"


def test_custom_verifier_verify_success(tmp_path):
    config = {
        "verifier": "custom",
        "syntax_command": "echo 'syntax ok'",
        "test_command": "echo 'tests ok'"
    }
    v = CustomVerifier(config)
    res = v.verify(tmp_path, ["main.py"])
    assert res["passed"] is True
    assert res["available"] is True
    assert len(res["stages"]) == 2
    assert res["stages"][0]["name"] == "syntax"
    assert res["stages"][0]["ok"] is True
    assert res["stages"][1]["name"] == "tests"
    assert res["stages"][1]["ok"] is True


def test_custom_verifier_verify_syntax_failure(tmp_path):
    config = {
        "verifier": "custom",
        "syntax_command": "false",
        "test_command": "echo 'tests ok'"
    }
    v = CustomVerifier(config)
    res = v.verify(tmp_path, ["main.py"])
    assert res["passed"] is False
    assert len(res["stages"]) == 1
    assert res["stages"][0]["name"] == "syntax"
    assert res["stages"][0]["ok"] is False


def test_custom_verifier_verify_test_failure(tmp_path):
    config = {
        "verifier": "custom",
        "syntax_command": "echo 'syntax ok'",
        "test_command": "false"
    }
    v = CustomVerifier(config)
    res = v.verify(tmp_path, ["main.py"])
    assert res["passed"] is False
    assert len(res["stages"]) == 2
    assert res["stages"][1]["name"] == "tests"
    assert res["stages"][1]["ok"] is False


def test_custom_verifier_capture_baseline(tmp_path):
    config = {
        "verifier": "custom",
        "test_command": "echo 'baseline'"
    }
    v = CustomVerifier(config)
    baseline = v.capture_baseline(tmp_path)
    assert isinstance(baseline, dict)
    assert "passed" in baseline


def test_init_git_hook_success(tmp_path):
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    
    code = init_git_hook(str(tmp_path))
    assert code == 0
    
    hook_file = git_dir / "hooks" / "pre-commit"
    assert hook_file.exists()
    assert "greenlock.gate" in hook_file.read_text(encoding="utf-8")
    
    # Check if executable (on Unix)
    if os.name != 'nt':
        assert os.access(hook_file, os.X_OK)


def test_init_git_hook_not_git_repo(tmp_path):
    code = init_git_hook(str(tmp_path))
    assert code == 1


def test_gate_main_init_hook(tmp_path):
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    
    with patch.object(sys, "argv", ["greenlock", "init", str(tmp_path)]):
        with patch("sys.stdout") as mock_stdout:
            code = gate_main()
            assert code == 0
    
    hook_file = git_dir / "hooks" / "pre-commit"
    assert hook_file.exists()


def test_docker_wrapper_helpers():
    # Test is_docker_enabled
    with patch("greenlock.adapters.docker_wrapper.DOCKER", "1"):
        assert is_docker_enabled() is True
    with patch("greenlock.adapters.docker_wrapper.DOCKER", "true"):
        assert is_docker_enabled() is True
    with patch("greenlock.adapters.docker_wrapper.DOCKER", ""):
        assert is_docker_enabled() is False

    # Test get_default_image
    with patch("greenlock.adapters.docker_wrapper.DOCKER_IMAGE", "custom:latest"):
        assert get_default_image("PytestVerifier") == "custom:latest"
    with patch("greenlock.adapters.docker_wrapper.DOCKER_IMAGE", ""):
        assert get_default_image("NodeVerifier") == "node:20-slim"
        assert get_default_image("PytestVerifier") == "python:3.10-slim"


@patch("subprocess.run")
def test_run_in_docker(mock_run, tmp_path):
    mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="out", stderr="")
    
    cmd = ["pytest"]
    res = run_in_docker(cmd, tmp_path, "PytestVerifier", timeout=30)
    
    assert mock_run.called
    args, kwargs = mock_run.call_args
    docker_cmd = args[0]
    assert docker_cmd[0] == "docker"
    assert docker_cmd[1] == "run"
    assert "--network" in docker_cmd
    assert "none" in docker_cmd
    assert "python:3.10-slim" in docker_cmd
    assert docker_cmd[-1] == "pytest"
