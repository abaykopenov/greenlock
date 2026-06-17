"""adapters.go_verifier — оракул для Go-проектов (детект по go.mod).

Требует toolchain `go` в PATH. Грубая (returncode) проверка build → test;
per-test regression-diff — TODO. Без `go` честно деградирует (confidence='none'),
из-за чего write_code НЕ применит патч (безопасно).
"""
import shutil
import subprocess
from pathlib import Path

__all__ = ["GoVerifier"]


class GoVerifier:
    def detect(self, root) -> bool:
        return (Path(root) / "go.mod").exists()

    def _has_tool(self) -> bool:
        return shutil.which("go") is not None

    def native_suite_green(self, root) -> bool:
        if not self._has_tool():
            return False
        p = subprocess.run(["go", "test", "./..."], cwd=str(root),
                           capture_output=True, text=True, timeout=300)
        return p.returncode == 0

    def capture_baseline(self, workdir) -> dict:
        green = self.native_suite_green(workdir) if self._has_tool() else False
        return {"green": green, "passed": set(), "failed": set(), "errors": set()}

    def verify(self, workdir, changed, baseline=None) -> dict:
        if not self._has_tool():
            return {"available": False, "passed": False, "confidence": "none",
                    "regression": False,
                    "stages": [{"name": "toolchain", "ok": False,
                                "output": "`go` не найден в PATH — верификация недоступна."}]}
        workdir = Path(workdir)
        stages = []
        build = subprocess.run(["go", "build", "./..."], cwd=str(workdir),
                              capture_output=True, text=True, timeout=120)
        stages.append({"name": "build", "ok": build.returncode == 0,
                       "output": build.stdout + build.stderr})
        if build.returncode != 0:
            return {"available": True, "stages": stages, "passed": False,
                    "confidence": "full", "regression": False}
        test = subprocess.run(["go", "test", "./..."], cwd=str(workdir),
                             capture_output=True, text=True, timeout=300)
        tests_ok = test.returncode == 0
        regression = bool(baseline and baseline.get("green")) and not tests_ok
        stages.append({"name": "tests", "ok": tests_ok,
                       "output": test.stdout + test.stderr})
        return {"available": True, "stages": stages,
                "passed": all(s["ok"] for s in stages),
                "confidence": "full", "regression": regression}
