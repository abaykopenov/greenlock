"""greenlock.danger — детектор опасных/обфусцирующих конструкций, ВНЕСЁННЫХ патчем.

Closed-world и тест-оракул НЕ защищают от злонамеренного автора: код можно спрятать
в eval/exec, выполнить команды через os.system/subprocess или сделать «двуличным»
(вести себя правильно только под тестом — defeat device). Этот модуль ищет такие
конструкции в Python-AST и флажит ТОЛЬКО те, что патч ДОБАВИЛ (то, что уже было в
файле, не трогаем — иначе ругались бы на легитимный код проекта).

ВАЖНО: это НЕ песочница. Полная защита от RCE — изоляция исполнения (Docker/VM),
см. SECURITY.md. Здесь — дешёвый AST-фильтр, поднимающий планку и отсекающий
показанные эксплойты ДО запуска оракула.

MVP: Python (.py). Для прочих языков — пусто (пропуск).
"""
import ast
from pathlib import Path

__all__ = ["scan_introduced", "danger_tags"]

# Прямые вызовы code-exec / обфускации (bare Name).
_EXEC_CALLS = {"eval", "exec", "compile", "__import__"}
# Строки-сигналы детекции тест-окружения (двуличный код).
_TEST_ENV_STRINGS = {"PYTEST_CURRENT_TEST"}
# Имена модулей тест-фреймворков (проверки вида 'pytest' in sys.modules).
_TEST_FRAMEWORKS = {"pytest", "unittest", "_pytest", "nose"}


def danger_tags(source: str) -> set[str]:
    """Множество тегов опасных конструкций в Python-исходнике (по имени, не по строке —
    устойчиво к сдвигам)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()

    tags: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                if f.id in _EXEC_CALLS:
                    tags.add(f.id)
                if f.id in ("getattr", "setattr", "delattr") and len(node.args) >= 2 \
                        and not isinstance(node.args[1], ast.Constant):
                    tags.add(f"dynamic-{f.id}")
            elif isinstance(f, ast.Attribute):
                base = f.value.id if isinstance(f.value, ast.Name) else None
                attr = f.attr
                if base == "os" and (attr.startswith("exec")
                                     or attr in ("system", "popen", "spawnl", "spawnv", "spawnlp")):
                    tags.add(f"os.{attr}")
                elif base == "subprocess":
                    tags.add(f"subprocess.{attr}")
                elif (base, attr) in (("importlib", "import_module"),
                                      ("pickle", "loads"), ("marshal", "loads")):
                    tags.add(f"{base}.{attr}")
        # детекция тест-окружения по строковому литералу
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in _TEST_ENV_STRINGS:
                tags.add(f"test-env:{node.value}")
            elif node.value in _TEST_FRAMEWORKS:
                # 'pytest' as a string обычно фигурирует в проверках окружения
                tags.add(f"test-framework-ref:{node.value}")
        # sys.modules — проверка «бежим ли мы под тест-раннером»
        elif isinstance(node, ast.Attribute) and node.attr == "modules" \
                and isinstance(node.value, ast.Name) and node.value.id == "sys":
            tags.add("sys.modules-check")
    return tags


def scan_introduced(patched_source: str, baseline_source: str | None = None) -> list[str]:
    """Опасные конструкции, которых НЕ было в baseline, но есть в патче.

    Если baseline_source=None (новый файл) — флажится всё опасное в патче.
    """
    new = danger_tags(patched_source)
    old = danger_tags(baseline_source or "")
    return sorted(new - old)


def scan_file(patched_path: Path, baseline_source: str | None = None) -> list[str]:
    """Удобная обёртка: прочитать .py-файл и вернуть внесённые опасные конструкции."""
    if patched_path.suffix != ".py" or not patched_path.exists():
        return []
    try:
        src = patched_path.read_text(encoding="utf-8")
    except Exception:
        return []
    return scan_introduced(src, baseline_source)
