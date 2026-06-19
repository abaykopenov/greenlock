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
        confidence = "full"
        if tests_ok and not regression:
            confidence, cov_msg = self._coverage_pass(workdir, changed)
            if cov_msg:
                stages[-1]["output"] += cov_msg
        return {"available": True, "stages": stages,
                "passed": all(s["ok"] for s in stages),
                "confidence": confidence, "regression": regression}

    def _coverage_pass(self, workdir, changed) -> tuple[str, str]:
        """WS-1 для Go: `go test -coverprofile` → confidence по покрытию изменённых .go.

        Fail-open: нет тулчейна/профиля/данных для файла → не блокируем зелёный патч.
        """
        changed_lines = getattr(self, "changed_lines", None)
        if not changed_lines:
            return "full", ""
        go = {rel: lns for rel, lns in changed_lines.items()
              if rel.endswith(".go") and (Path(workdir) / rel).exists()}
        if not go:
            return "full", ""
        prof = Path(workdir) / ".gl_go_cover.out"
        try:
            subprocess.run(["go", "test", f"-coverprofile={prof}", "./..."],
                           cwd=str(workdir), capture_output=True, text=True, timeout=300)
        except Exception:
            return "full", "\n[coverage] go coverprofile run failed — пропуск"
        if not prof.exists():
            return "full", "\n[coverage] go coverprofile not produced — пропуск"
        from greenlock.coverage import go_cover_executed_lines
        cov = go_cover_executed_lines(prof.read_text(encoding="utf-8"))
        try:
            prof.unlink()
        except OSError:
            pass
        uncovered = []
        for rel, lns in go.items():
            inner = "/".join(Path(rel).parts[1:]) or rel   # rel без префикса <reponame>
            ex = next((lines for name, lines in cov.items()
                       if name == rel or name == inner or name.endswith("/" + inner)), None)
            if ex is None:
                continue            # нет данных для файла → fail-open
            if not (set(lns) & ex):
                uncovered.append(rel)
        if uncovered:
            return "degraded", ("\nChanged code is NOT exercised by the test suite "
                                f"(confidence=degraded): {', '.join(sorted(uncovered))}")
        return "full", ""
