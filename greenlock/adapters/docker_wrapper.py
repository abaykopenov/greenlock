import os
import subprocess
from pathlib import Path
from greenlock.config import VERIFIER_DOCKER, DOCKER_IMAGE

def is_docker_enabled() -> bool:
    # WEAK per-command изоляция управляется ОТДЕЛЬНЫМ ключом GREENLOCK_VERIFIER_DOCKER
    # (WS-5): GREENLOCK_DOCKER теперь означает только STRONG whole-gate изоляцию.
    return VERIFIER_DOCKER.lower() in ("1", "true", "yes")

def get_default_image(verifier_name: str) -> str:
    if DOCKER_IMAGE:
        return DOCKER_IMAGE
    if verifier_name == "NodeVerifier":
        return "node:20-slim"
    if verifier_name == "GoVerifier":
        return "golang:1.22"
    if verifier_name == "RustVerifier":
        return "rust:1.75-slim"
    return "python:3.10-slim"

def run_in_docker(cmd: list[str], workdir: Path, verifier_name: str, timeout: int = 30) -> subprocess.CompletedProcess:
    """Run a command inside an isolated Docker container with the workdir mounted."""
    workdir_abs = str(Path(workdir).resolve())
    image = get_default_image(verifier_name)

    docker_cmd = [
        "docker", "run", "--rm",
        "-v", f"{workdir_abs}:{workdir_abs}",
        "-w", workdir_abs,
        "--network", "none",
        image
    ] + cmd

    return subprocess.run(
        docker_cmd,
        capture_output=True,
        text=True,
        timeout=timeout
    )
