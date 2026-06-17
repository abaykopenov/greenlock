"""adapters — интерфейсы языковых адаптеров и верификаторов.

LanguageAdapter: разбор исходного кода → символы + спаны + импорты + ссылки.
ProjectVerifier: оракул проекта (syntax → types → lint → tests).

Ядро (closed_world, code_writer, gate) знает только эти два контракта — новый язык
или тулчейн добавляется новой реализацией, без правок ядра.
"""
from dataclasses import dataclass, field
from typing import Protocol

__all__ = [
    "ParseResult", "LanguageAdapter", "ProjectVerifier",
    "detect_adapters", "detect_verifier",
]


@dataclass
class ParseResult:
    """Результат parse() адаптера.

    symbols: список словарей {name, kind, file, line, span_start, span_end}.
             span_start/span_end — границы тела (строки, 1-based); None если
             адаптер не умеет (regex-fallback).
    imports: список словарей {module, names, file, line}.
    refs:    список словарей {name, file, line} — вызовы / ссылки.
    defined: список строк — все имена, определённые внутри файла (переменные, аргументы и т.д.).
    """
    symbols: list[dict] = field(default_factory=list)
    imports: list[dict] = field(default_factory=list)
    refs: list[dict] = field(default_factory=list)
    defined: list[str] = field(default_factory=list)


class LanguageAdapter(Protocol):
    """Адаптер для конкретного языка/семейства.

    Ядро знает только этот контракт. Новый язык = новая реализация,
    ядро не трогается.
    """
    name: str
    extensions: set[str]  # {".py"}, {".go"}, ...

    def parse(self, rel: str, text: str) -> ParseResult:
        """Разобрать файл: символы (с границами спанов) + импорты + refs/calls."""
        ...


class ProjectVerifier(Protocol):
    """Верификатор проекта — оракул (detect → capture_baseline → verify)."""

    def detect(self, root) -> bool:
        """Есть ли манифест/тулинг в проекте."""
        ...

    def verify(self, workdir, changed: list[str], baseline: dict | None = None) -> dict:
        """Прогнать стадии: syntax → types → lint → tests."""
        ...

    def capture_baseline(self, workdir) -> dict:
        """Собрать базовую линию прохождения тестов."""
        ...


def detect_adapters() -> list:
    """Вернуть список доступных адаптеров."""
    from greenlock.adapters.python_adapter import PythonAdapter
    from greenlock.adapters.node_adapter import NodeAdapter
    
    adapters = [PythonAdapter(), NodeAdapter()]
    try:
        from greenlock.adapters.tree_sitter_adapter import TreeSitterAdapter
        adapters.append(TreeSitterAdapter())
    except ImportError:
        pass
        
    from greenlock.adapters.regex_adapter import RegexAdapter
    adapters.append(RegexAdapter())
    return adapters


def detect_verifier(root) -> ProjectVerifier:
    """Определить верификатор проекта. Приоритет — по greenlock.json,
    затем по МАНИФЕСТУ (сильный сигнал), затем фолбэк по наличию файлов."""
    from pathlib import Path
    from greenlock.adapters.pytest_verifier import PytestVerifier
    from greenlock.adapters.node_verifier import NodeVerifier
    from greenlock.adapters.go_verifier import GoVerifier
    from greenlock.adapters.rust_verifier import RustVerifier

    path = Path(root)

    # 0. По greenlock.json (явная конфигурация)
    for cfg_name in ("greenlock.json", "greenlock.config.json"):
        cfg_path = path / cfg_name
        if cfg_path.exists():
            try:
                import json
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                verifier_type = cfg.get("verifier")
                v = None
                if verifier_type == "pytest":
                    v = PytestVerifier()
                elif verifier_type == "node":
                    v = NodeVerifier()
                elif verifier_type == "go":
                    v = GoVerifier()
                elif verifier_type == "rust":
                    v = RustVerifier()
                elif verifier_type == "custom":
                    from greenlock.adapters.custom_verifier import CustomVerifier
                    v = CustomVerifier(cfg)
                
                if v:
                    if "test_command" in cfg:
                        v.test_command = cfg["test_command"]
                    if "syntax_command" in cfg:
                        v.syntax_command = cfg["syntax_command"]
                    return v
            except Exception:
                pass

    # 1. По манифесту
    if (path / "go.mod").exists():
        return GoVerifier()
    if (path / "Cargo.toml").exists():
        return RustVerifier()
    if (path / "package.json").exists():
        return NodeVerifier()
    if any((path / m).exists() for m in
           ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt")):
        return PytestVerifier()

    # 2. Фолбэк по файлам языка (для репо без манифеста, как наши бенч-либы)
    for V in (NodeVerifier, PytestVerifier):
        v = V()
        if v.detect(path):
            return v

    # Заглушка, если верификатор не найден
    class DummyVerifier:
        def detect(self, root) -> bool:
            return False

        def verify(self, workdir, changed: list[str], baseline: dict | None = None) -> dict:
            return {
                "available": False,
                "stages": [{"name": "tests", "ok": False, "output": "No verifiers detected."}],
                "passed": False,
                "confidence": "none"
            }

        def capture_baseline(self, workdir) -> dict:
            return {"passed": set(), "failed": set(), "errors": set()}

    return DummyVerifier()
