"""core.closed_world — префилтер-валидатор closed-world вызовов.

Гарантирует, что измененный код ссылается только на известные / импортированные
имена и не выдумывает несуществующие функции или переменные.
"""
import builtins
from pathlib import Path

from greenlock.adapters import detect_adapters

__all__ = ["closed_world_check"]

# Множество имен из builtins
BUILTIN_NAMES = set(dir(builtins))


def closed_world_check(filepath: Path, global_symbols: dict) -> list[str]:
    """Проверить, что все Name-вызовы в файле разрешимы.

    Возвращает список сообщений об ошибках (пустой список = всё хорошо).
    """
    if not filepath.exists():
        return [f"File does not exist: {filepath}"]

    # Находим подходящий адаптер по расширению
    adapter = None
    for a in detect_adapters():
        if filepath.suffix in a.extensions:
            adapter = a
            break

    if not adapter:
        # Если адаптера нет, closed-world проверку пропустить (или выдать предупреждение)
        return []

    try:
        content = filepath.read_text(encoding="utf-8")
        res = adapter.parse(str(filepath), content)
    except Exception as e:
        return [f"Failed to read/parse file during closed-world analysis: {e}"]

    # Собираем импортированные имена
    imported_names = set()
    for imp in res.imports:
        for name in imp.get("names", []):
            imported_names.add(name)
            if "." in name:
                imported_names.add(name.split(".")[0])
        if imp.get("module"):
            imported_names.add(imp["module"].split(".")[0])

    # Специфичные для конкретных языков специальные имена
    special_names = {
        "__name__", "__file__", "__doc__", "__package__", "self", "cls", 
        "arguments", "module", "exports", "require", "process", "console"
    }

    # builtins/глобалы — по языку (адаптер отдаёт свой набор; иначе питоновские).
    lang_builtins = getattr(adapter, "builtins", BUILTIN_NAMES)

    # Имена, разрешимые локально
    resolved_locally = (
        set(res.defined)
        | imported_names
        | set(lang_builtins)
        | special_names
    )
    # Также добавим все имена символов из этого же файла
    for sym in res.symbols:
        resolved_locally.add(sym["name"])

    errors = []
    # Для каждого используемого имени проверяем разрешимость
    used_names = {ref["name"] for ref in res.refs}
    for name in sorted(used_names):
        # Если имя не разрешено локально и его нет в глобальном индексе символов
        if name not in resolved_locally and name not in global_symbols:
            errors.append(
                f"Undeclared name '{name}': not defined locally, not imported, "
                f"and not found in project symbols."
            )

    return errors
