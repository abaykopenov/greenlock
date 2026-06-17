"""adapters.rust_verifier — оракул для Rust-проектов (детект по Cargo.toml).

Требует toolchain `cargo` в PATH. Грубая (returncode) проверка build → test;
per-test regression-diff — TODO. Без `cargo` честно деградирует (confidence='none'),
из-за чего write_code НЕ применит патч (безопасно).
"""
import shutil
import subprocess
from pathlib import Path

__all__ = ["RustVerifier"]


class RustVerifier:
    def detect(self, root) -> bool:
        return (Path(root) / "Cargo.toml").exists()

    def _has_tool(self) -> bool:
        return shutil.which("cargo") is not None

    def native_suite_green(self, root) -> bool:
        if not self._has_tool():
            return False
        p = subprocess.run(["cargo", "test", "--quiet"], cwd=str(root),
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
                                "output": "`cargo` не найден в PATH — верификация недоступна."}]}
        workdir = Path(workdir)
        stages = []
        build = subprocess.run(["cargo", "build", "--quiet"], cwd=str(workdir),
                              capture_output=True, text=True, timeout=180)
        stages.append({"name": "build", "ok": build.returncode == 0,
                       "output": build.stdout + build.stderr})
        if build.returncode != 0:
            return {"available": True, "stages": stages, "passed": False,
                    "confidence": "full", "regression": False}
        test = subprocess.run(["cargo", "test", "--quiet"], cwd=str(workdir),
                             capture_output=True, text=True, timeout=300)
        tests_ok = test.returncode == 0
        regression = bool(baseline and baseline.get("green")) and not tests_ok
        stages.append({"name": "tests", "ok": tests_ok,
                       "output": test.stdout + test.stderr})
        return {"available": True, "stages": stages,
                "passed": all(s["ok"] for s in stages),
                "confidence": "full", "regression": regression}
