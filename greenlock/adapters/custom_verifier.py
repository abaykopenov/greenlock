import subprocess
import shlex
from pathlib import Path
from greenlock.adapters.docker_wrapper import is_docker_enabled, run_in_docker

class CustomVerifier:
    """A generic project verifier that runs user-defined shell commands."""

    def __init__(self, config: dict):
        self.config = config
        self.test_command = config.get("test_command")
        self.syntax_command = config.get("syntax_command")

    def detect(self, root: Path) -> bool:
        return True

    def verify(self, workdir: Path, changed: list[str], baseline: dict | None = None) -> dict:
        workdir = Path(workdir).resolve()
        stages = []

        # 1. Syntax check
        if self.syntax_command:
            syntax_ok = True
            syntax_output = ""
            cmd = shlex.split(self.syntax_command)
            try:
                if is_docker_enabled():
                    proc = run_in_docker(cmd, workdir, "CustomVerifier", timeout=15)
                else:
                    proc = subprocess.run(
                        cmd,
                        cwd=str(workdir),
                        capture_output=True,
                        text=True,
                        timeout=15
                    )
                if proc.returncode != 0:
                    syntax_ok = False
                    syntax_output = (proc.stderr or "") + "\n" + (proc.stdout or "")
            except Exception as e:
                syntax_ok = False
                syntax_output = f"Failed to run syntax check: {e}"

            stages.append({
                "name": "syntax",
                "ok": syntax_ok,
                "output": syntax_output or "Syntax check passed."
            })

            if not syntax_ok:
                return {
                    "available": True,
                    "stages": stages,
                    "passed": False,
                    "confidence": "full",
                    "regression": False
                }

        # 2. Test command
        test_ok = True
        test_output = ""
        if self.test_command:
            cmd = shlex.split(self.test_command)
            try:
                if is_docker_enabled():
                    proc = run_in_docker(cmd, workdir, "CustomVerifier", timeout=30)
                else:
                    proc = subprocess.run(
                        cmd,
                        cwd=str(workdir),
                        capture_output=True,
                        text=True,
                        timeout=30
                    )
                test_output = (proc.stdout or "") + "\n" + (proc.stderr or "")
                if proc.returncode != 0:
                    test_ok = False
            except Exception as e:
                test_ok = False
                test_output = f"Failed to run test command: {e}"
        else:
            test_output = "No test command configured."

        stages.append({
            "name": "tests",
            "ok": test_ok,
            "output": test_output
        })

        passed = all(st["ok"] for st in stages)
        return {
            "available": True,
            "stages": stages,
            "passed": passed,
            "confidence": "degraded",
            "regression": False
        }

    def capture_baseline(self, workdir: Path) -> dict:
        """Execute baseline command to ensure existing test suite compiles and runs successfully."""
        workdir = Path(workdir).resolve()
        if self.test_command:
            cmd = shlex.split(self.test_command)
            try:
                if is_docker_enabled():
                    proc = run_in_docker(cmd, workdir, "CustomVerifier", timeout=30)
                else:
                    proc = subprocess.run(
                        cmd,
                        cwd=str(workdir),
                        capture_output=True,
                        text=True,
                        timeout=30
                    )
                if proc.returncode != 0:
                    raise RuntimeError(
                        f"Custom baseline command failed with exit code {proc.returncode}.\n"
                        f"Output: {(proc.stdout or '') + (proc.stderr or '')}"
                    )
            except Exception as e:
                raise RuntimeError(f"Failed to execute custom test command: {e}")
        return {"passed": set(), "failed": set(), "errors": set()}

