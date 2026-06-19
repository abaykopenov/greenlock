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
        confidence = "full"
        if tests_ok and not regression:
            confidence, cov_msg = self._coverage_pass(workdir, changed)
            if cov_msg:
                stages[-1]["output"] += cov_msg
        return {"available": True, "stages": stages,
                "passed": all(s["ok"] for s in stages),
                "confidence": confidence, "regression": regression}

    def _coverage_pass(self, workdir, changed) -> tuple[str, str]:
        """WS-1 для Rust: `cargo-llvm-cov --lcov` → confidence по покрытию изменённых .rs.

        У cargo нет встроенного line-coverage, поэтому нужен внешний cargo-llvm-cov.
        Fail-open: нет инструмента/данных → не блокируем зелёный патч.
        """
        import os
        changed_lines = getattr(self, "changed_lines", None)
        if not changed_lines:
            return "full", ""
        rs = {rel: lns for rel, lns in changed_lines.items()
              if rel.endswith(".rs") and (Path(workdir) / rel).exists()}
        if not rs:
            return "full", ""
        if shutil.which("cargo-llvm-cov") is None:
            return "full", "\n[coverage] cargo-llvm-cov не найден — покрытие не измерено (пропуск)"
        lcov = Path(workdir) / ".gl_rust_cov.lcov"
        try:
            subprocess.run(["cargo", "llvm-cov", "--quiet", "--lcov",
                            "--output-path", str(lcov)],
                           cwd=str(workdir), capture_output=True, text=True, timeout=600)
        except Exception:
            return "full", "\n[coverage] cargo-llvm-cov run failed — пропуск"
        if not lcov.exists():
            return "full", "\n[coverage] lcov не получен — пропуск"
        from greenlock.coverage import lcov_executed_lines, code_changed_lines
        cov = lcov_executed_lines(lcov.read_text(encoding="utf-8"))
        try:
            lcov.unlink()
        except OSError:
            pass
        uncovered = []
        for rel, lns in rs.items():
            target = os.path.realpath(str((Path(workdir) / rel).resolve()))
            ex = next((lines for f, lines in cov.items()
                       if os.path.realpath(f) == target or f.endswith(rel)), None)
            if ex is None:
                continue            # нет данных для файла → fail-open
            try:
                src = (Path(workdir) / rel).read_text(encoding="utf-8")
            except OSError:
                continue
            code = code_changed_lines(src, ".rs", set(lns))
            if not code:
                continue            # комментарии/use/сигнатуры → покрытие не требуется
            if not (code & ex):
                uncovered.append(rel)
        if uncovered:
            return "degraded", ("\nChanged code is NOT exercised by the test suite "
                                f"(confidence=degraded): {', '.join(sorted(uncovered))}")
        return "full", ""
