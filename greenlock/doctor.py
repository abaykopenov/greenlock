"""greenlock.doctor — «что Greenlock сможет проверить в этом репозитории».

Онбординг-команда: детектит языки, верификатор-оракул, тулчейны, Docker и бэкенды
покрытия, и честно говорит ожидаемую силу гарантии (full / degraded / none).
"""
import shutil
import subprocess
from pathlib import Path

__all__ = ["diagnose", "format_report", "main"]

_CODE_EXTS = {
    ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript", ".go": "Go",
    ".rs": "Rust", ".java": "Java", ".rb": "Ruby", ".php": "PHP",
    ".c": "C", ".cpp": "C++", ".cs": "C#",
}
_SKIP_PARTS = {".git", "node_modules", ".venv", "venv", "__pycache__",
               ".groundqa_sandbox", "target", "dist", "build", "vendor"}


def _detected_languages(root: Path) -> dict[str, int]:
    found: dict[str, int] = {}
    for p in root.rglob("*"):
        if not p.is_file() or p.suffix not in _CODE_EXTS:
            continue
        if any(part in _SKIP_PARTS for part in p.parts):
            continue
        lang = _CODE_EXTS[p.suffix]
        found[lang] = found.get(lang, 0) + 1
    return found


def _pytest_available() -> bool:
    import sys
    for py in (sys.executable, "python3"):
        try:
            if subprocess.run([py, "-c", "import pytest"], capture_output=True).returncode == 0:
                return True
        except Exception:
            pass
    return False


def _coverage_status(lang: str) -> tuple[str, str]:
    """(status, detail) бэкенда покрытия для языка."""
    if lang == "Python":
        return ("ok", "stdlib trace") if _pytest_available() else ("bad", "нет pytest")
    if lang in ("JavaScript", "TypeScript"):
        return ("ok", "node V8") if shutil.which("node") else ("warn", "нет node → fail-open")
    if lang == "Go":
        return ("ok", "go -coverprofile") if shutil.which("go") else ("warn", "нет go → fail-open")
    if lang == "Rust":
        if shutil.which("cargo-llvm-cov"):
            return ("ok", "cargo-llvm-cov")
        return ("warn", "нет cargo-llvm-cov → покрытие fail-open")
    return ("warn", "покрытие не поддержано (oracle-only)")


def diagnose(repo: str) -> dict:
    """Список проверок: каждая — (status: ok|warn|bad, label, detail)."""
    root = Path(repo).resolve()
    checks: list[tuple[str, str, str]] = []
    if not root.is_dir():
        return {"repo": str(root), "checks": [("bad", "репозиторий", "путь не найден")]}

    checks.append(("ok", "репозиторий", str(root)))
    git = (root / ".git").is_dir()
    checks.append(("ok" if git else "warn", "git-репозиторий",
                   "есть" if git else "нет — `greenlock check` не сможет взять git diff"))

    langs = _detected_languages(root)
    checks.append(("ok" if langs else "bad", "языки",
                   ", ".join(f"{k}×{v}" for k, v in sorted(langs.items(), key=lambda x: -x[1]))
                   or "код не найден"))

    try:
        from greenlock.adapters import detect_verifier
        vname = type(detect_verifier(root)).__name__
        checks.append(("ok", "верификатор (оракул)", vname))
    except Exception as e:
        checks.append(("bad", "верификатор (оракул)", f"не определён: {e}"))

    try:
        from greenlock import isolate
        docker = isolate.docker_available()
        img = docker and subprocess.run(
            ["docker", "image", "inspect", isolate.DEFAULT_IMAGE],
            capture_output=True).returncode == 0
        if docker:
            checks.append(("ok" if img else "warn", "Docker (для --isolated)",
                           f"есть; образ {isolate.DEFAULT_IMAGE} "
                           + ("собран" if img else "НЕ собран (`docker build -t greenlock:latest .`)")))
        else:
            checks.append(("warn", "Docker (для --isolated)", "нет — изоляция недоступна"))
    except Exception:
        checks.append(("warn", "Docker (для --isolated)", "нет"))

    try:
        from greenlock.adapters.tree_sitter_adapter import HAS_TREE_SITTER
    except Exception:
        HAS_TREE_SITTER = False
    checks.append(("ok" if HAS_TREE_SITTER else "warn", "tree-sitter",
                   "есть (closed-world Go/Rust + точность покрытия)"
                   if HAS_TREE_SITTER else "нет — closed-world Go/Rust и точность покрытия снижены"))

    for lang in sorted(langs):
        if lang in ("Python", "JavaScript", "TypeScript", "Go", "Rust"):
            st, detail = _coverage_status(lang)
            checks.append((st, f"покрытие · {lang}", detail))

    return {"repo": str(root), "checks": checks}


def format_report(report: dict) -> str:
    mark = {"ok": "✓", "warn": "⚠", "bad": "✗"}
    lines = ["Greenlock doctor — что я смогу проверить здесь:\n"]
    for st, label, detail in report["checks"]:
        lines.append(f"  {mark.get(st, '?')} {label}: {detail}")
    statuses = [st for st, _, _ in report["checks"]]
    if "bad" in statuses:
        verdict = "✗ часть базовых условий не выполнена — гейт может деградировать/отказывать."
    elif "warn" in statuses:
        verdict = "⚠ основное работает; предупреждения выше — мягкая деградация (fail-open)."
    else:
        verdict = "✓ всё на месте — closed-world + оракул + честное покрытие (full confidence)."
    lines.append("\n" + verdict)
    return "\n".join(lines)


def main(repo: str = ".") -> int:
    print(format_report(diagnose(repo)))
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "."))
