"""adapters.node_verifier — Оракул-верификатор на базе встроенного Node.js test runner.

Выполняет синтаксическую проверку JS-файлов, прогон тестов с таймаутом и
сравнение результатов с базовой линией (baseline) для выявления регрессии.
"""
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

__all__ = ["NodeVerifier"]


class NodeVerifier:
    """Верификатор проектов Node.js."""

    def detect(self, root: Path) -> bool:
        """Автоопределение: наличие package.json или JS-файлов."""
        root = Path(root)
        return (root / "package.json").exists() or any(root.rglob("*.js"))

    def verify(self, workdir: Path, changed: list[str], baseline: dict | None = None) -> dict:
        """Прогнать стадии: syntax → tests с таймаутом и сверкой с baseline."""
        workdir = Path(workdir).resolve()
        stages = []
        confidence = "full"
        has_regression = False

        # 1. Syntax (node --check)
        syntax_ok = True
        syntax_output = ""
        for rel_file in changed:
            abs_file = workdir / rel_file
            if abs_file.suffix != ".js":
                continue
            if not abs_file.exists():
                continue
            try:
                proc = subprocess.run(
                    ["node", "--check", str(abs_file)],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                if proc.returncode != 0:
                    syntax_ok = False
                    syntax_output += f"Syntax error in {rel_file}:\n{proc.stderr}\n"
            except Exception as e:
                syntax_ok = False
                syntax_output += f"Failed to check syntax of {rel_file}: {e}\n"

        stages.append({
            "name": "syntax",
            "ok": syntax_ok,
            "output": syntax_output or "All JS files compiled/parsed successfully."
        })

        if not syntax_ok:
            return {
                "available": True,
                "stages": stages,
                "passed": False,
                "confidence": confidence,
                "regression": False
            }

        # 2. Tests (node --test)
        xml_path = workdir / ".groundqa_sandbox_report.xml"
        if xml_path.exists():
            try:
                xml_path.unlink()
            except OSError:
                pass

        if getattr(self, "test_command", None):
            import shlex
            cmd = shlex.split(self.test_command)
            if not any(arg.startswith("--test-reporter-destination") for arg in cmd):
                cmd += [
                    "--test-reporter=junit",
                    f"--test-reporter-destination={xml_path}"
                ]
        else:
            cmd = [
                "node", "--test",
                "--test-reporter=junit",
                f"--test-reporter-destination={xml_path}"
            ]

        test_ok = True
        test_output = ""

        try:
            # Лимит времени 30 секунд для предотвращения бесконечных циклов
            from greenlock.adapters.docker_wrapper import is_docker_enabled, run_in_docker
            if is_docker_enabled():
                proc = run_in_docker(cmd, workdir, "NodeVerifier", timeout=30)
            else:
                proc = subprocess.run(
                    cmd,
                    cwd=str(workdir),
                    capture_output=True,
                    text=True,
                    timeout=30
                )

            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            test_output = stdout + "\n" + stderr

            if proc.returncode not in (0, 1):
                if not xml_path.exists():
                    test_ok = False
                    test_output += f"\nNode test runner error (exit code {proc.returncode})"

            current_results = self._parse_junit_xml(xml_path)

            if xml_path.exists():
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
            else:
                confidence = "degraded"
                test_ok = False
                test_output += "\nWarning: No test report XML found. Verification confidence is degraded."

        except subprocess.TimeoutExpired as e:
            test_ok = False
            test_output = "Node --test timed out after 30 seconds.\n"
            if e.stdout:
                test_output += f"Stdout partially captured:\n{e.stdout}\n"
            if e.stderr:
                test_output += f"Stderr partially captured:\n{e.stderr}\n"
        except FileNotFoundError:
            confidence = "degraded"
            test_ok = False
            test_output = "Node.js is not installed or not executable in this environment."

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
            if not any(arg.startswith("--test-reporter-destination") for arg in cmd):
                cmd += [
                    "--test-reporter=junit",
                    f"--test-reporter-destination={xml_path}"
                ]
        else:
            cmd = [
                "node", "--test",
                "--test-reporter=junit",
                f"--test-reporter-destination={xml_path}"
            ]
        try:
            from greenlock.adapters.docker_wrapper import is_docker_enabled, run_in_docker
            if is_docker_enabled():
                proc = run_in_docker(cmd, workdir, "NodeVerifier", timeout=30)
            else:
                proc = subprocess.run(
                    cmd,
                    cwd=str(workdir),
                    capture_output=True,
                    text=True,
                    timeout=30
                )
        except Exception as e:
            raise RuntimeError(f"Failed to execute Node tests for baseline capture: {e}")

        if xml_path.exists():
            return self._parse_junit_xml(xml_path)

        raise RuntimeError(
            f"Node baseline capture failed with exit code {proc.returncode}.\n"
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
