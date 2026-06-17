"""core.dep_closure — вычисление графа зависимостей и извлечение сигнатур контрактов.

Использует ParseResult адаптеров для поиска импортируемых файлов и вызываемых символов.
"""
from pathlib import Path
from greenlock.adapters import detect_adapters

__all__ = ["get_dependency_closure", "get_dependency_signatures"]


def get_dependency_closure(index: dict, target_file: str, max_depth: int = 2) -> set[str]:
    """Вычислить транзитивное замыкание зависимостей для целевого файла.

    Возвращает множество относительных путей файлов из индекса.
    """
    closure = set()
    to_visit = [(target_file, 0)]
    visited = set()

    adapters = detect_adapters()
    ext_to_adapter = {}
    for a in adapters:
        for ext in a.extensions:
            if ext not in ext_to_adapter:
                ext_to_adapter[ext] = a

    while to_visit:
        current, depth = to_visit.pop(0)
        if current in visited:
            continue
        visited.add(current)

        if current != target_file:
            closure.add(current)

        if depth >= max_depth:
            continue

        content = index["files"].get(current)
        if not content:
            continue

        suffix = Path(current).suffix
        adapter = ext_to_adapter.get(suffix)
        if not adapter or adapter.name == "regex-fallback":
            continue

        try:
            res = adapter.parse(current, content)
        except Exception:
            continue

        # 1. Разрешение импортов
        for imp in res.imports:
            module = imp.get("module")
            if not module:
                continue

            # Разрешение имени модуля в файлы из индекса через Suffix-Slice Matching
            mod_parts = [p for p in module.split(".") if p]
            clean_parts = []
            for part in mod_parts:
                for sub in part.split("/"):
                    if sub and sub not in (".", ".."):
                        clean_parts.append(sub)

            # Кандидаты по суффиксам
            candidates = []
            for start_idx in range(len(clean_parts)):
                sub_path = "/".join(clean_parts[start_idx:])
                for ext in ext_to_adapter.keys():
                    candidates.append(sub_path + ext)
                    candidates.append(f"{sub_path}/index{ext}")

            # Относительное разрешение для JS/Node импортов вида ./pricing
            if module.startswith("."):
                try:
                    resolved_mod = Path(current).parent / module
                    rel_resolved = resolved_mod.as_posix()
                    rel_resolved = rel_resolved.replace("/./", "/").replace("/../", "/")
                    clean_rel = [p for p in rel_resolved.split("/") if p and p not in (".", "..")]
                    for start_idx in range(len(clean_rel)):
                        sub_path = "/".join(clean_rel[start_idx:])
                        for ext in ext_to_adapter.keys():
                            candidates.append(sub_path + ext)
                            candidates.append(f"{sub_path}/index{ext}")
                except Exception:
                    pass

            # Ищем, есть ли кандидат в файлах индекса
            matched_file = None
            for cand in candidates:
                cand_clean = cand.lstrip("/")
                if cand_clean in index["files"]:
                    matched_file = cand_clean
                    break

            if matched_file:
                to_visit.append((matched_file, depth + 1))

        # 2. Разрешение references
        for ref in res.refs:
            name = ref["name"]
            defining_locations = index.get("symbols", {}).get(name, [])
            for defining_file, line in defining_locations:
                if defining_file in index["files"]:
                    to_visit.append((defining_file, depth + 1))

    return closure


def get_dependency_signatures(index: dict, filepath: str) -> str:
    """Извлечь сигнатуры символов из файла зависимости."""
    content = index["files"].get(filepath, "")
    if not content:
        return ""

    suffix = Path(filepath).suffix
    adapter = None
    for a in detect_adapters():
        if suffix in a.extensions:
            adapter = a
            break
    if not adapter or adapter.name == "regex-fallback":
        return ""

    try:
        res = adapter.parse(filepath, content)
    except Exception:
        return ""

    lines = content.splitlines()
    items = []
    for s in res.symbols:
        start = s.get("span_start")
        if start is not None and 1 <= start <= len(lines):
            sig = lines[start - 1].strip()
            if sig.endswith("{"):
                sig = sig[:-1].strip()
            if sig.endswith(":"):
                sig = sig[:-1].strip()
            items.append(f"    {sig} ({s['kind']})")
    return "\n".join(items) if items else "    (нет доступных символов)"
