"""adapters.pytest_verifier — Оракул-верификатор на базе pytest.

Выполняет компиляцию Python-файлов, прогон тестов с таймаутом и сравнение с
базовой линией (baseline) для выявления регрессии.
"""
import subprocess
import py_compile
import xml.etree.ElementTree as ET
from pathlib import Path

__all__ = ["PytestVerifier"]


class PytestVerifier:
    """Верификатор проектов Python."""

    def detect(self, root: Path) -> bool:
        """Автоопределение: наличие Python-файлов в проекте."""
        return any(root.rglob("*.py"))

    def _get_python_executable(self) -> str:
        """Найти интерпретатор Python, в котором установлен pytest."""
        import sys
        # 1. Проверить текущий интерпретатор sys.executable
        try:
            res = subprocess.run([sys.executable, "-c", "import pytest"], capture_output=True)
            if res.returncode == 0:
                return sys.executable
        except Exception:
            pass

        # 2. Проверить системный python3
        try:
            res = subprocess.run(["python3", "-c", "import pytest"], capture_output=True)
            if res.returncode == 0:
                return "python3"
        except Exception:
            pass

        return sys.executable or "python3"

    def verify(self, workdir: Path, changed: list[str], baseline: dict | None = None) -> dict:
        """Прогнать стадии: syntax → tests с таймаутом и сверкой с baseline."""
        workdir = Path(workdir).resolve()
        stages = []
        confidence = "full"
        has_regression = False

        # 1. Syntax (compile check)
        syntax_ok = True
        syntax_output = ""
        for rel_file in changed:
            abs_file = workdir / rel_file
            if abs_file.suffix != ".py":
                continue
            if not abs_file.exists():
                continue
            try:
                py_compile.compile(str(abs_file), doraise=True)
            except Exception as e:
                syntax_ok = False
                syntax_output += f"Syntax error in {rel_file}:\n{str(e)}\n"

        stages.append({
            "name": "syntax",
            "ok": syntax_ok,
            "output": syntax_output or "All files compiled successfully."
        })

        if not syntax_ok:
            return {
                "available": True,
                "stages": stages,
                "passed": False,
                "confidence": confidence,
                "regression": False
            }

        # 2. Tests (pytest)
        xml_path = workdir / ".groundqa_sandbox_report.xml"
        if xml_path.exists():
            try:
                xml_path.unlink()
            except OSError:
                pass

        if getattr(self, "test_command", None):
            import shlex
            cmd = shlex.split(self.test_command)
            if not any(arg.startswith("--junitxml") for arg in cmd):
                cmd.append(f"--junitxml={xml_path}")
        else:
            python_bin = self._get_python_executable()
            cmd = [
                python_bin, "-m", "pytest",
                f"--junitxml={xml_path}",
                "--ignore=.groundqa_sandbox"
            ]

        test_ok = True
        test_output = ""

        try:
            # Лимит времени 30 секунд для предотвращения бесконечных циклов
            import os
            from greenlock.adapters.docker_wrapper import is_docker_enabled, run_in_docker
            
            repo_dir = None
            if changed:
                first_parts = Path(changed[0]).parts
                if first_parts:
                    repo_dir = workdir / first_parts[0]
            if not repo_dir:
                subdirs = [p for p in workdir.iterdir() if p.is_dir() and not p.name.startswith(".")]
                if subdirs:
                    repo_dir = subdirs[0]

            env = os.environ.copy()
            if repo_dir:
                env["PYTHONPATH"] = str(repo_dir) + (os.pathsep + env.get("PYTHONPATH", "") if env.get("PYTHONPATH") else "")

            if is_docker_enabled():
                if "python" in cmd[0] or ".venv" in cmd[0]:
                    cmd[0] = "python3"
                proc = run_in_docker(cmd, workdir, "PytestVerifier", timeout=30)
            else:
                proc = subprocess.run(
                    cmd,
                    cwd=str(workdir),
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=30
                )

            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            test_output = stdout + "\n" + stderr

            # 5 означает "тесты не найдены"
            if proc.returncode == 5:
                confidence = "degraded"
                test_output += "\nWarning: No tests collected. Verification confidence is degraded."
            elif proc.returncode not in (0, 1):
                test_ok = False
                test_output += f"\nPytest error (exit code {proc.returncode})"
            else:
                current_results = self._parse_junit_xml(xml_path)

                # Проверка на регрессию
                if baseline:
                    regressions = baseline["passed"] - current_results["passed"]
                    if regressions:
                        test_ok = False
                        has_regression = True
                        test_output += "\nRegression detected! The following tests failed/errored/disappeared:\n"
                        for r in regressions:
                            test_output += f"  - {r}\n"

                # Проверка наличия упавших тестов в текущем запуске
                if current_results["failed"] or current_results["errors"]:
                    test_ok = False

        except subprocess.TimeoutExpired as e:
            test_ok = False
            test_output = "Pytest timed out after 30 seconds.\n"
            if e.stdout:
                test_output += f"Stdout partially captured:\n{e.stdout}\n"
            if e.stderr:
                test_output += f"Stderr partially captured:\n{e.stderr}\n"
        except FileNotFoundError:
            confidence = "degraded"
            test_ok = False
            test_output = "Pytest is not installed or not executable in this environment."

        stages.append({
            "name": "tests",
            "ok": test_ok,
            "output": test_output
        })

        passed = all(st["ok"] for st in stages)
        return {
            "available": confidence == "full",
            "stages": stages,
            "passed": passed,
            "confidence": confidence,
            "regression": has_regression
        }

    def capture_baseline(self, workdir: Path) -> dict:
        """Собрать базовую линию прохождения тестов до внесения изменений."""
        workdir = Path(workdir).resolve()
        xml_path = workdir / ".groundqa_sandbox_report.xml"
        if xml_path.exists():
            try:
                xml_path.unlink()
            except OSError:
                pass

        if getattr(self, "test_command", None):
            import shlex
            cmd = shlex.split(self.test_command)
            if not any(arg.startswith("--junitxml") for arg in cmd):
                cmd.append(f"--junitxml={xml_path}")
        else:
            python_bin = self._get_python_executable()
            cmd = [
                python_bin, "-m", "pytest",
                f"--junitxml={xml_path}",
                "--ignore=.groundqa_sandbox"
            ]
        try:
            import os
            subdirs = [p for p in workdir.iterdir() if p.is_dir() and not p.name.startswith(".")]
            repo_dir = subdirs[0] if subdirs else None

            env = os.environ.copy()
            if repo_dir:
                env["PYTHONPATH"] = str(repo_dir) + (os.pathsep + env.get("PYTHONPATH", "") if env.get("PYTHONPATH") else "")

            from greenlock.adapters.docker_wrapper import is_docker_enabled, run_in_docker
            if is_docker_enabled():
                if "python" in cmd[0] or ".venv" in cmd[0]:
                    cmd[0] = "python3"
                proc = run_in_docker(cmd, workdir, "PytestVerifier", timeout=30)
            else:
                proc = subprocess.run(
                    cmd,
                    cwd=str(workdir),
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=30
                )
        except Exception as e:
            raise RuntimeError(f"Failed to execute pytest for baseline capture: {e}")

        # 5: no tests collected (confidence will be degraded)
        if proc.returncode == 5:
            return {"passed": set(), "failed": set(), "errors": set(), "no_tests": True}

        # 0 or 1: pytest executed successfully (even if some tests failed)
        if proc.returncode in (0, 1):
            return self._parse_junit_xml(xml_path)

        raise RuntimeError(
            f"Pytest baseline capture failed with exit code {proc.returncode}.\n"
            f"Stdout: {proc.stdout or ''}\nStderr: {proc.stderr or ''}"
        )

    def _parse_junit_xml(self, xml_path: Path) -> dict:
        if not xml_path.exists():
            return {"passed": set(), "failed": set(), "errors": set()}
        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()
            passed = set()
            failed = set()
            errors = set()
            for tc in root.findall(".//testcase"):
                classname = tc.get("classname") or ""
                name = tc.get("name") or ""
                test_id = f"{classname}::{name}"
                if tc.find("failure") is not None:
                    failed.add(test_id)
                elif tc.find("error") is not None:
                    errors.add(test_id)
                else:
                    passed.add(test_id)
            return {"passed": passed, "failed": failed, "errors": errors}
        except Exception:
            return {"passed": set(), "failed": set(), "errors": set()}
